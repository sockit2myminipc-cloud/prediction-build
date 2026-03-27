from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import requests
from loguru import logger

GAMMA_BASE = "https://gamma-api.polymarket.com"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_PATH = PROJECT_ROOT / "data" / "polymarket_cache.json"
CACHE_MAX_AGE_SEC = 4 * 3600
REQUEST_GAP = 0.05

# All known Polymarket category tag slugs
POLYMARKET_CATEGORIES = [
    "politics", "crypto", "sports", "science", "pop-culture",
    "business", "economics", "weather", "tech", "health",
    "world", "gaming", "entertainment", "news",
]


class PolymarketClient:
    def __init__(self, cache_path: Path | None = None):
        self.cache_path = cache_path or CACHE_PATH
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_cache(self, markets: list[dict]) -> None:
        payload = {"fetched_at": datetime.now(UTC).isoformat(), "markets": markets}
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")

    def _read_cache(self) -> tuple[list[dict], float | None]:
        if not self.cache_path.exists():
            return [], None
        age = time.time() - self.cache_path.stat().st_mtime
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return [], age
        return data.get("markets") or [], age

    def _fetch_page(self, params: dict) -> list[dict]:
        url = f"{GAMMA_BASE}/markets"
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            logger.warning("Polymarket page fetch failed: {}", e)
            return []
        if isinstance(batch, dict) and "data" in batch:
            batch = batch["data"]
        if not isinstance(batch, list):
            return []
        return batch

    def get_markets_by_category(self, category_slug: str, max_pages: int = 20) -> list[dict]:
        """Fetch all active markets for one category slug, paginating fully."""
        rows: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            params = {"active": "true", "tag_slug": category_slug, "limit": "500", "offset": str(offset)}
            batch = self._fetch_page(params)
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < 500:
                break
            offset += len(batch)
            if offset > 10000:
                break
            time.sleep(REQUEST_GAP)
        return rows

    @staticmethod
    def _passes_prefilter(m: dict) -> bool:
        """Broad pre-filter: only store markets with liquidity >= 1000 OR volume_24h >= 1000."""
        liq = float(m.get("liquidity") or 0)
        # Polymarket raw API uses volume24hr or volumeClob for 24h volume
        vol = float(m.get("volume24hr") or m.get("volumeClob") or m.get("volume") or 0)
        return liq >= 1000 or vol >= 1000

    def get_all_active_markets(self, limit: int = 500) -> list[dict]:
        """
        Fetch ALL active markets across ALL categories with full pagination.
        Deduplicates by market id and logs per-category counts.
        Pre-filters to markets with liquidity >= 1000 OR volume_24h >= 1000 to avoid DB bloat.
        """
        seen: dict[str, dict] = {}
        category_counts: dict[str, int] = {}
        prefilter_dropped = 0

        # Phase 1: per-category sweep so every category is represented
        for cat in POLYMARKET_CATEGORIES:
            cat_markets = self.get_markets_by_category(cat, max_pages=1)
            new_count = 0
            for m in cat_markets:
                mid = str(m.get("id", ""))
                if not mid:
                    continue
                if not self._passes_prefilter(m):
                    prefilter_dropped += 1
                    continue
                if mid not in seen:
                    if not m.get("category"):
                        m = {**m, "category": cat}
                    seen[mid] = m
                    new_count += 1
            if new_count:
                category_counts[cat] = new_count
                logger.debug("Polymarket category '{}': {} new markets", cat, new_count)
            time.sleep(REQUEST_GAP)

        # Phase 2: general volume-sorted sweep to catch any remaining markets
        offset = 0
        uncategorised = 0
        for _ in range(25):
            params = {
                "active": "true", "order": "volume24hr", "ascending": "false",
                "limit": str(min(limit, 500)), "offset": str(offset),
            }
            batch = self._fetch_page(params)
            if not batch:
                break
            for m in batch:
                mid = str(m.get("id", ""))
                if not mid:
                    continue
                if not self._passes_prefilter(m):
                    prefilter_dropped += 1
                    continue
                if mid not in seen:
                    seen[mid] = m
                    uncategorised += 1
            if len(batch) < min(limit, 500):
                break
            offset += len(batch)
            if offset > 20000:
                break
            time.sleep(REQUEST_GAP)

        if prefilter_dropped:
            logger.info("Polymarket pre-filter: dropped {} markets below $1k liquidity/volume", prefilter_dropped)
        if uncategorised:
            logger.debug("Polymarket general sweep: {} additional uncategorised markets", uncategorised)

        all_rows = list(seen.values())
        if category_counts:
            summary = ", ".join(f"{k}:{v}" for k, v in sorted(category_counts.items()))
            logger.info("Polymarket fetched {} total markets — by category: {}", len(all_rows), summary)

        if all_rows:
            self._write_cache(all_rows)
            return all_rows

        cached, age = self._read_cache()
        if cached and age is not None and age < CACHE_MAX_AGE_SEC:
            logger.warning("Polymarket using cache (age {:.0f}s)", age)
            return cached
        if age is not None and age >= CACHE_MAX_AGE_SEC:
            logger.critical("Polymarket cache too stale — no markets available")
        return []

    def get_market_by_id(self, market_id: str) -> dict | None:
        url = f"{GAMMA_BASE}/markets/{market_id}"
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("Polymarket get_market_by_id {} failed: {}", market_id, e)
            return None

    def get_clob_spread(self, token_id: str) -> float | None:
        """Fetch bid/ask spread from Polymarket CLOB API for thin-liquidity detection."""
        try:
            r = requests.get("https://clob.polymarket.com/book", params={"token_id": token_id}, timeout=15)
            if r.ok:
                data = r.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0].get("price", 0))
                    best_ask = float(asks[0].get("price", 0))
                    return abs(best_ask - best_bid)
        except Exception:
            pass
        return None

    def get_tags(self) -> list[dict]:
        """Fetch all tags from Gamma API. Returns list of dicts with id, label, slug."""
        url = f"{GAMMA_BASE}/tags"
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.warning("Polymarket get_tags failed: {}", e)
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return []

    def get_markets_by_tag(self, tag_id: str | int, limit: int = 100) -> list[dict]:
        """Fetch active markets for a specific tag_id with offset pagination."""
        rows: list[dict] = []
        offset = 0
        max_pages = 50
        for _ in range(max_pages):
            url = f"{GAMMA_BASE}/markets"
            params = {
                "active": "true",
                "tag_id": str(tag_id),
                "limit": str(limit),
                "offset": str(offset),
            }
            try:
                r = requests.get(url, params=params, timeout=60)
                r.raise_for_status()
                batch = r.json()
            except Exception as e:
                logger.warning("Polymarket get_markets_by_tag tag_id={} failed: {}", tag_id, e)
                break
            if isinstance(batch, dict) and "data" in batch:
                batch = batch["data"]
            if not isinstance(batch, list) or not batch:
                break
            rows.extend(batch)
            if len(batch) < limit:
                break
            offset += len(batch)
            if offset > 10000:
                break
            time.sleep(REQUEST_GAP)
        return rows

    def get_all_markets_with_categories(self, limit_per_tag: int = 100) -> list[dict]:
        """
        Fetch all active markets enriched with category from tags.
        Deduplicates by market id; entries with category override those without.
        """
        seen: dict[str, dict] = {}

        # Phase 1: uncategorized sweep using existing method
        uncategorized = self.get_all_active_markets()
        for m in uncategorized:
            mid = str(m.get("id", ""))
            if mid:
                seen[mid] = m

        # Phase 2: per-tag sweep
        tags = self.get_tags()
        for tag in tags:
            tag_id = tag.get("id")
            tag_label = tag.get("label") or tag.get("slug") or str(tag_id)
            if tag_id is None:
                continue
            try:
                tag_markets = self.get_markets_by_tag(tag_id, limit_per_tag)
                count = 0
                for m in tag_markets:
                    mid = str(m.get("id", ""))
                    if not mid:
                        continue
                    # Attach category from tag
                    m = {**m, "category": tag.get("label") or tag.get("slug")}
                    # Override existing entry if this one has a category
                    if mid not in seen or not seen[mid].get("category"):
                        seen[mid] = m
                        count += 1
                    else:
                        # Still update category on existing entry
                        seen[mid] = {**seen[mid], "category": m["category"]}
                        count += 1
                logger.info("Tag '{}': {} markets", tag_label, count)
            except Exception as e:
                logger.warning("Polymarket tag '{}' failed: {}", tag_label, e)
            time.sleep(0.1)

        return list(seen.values())

    @staticmethod
    def extract_probability(market_dict: dict) -> float | None:
        prices = market_dict.get("outcomePrices")
        if not prices:
            return None
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except json.JSONDecodeError:
                return None
        if not isinstance(prices, list) or len(prices) != 2:
            logger.debug("Skip multi-outcome or bad outcomePrices: {}", market_dict.get("id"))
            return None
        try:
            return float(prices[0])
        except (TypeError, ValueError):
            return None
