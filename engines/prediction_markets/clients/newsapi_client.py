from __future__ import annotations

import datetime as dt
import os
import time

import feedparser
import requests
from dotenv import load_dotenv
from loguru import logger
from newsapi import NewsApiClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engines.prediction_markets.runtime_state import set_newsapi_status
from engines.prediction_markets.storage.models import NewsArticle

load_dotenv()

# Batch queries into 2 NewsAPI calls instead of 5 (reduces usage 60%)
NEWS_QUERY_BATCHES = [
    "federal reserve interest rate OR bitcoin crypto market OR election results poll",
    "sports championship winner OR recession inflation GDP",
]

_CACHE_TTL_SEC = 6 * 3600  # 6 hours — don't re-fetch same batch within this window
_MAX_DAILY_CALLS = 80       # leave 20-call buffer on the 100/day dev key
_RATE_LIMIT_COOLDOWN_SEC = 12 * 3600  # dev keys replenish every 12h window
_NEWSAPI_CACHE: dict[str, tuple[float, list[dict]]] = {}  # query → (fetch_time, articles)
_DAILY_CALL_COUNT: dict[str, int] = {}  # "YYYY-MM-DD" → calls made
_RATE_LIMIT_UNTIL_TS: float = 0.0

RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://www.espn.com/espn/rss/news",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
]

REDDIT_URLS = [
    "https://www.reddit.com/r/politics/top.json?limit=10&t=hour",
    "https://www.reddit.com/r/CryptoCurrency/top.json?limit=10&t=hour",
    "https://www.reddit.com/r/nfl/top.json?limit=10&t=hour",
    "https://www.reddit.com/r/nba/top.json?limit=10&t=hour",
]


