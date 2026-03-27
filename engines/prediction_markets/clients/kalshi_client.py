from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import requests
from loguru import logger

BASES = [
    "https://trading.kalshi.com/trade-api/v2",
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://trading-api.kalshi.com/trade-api/v2",
]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_PATH = PROJECT_ROOT / "data" / "kalshi_cache.json"
CACHE_MAX_AGE_SEC = 4 * 3600
MAX_FETCH_RETRIES = 3


def _to_float(v: object) -> float:
    """Safely convert a value to float — avoids the truthy-string trap with '0.0000'."""
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


class KalshiClient:
    def __init__(self, cache_path: Path | None = None):
        self.cache_path = cache_path or CACHE_PATH
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._working_base: str | None = None

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

    def _pick_base(self) -> str | None:
        if self._working_base:
            return self._working_base
        for base in BASES:
            try:
                r = requests.get(f"{base}/markets", params={"limit": 1}, timeout=15)
                if r.ok:
                    self._working_base = base
                    logger.info("Kalshi API base: {}", base)
                    return base
            except Exception as e:
                logger.debug("Kalshi probe {} failed: {}", base, e)
        return None

    def get_all_markets(self, limit: int = 2000, status: str = "open") -> list[dict]:
        """
        Fetch ALL markets with cursor-based pagination (no artificial cap).
        Logs per-category counts.
        """
        base = self._pick_base()
        if not base:
            logger.warning("Kalshi: no reachable base URL")
            cached, age = self._read_cache()
            if cached and age is not None and age < CACHE_MAX_AGE_SEC:
                return cached
            if age is not None and age >= CACHE_MAX_AGE_SEC:
                logger.critical("Kalshi cache too stale")
            return []

        collected: list[dict] = []
        cursor: str | None = None

        while True:
            params: dict = {"limit": 200, "status": status}
            if cursor:
                params["cursor"] = cursor
            body = None
            last_err: Exception | None = None
            for attempt in range(1, MAX_FETCH_RETRIES + 1):
                try:
                    r = requests.get(f"{base}/markets", params=params, timeout=60)
                    r.raise_for_status()
                    body = r.json()
                    break
                except Exception as e:
                    last_err = e
                    if attempt < MAX_FETCH_RETRIES:
                        backoff_s = 2 ** (attempt - 1)
                        logger.warning(
                            "Kalshi markets fetch failed (attempt {}/{}): {}. Retrying in {}s",
                            attempt, MAX_FETCH_RETRIES, e, backoff_s,
                        )
                        time.sleep(backoff_s)
                    else:
                        logger.warning(
                            "Kalshi markets fetch failed after {} attempts: {}",
                            MAX_FETCH_RETRIES, e,
                        )

            if body is None:
                logger.warning("Kalshi markets fetch failed: {}", last_err)
                cached, age = self._read_cache()
                if cached and age is not None and age < CACHE_MAX_AGE_SEC:
                    logger.warning("Using Kalshi cache age {:.0f}s", age)
                    return cached
                if age is not None and age >= CACHE_MAX_AGE_SEC:
                    logger.critical("Kalshi cache too stale")
                return []

            markets = body.get("markets") or []
            # Sort each page by volume descending so high-volume markets rise to the top
            markets.sort(
                key=lambda m: _to_float(m.get("volume_24h_fp") or m.get("volume_fp")),
                reverse=True,
            )
            collected.extend(markets)
            cursor = body.get("cursor")
            if not cursor or not markets:
                break
            if len(collected) >= limit:
                break
            time.sleep(0.1)

        if collected:
            # Sort all collected markets by volume_24h_fp desc so high-volume markets
            # survive the cap even when early pages were newest-first (zero-volume).
            collected.sort(
                key=lambda m: _to_float(m.get("volume_24h_fp") or m.get("volume_fp")),
                reverse=True,
            )
            collected = collected[:500]

            # Pre-filter: drop markets with zero volume AND zero open_interest.
            # Threshold is $1 (not $1k) so we don't discard thinly-traded but real markets;
            # the downstream filter_markets() in opportunity_ranker applies the real $5k threshold.
            before_filter = len(collected)
            collected = [
                m for m in collected
                if (
                    _to_float(m.get("volume_24h_fp")) >= 1
                    or _to_float(m.get("volume_fp")) >= 1
                    or _to_float(m.get("open_interest_fp")) >= 1
                    or _to_float(m.get("liquidity_dollars")) >= 1
                )
            ]
            dropped = before_filter - len(collected)
            if dropped:
                logger.info(
                    "Kalshi pre-filter: kept {}/{} markets (dropped {} with zero volume/liquidity)",
                    len(collected), before_filter, dropped,
                )

            # Log per-category counts
            category_counts: dict[str, int] = {}
            for m in collected:
                cat = m.get("category") or "unknown"
                category_counts[cat] = category_counts.get(cat, 0) + 1
            summary = ", ".join(f"{k}:{v}" for k, v in sorted(category_counts.items()))
            logger.info("Kalshi fetched {} total markets — by category: {}", len(collected), summary)
            self._write_cache(collected)

        return collected

    @staticmethod
    def normalize_market(raw: dict) -> dict:
        # Kalshi API v2 returns dollar-denominated prices in *_dollars fields (already 0-1 range)
        # and volume/open_interest in *_fp (floating-point dollars) fields.
        yb = raw.get("yes_bid_dollars") if raw.get("yes_bid_dollars") is not None else raw.get("yes_bid")
        ya = raw.get("yes_ask_dollars") if raw.get("yes_ask_dollars") is not None else raw.get("yes_ask")
        try:
            # _dollars fields are already in [0, 1]; legacy yes_bid/yes_ask were in cents
            if raw.get("yes_bid_dollars") is not None:
                yb_f = float(yb) if yb is not None else 0.0
                ya_f = float(ya) if ya is not None else 0.0
            else:
                yb_f = float(yb) / 100.0 if yb is not None else 0.0
                ya_f = float(ya) / 100.0 if ya is not None else 0.0
        except (TypeError, ValueError):
            yb_f, ya_f = 0.0, 0.0
        yes_mid = (yb_f + ya_f) / 2 if yb_f or ya_f else 0.0
        spread = abs(ya_f - yb_f)
        low_confidence = spread > 0.10
        close_time = raw.get("close_time") or raw.get("expiration_time")
        # volume_24h_fp is 24h dollar volume; volume_fp is total dollar volume; fall back to legacy fields.
        # Use _to_float() — "0.00" is a truthy string so plain `or` chaining gives wrong results.
        volume_24h = (
            _to_float(raw.get("volume_24h_fp"))
            or _to_float(raw.get("volume_24h"))
            or _to_float(raw.get("volume_fp"))
            or _to_float(raw.get("volume"))
        )
        # open_interest_fp = total outstanding contract value (stable proxy for liquidity).
        # liquidity_dollars = real-time order book depth — routinely 0 on active Kalshi markets.
        liquidity = (
            _to_float(raw.get("open_interest_fp"))
            or _to_float(raw.get("liquidity_dollars"))
            or _to_float(raw.get("open_interest"))
            or _to_float(raw.get("liquidity"))
        )
        return {
            "platform": "kalshi",
            "market_id": raw.get("ticker", ""),
            "question": raw.get("title", ""),
            "probability": yes_mid,
            "yes_bid": yb_f,
            "yes_ask": ya_f,
            "spread": spread,
            "volume_24h": volume_24h,
            "liquidity": liquidity,
            "end_date": close_time,
            "category": raw.get("category", "") or "",
            "low_confidence": low_confidence,
            "raw": raw,
        }
