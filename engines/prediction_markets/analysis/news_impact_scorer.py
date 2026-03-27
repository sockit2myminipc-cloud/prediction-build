from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time

import requests
from dotenv import load_dotenv
from loguru import logger
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from engines.prediction_markets.filters.market_filter import MarketFilter
from engines.prediction_markets.storage.models import NewsArticle, NewsMarketLink

load_dotenv()


def ensure_ollama_running() -> bool:
    """Start Ollama if not running; verify configured model exists.

    Returns True if Ollama is available, False if we must fall back to regex.
    """
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

    def _ping() -> bool:
        try:
            r = requests.get(f"{base}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    if _ping():
        logger.info("Ollama already running at {}", base)
    else:
        logger.info("Ollama not running — attempting to start with 'ollama serve'")
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning("Ollama not found — using regex fallback for entity extraction")
            return False
        except Exception as e:
            logger.warning("Failed to start Ollama: {} — using regex fallback", e)
            return False

        # Wait up to 10 seconds for Ollama to become responsive
        for i in range(10):
            time.sleep(1)
            if _ping():
                logger.info("Ollama started automatically (took {}s)", i + 1)
                break
        else:
            logger.warning("Ollama did not become ready in 10s — using regex fallback")
            return False

    # Verify the configured model is actually available
    try:
        r = requests.get(f"{base}/api/tags", timeout=5)
        available = [m["name"] for m in r.json().get("models", [])]
        if any(model == a or a.startswith(model.split(":")[0]) for a in available):
            logger.info("Ollama model '{}' confirmed available", model)
        else:
            logger.warning(
                "Configured model '{}' not found in Ollama — available: {}",
                model,
                ", ".join(available) or "(none)",
            )
    except Exception as e:
        logger.debug("Could not verify Ollama model availability: {}", e)

    return True


class NewsImpactScorer:
    def __init__(self, session: Session):
        self.session = session
        self.ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

    def _ollama_chat(self, system: str, user: str) -> str | None:
        try:
            r = requests.post(
                f"{self.ollama_base}/api/chat",
                json={
                    "model": self.ollama_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                },
                timeout=120,
            )
            r.raise_for_status()
            data = r.json()
            msg = (data.get("message") or {}).get("content")
            return msg.strip() if msg else None
        except Exception as e:
            logger.debug("Ollama unavailable: {}", e)
            return None

    @staticmethod
    def _parse_ollama_json(raw: str) -> dict | None:
        raw = raw.strip()
        if not raw:
            return None

        # Common case: fenced markdown JSON from model responses.
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
        candidates = [fence.group(1).strip()] if fence else []
        candidates.append(raw)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

            # Next best: pull first JSON object from mixed prose.
            m = re.search(r"\{[\s\S]*\}", candidate)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
        return None

    def extract_entities(self, article_text: str, headline: str) -> dict:
        text = article_text or headline
        sys = (
            "You are a prediction market analyst. From the article extract ONLY concrete, "
            "specific entities that would affect prediction market outcomes. Respond in JSON only."
        )
        user = (
            "Extract from this article: teams, players, politicians, countries, companies, "
            "dates (month + year), specific events (e.g. Fed rate decision March), "
            "numeric outcomes (e.g. BTC $100k, unemployment 4.2%).\n\n"
            f"Article: {text[:3000]}"
        )
        raw = self._ollama_chat(sys, user)
        if raw:
            parsed = self._parse_ollama_json(raw)
            if parsed is not None:
                return parsed
            logger.debug("Ollama entity JSON parse failed; falling back to keywords")
        return _keyword_entities(text, headline)

    def match_markets(self, entities: dict, all_markets: list[dict]) -> list[dict]:
        entity_strings = _flatten_entities(entities)
        out: list[dict] = []
        for m in all_markets:
            q = m.get("question", "")
            if not q:
                continue
            best = 0
            for es in entity_strings:
                sc = fuzz.token_set_ratio(es.lower(), q.lower())
                best = max(best, sc)
            theme = m.get("theme") or MarketFilter.assign_theme(q)
            bonus = _theme_entity_bonus(theme, entities)
            score = min(1.0, (best / 100.0) * 0.85 + bonus)
            if best >= 60 and score > 0.4:
                out.append({"market": m, "relevance_score": score})
        out.sort(key=lambda x: x["relevance_score"], reverse=True)
        return out

    def detect_news_lag(
        self,
        article: NewsArticle,
        matched_markets: list[dict],
        readings_loader,
    ) -> list[dict]:
        alerts = []
        pub = article.published_at or article.fetched_at
        now = dt.datetime.now(dt.UTC)
        if pub and pub.tzinfo is None:
            pub = pub.replace(tzinfo=dt.UTC)
        if pub and (now - pub).total_seconds() > 45 * 60:
            return []

        # Extract article primitives here so the alert dict never holds an ORM object.
        # This prevents DetachedInstanceError if the session closes before the alert
        # is consumed by news_lag_to_opportunities().
        article_snapshot = {
            "id": article.id,
            "headline": article.headline,
            "published_at": pub,
            "fetched_at": article.fetched_at,
        }

        for item in matched_markets:
            m = item["market"]
            mid = m.get("_db_id") or m.get("id")
            if mid is None:
                continue
            series = readings_loader(int(mid))
            if len(series) < 2:
                continue
            latest = series[-1].probability
            past = series[0].probability
            delta_30 = abs(latest - past)
            if delta_30 < 0.02:
                direction = self.expected_direction(article.headline, m.get("question", ""))
                alerts.append(
                    {
                        "type": "news_lag",
                        "market": m,
                        "article": article_snapshot,
                        "relevance_score": item["relevance_score"],
                        "current_probability": latest,
                        "expected_direction": direction,
                    }
                )
        alerts.sort(key=lambda x: x["relevance_score"], reverse=True)
        return alerts

    def expected_direction(self, headline: str, market_question: str) -> str:
        sys = "Reply with exactly one word: YES, NO, or UNKNOWN."
        user = (
            "Given this headline, does it make this market outcome more likely (YES) or less likely (NO)?\n"
            f"Headline: {headline}.\nMarket: {market_question}.\nReply: YES, NO, or UNKNOWN only."
        )
        raw = self._ollama_chat(sys, user)
        if not raw:
            return "unknown"
        t = raw.upper().strip().split()[0] if raw else "UNKNOWN"
        if "YES" in t:
            return "YES"
        if "NO" in t:
            return "NO"
        return "unknown"

    def process_article(
        self,
        article_id: int,
        all_markets: list[dict],
        readings_loader,
    ) -> tuple[dict, list[dict], list[dict]]:
        article = self.session.get(NewsArticle, article_id)
        if not article:
            return {}, [], []
        text = (article.body_text or "") + "\n" + article.headline
        entities = self.extract_entities(text, article.headline)
        article.entities_json = entities
        matches = self.match_markets(entities, all_markets)
        for item in matches:
            m = item["market"]
            dbid = m.get("_db_id")
            if dbid is None:
                continue
            link = NewsMarketLink(
                article_id=article.id,
                market_id=int(dbid),
                relevance_score=float(item["relevance_score"]),
            )
            self.session.add(link)
        with self.session.no_autoflush:
            alerts = self.detect_news_lag(article, matches, readings_loader)
        return entities, matches, alerts


def _flatten_entities(entities: dict) -> list[str]:
    parts: list[str] = []
    for k, v in entities.items():
        if isinstance(v, list):
            parts.extend(str(x) for x in v if x)
        elif v:
            parts.append(str(v))
    return [p for p in parts if len(p) > 2]


def _keyword_entities(text: str, headline: str) -> dict:
    blob = (headline + " " + text).lower()
    return {
        "teams": [],
        "players": [],
        "politicians": [],
        "organizations": [],
        "events": [w for w in ["fomc", "fed", "cpi", "election", "bitcoin", "btc", "nfl", "nba"] if w in blob],
        "numeric_targets": re.findall(r"\$\s*\d[\d,]*k?", blob),
        "dates": [],
        "tickers": re.findall(r"\b[A-Z]{2,5}\b", headline),
    }


def _theme_entity_bonus(theme: str, entities: dict) -> float:
    ev = entities.get("events") or []
    if theme.startswith("sports") and any("nfl" in str(x).lower() or "nba" in str(x).lower() for x in ev):
        return 0.15
    if theme in ("fed_macro", "cpi_macro") and any("fomc" in str(x).lower() or "cpi" in str(x).lower() for x in ev):
        return 0.15
    return 0.0