class NewsIngester:
    def __init__(self, session: Session):
        self.session = session
        key = os.environ.get("NEWSAPI_KEY", "")
        self._newsapi = NewsApiClient(api_key=key) if key else None

    def poll_newsapi(self) -> list[dict]:
        global _RATE_LIMIT_UNTIL_TS
        if not self._newsapi:
            logger.debug("NEWSAPI_KEY not set; skipping NewsAPI")
            return []

        now_ts = time.time()
        if now_ts < _RATE_LIMIT_UNTIL_TS:
            cooldown_left = int(_RATE_LIMIT_UNTIL_TS - now_ts)
            logger.warning(
                "NewsAPI in cooldown after rate limit ({}s remaining) — skipping",
                cooldown_left,
            )
            today = dt.date.today().isoformat()
            set_newsapi_status(_DAILY_CALL_COUNT.get(today, 0), dt.datetime.now(dt.UTC))
            return []

        today = dt.date.today().isoformat()
        calls_today = _DAILY_CALL_COUNT.get(today, 0)
        if calls_today >= _MAX_DAILY_CALLS:
            logger.warning("NewsAPI daily limit reached ({}/{}) — skipping until tomorrow", calls_today, _MAX_DAILY_CALLS)
            set_newsapi_status(calls_today, dt.datetime.now(dt.UTC))
            return []

        out: list[dict] = []
        for q in NEWS_QUERY_BATCHES:
            now_ts = time.time()
            cached_ts, cached_articles = _NEWSAPI_CACHE.get(q, (0.0, []))
            if now_ts - cached_ts < _CACHE_TTL_SEC:
                logger.debug("NewsAPI cache hit for batch (age {:.0f}s)", now_ts - cached_ts)
                out.extend(cached_articles)
                continue

            if _DAILY_CALL_COUNT.get(today, 0) >= _MAX_DAILY_CALLS:
                logger.warning("NewsAPI daily limit reached mid-poll — stopping")
                break

            try:
                resp = self._newsapi.get_everything(q=q, sort_by="publishedAt", page_size=20, language="en")
                _DAILY_CALL_COUNT[today] = _DAILY_CALL_COUNT.get(today, 0) + 1
                articles = [
                    {
                        "source": (a.get("source") or {}).get("name") or "newsapi",
                        "title": a.get("title") or "",
                        "description": a.get("description") or "",
                        "url": a.get("url") or "",
                        "publishedAt": a.get("publishedAt"),
                        "content": a.get("content") or "",
                    }
                    for a in (resp.get("articles") or [])
                ]
                _NEWSAPI_CACHE[q] = (time.time(), articles)
                out.extend(articles)
                logger.debug("NewsAPI fetched {} articles for batch (daily calls: {}/{})",
                             len(articles), _DAILY_CALL_COUNT[today], _MAX_DAILY_CALLS)
                set_newsapi_status(_DAILY_CALL_COUNT[today], dt.datetime.now(dt.UTC))
            except Exception as e:
                logger.warning("NewsAPI query batch failed: {}", e)
                if _is_rate_limit_error(e):
                    _RATE_LIMIT_UNTIL_TS = time.time() + _RATE_LIMIT_COOLDOWN_SEC
                    logger.warning(
                        "NewsAPI reported rate-limited. Entering cooldown for {} hours.",
                        _RATE_LIMIT_COOLDOWN_SEC // 3600,
                    )
                    break
        return out

    def poll_rss(self) -> list[dict]:
        out: list[dict] = []
        for url in RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:25]:
                    out.append(
                        {
                            "source": feed.feed.get("title", url)[:120],
                            "title": e.get("title", ""),
                            "description": e.get("summary", "") or e.get("description", ""),
                            "url": e.get("link", ""),
                            "publishedAt": e.get("published") or e.get("updated"),
                            "content": "",
                        }
                    )
            except Exception as ex:
                logger.warning("RSS {!r} failed: {}", url, ex)
        return out

    def poll_reddit(self) -> list[dict]:
        out: list[dict] = []
        headers = {"User-Agent": "PredictionMarketEngine/1.0"}
        for url in REDDIT_URLS:
            try:
                r = requests.get(url, headers=headers, timeout=25)
                r.raise_for_status()
                data = r.json()
                for child in data.get("data", {}).get("children") or []:
                    p = child.get("data") or {}
                    out.append(
                        {
                            "source": "reddit/" + (p.get("subreddit") or "unknown"),
                            "title": p.get("title", ""),
                            "description": p.get("selftext", "")[:2000],
                            "url": "https://reddit.com" + p.get("permalink", ""),
                            "publishedAt": None,
                            "content": p.get("selftext", "")[:5000],
                        }
                    )
            except Exception as e:
                logger.warning("Reddit fetch failed {}: {}", url, e)
        return out

    def store_article(
        self, headline: str, body: str, source: str, url: str, published_at: dt.datetime | None
    ) -> NewsArticle | None:
        if not url:
            return None
        url_key = url[:2048]
        existing = self.session.scalars(select(NewsArticle).where(NewsArticle.url == url_key).limit(1)).first()
        if existing:
            return None
        row = NewsArticle(
            source=source[:128],
            headline=headline[:10000],
            body_text=body[:50000] if body else None,
            url=url_key,
            published_at=published_at,
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            # Duplicate URL inserted concurrently or already present; treat as existing.
            logger.debug("Skipping duplicate article URL: {}", url_key[:120])
            return None
        return row

    def run_cycle(self) -> tuple[int, list[int]]:
        """Fetch all sources and store new articles. Returns (new count, new article ids)."""
        batch: list[dict] = []
        batch.extend(self.poll_newsapi())
        batch.extend(self.poll_rss())
        batch.extend(self.poll_reddit())
        n = 0
        ids: list[int] = []
        for a in batch:
            pub = _parse_pub(a.get("publishedAt"))
            body = (a.get("description") or "") + "\n" + (a.get("content") or "")
            saved = self.store_article(a.get("title") or "", body, a.get("source") or "", a.get("url") or "", pub)
            if saved:
                n += 1
                ids.append(saved.id)
        if n:
            self.session.commit()
        return n, ids


def _parse_pub(v: object) -> dt.datetime | None:
    if not v:
        return None
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.UTC)
    if isinstance(v, str):
        try:
            return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _is_rate_limit_error(err: Exception) -> bool:
    msg = str(err).lower()
    return "ratelimited" in msg or "too many requests" in msg or "429" in msg
