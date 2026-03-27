from __future__ import annotations

import json
import time
import datetime as dt
from pathlib import Path

import requests
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from engines.prediction_markets.storage.db import get_session
from engines.prediction_markets.storage.models import HistoricalMarket

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUEST_DELAY = 0.1  # seconds between HTTP calls

POLYMARKET_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
POLYMARKET_CLOB_URL = "https://clob.polymarket.com/prices-history"

KALSHI_URLS = [
    "https://api.elections.kalshi.com/trade-api/v2/markets",
    "https://trading-api.kalshi.com/trade-api/v2/markets",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(v: object) -> dt.datetime | None:
    """Parse an ISO-8601 string (or None/int) to a timezone-aware datetime."""
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=dt.timezone.utc)
        return v
    if isinstance(v, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(v), tz=dt.timezone.utc)
        except Exception:
            return None
    s = str(v).strip()
    if not s:
        return None
    # Try various ISO formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            parsed = dt.datetime.strptime(s, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed
        except ValueError:
            continue
    # Fallback: fromisoformat (Python 3.11+ handles Z)
    try:
        s_clean = s.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(s_clean)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except Exception:
        logger.warning("Could not parse datetime string: {!r}", s)
        return None


def _safe_get(url: str, params: dict | None = None, timeout: int = 20) -> dict | list | None:
    """GET with error handling. Returns parsed JSON or None."""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        logger.warning("HTTP {} fetching {}: {}", exc.response.status_code, url, exc)
    except requests.RequestException as exc:
        logger.warning("Request error fetching {}: {}", url, exc)
    except Exception as exc:
        logger.warning("Unexpected error fetching {}: {}", url, exc)
    return None


def _existing_ids(session, source: str) -> set[str]:
    """Return set of market_ids already stored for this source."""
    rows = session.scalars(
        select(HistoricalMarket.market_id).where(HistoricalMarket.source == source)
    ).all()
    return set(rows)


# ---------------------------------------------------------------------------
# Polymarket
# ---------------------------------------------------------------------------

def _fetch_polymarket_price_history(condition_id: str) -> list[dict]:
    """Fetch price history from Polymarket CLOB API."""
    if not condition_id:
        return []
    time.sleep(REQUEST_DELAY)
    data = _safe_get(POLYMARKET_CLOB_URL, params={"market": condition_id, "interval": "1d"})
    if not data or not isinstance(data, dict):
        return []
    history = data.get("history", [])
    if not isinstance(history, list):
        return []
    result = []
    for point in history:
        if isinstance(point, dict) and "t" in point and "p" in point:
            try:
                result.append({"t": int(point["t"]), "p": float(point["p"])})
            except (TypeError, ValueError):
                continue
    return result


def _parse_polymarket_resolution(market: dict) -> str | None:
    """Extract resolution as a string (YES/NO or numeric 0-1)."""
    res_price = market.get("resolutionPrice")
    if res_price is not None:
        try:
            val = float(res_price)
            if val >= 0.99:
                return "YES"
            elif val <= 0.01:
                return "NO"
            return str(round(val, 4))
        except (TypeError, ValueError):
            pass

    # Fallback: check outcomePrices for closed markets
    outcome_prices = market.get("outcomePrices")
    if outcome_prices:
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except Exception:
                outcome_prices = None
        if isinstance(outcome_prices, list) and len(outcome_prices) > 0:
            try:
                val = float(outcome_prices[0])
                if val >= 0.99:
                    return "YES"
                elif val <= 0.01:
                    return "NO"
                return str(round(val, 4))
            except (TypeError, ValueError):
                pass
    return None


def _fetch_polymarket_resolved(days_back: int, existing_ids: set[str]) -> list[HistoricalMarket]:
    """Paginate through Polymarket closed markets and return HistoricalMarket objects."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_back)
    results: list[HistoricalMarket] = []
    limit = 100
    offset = 0

    # Format cutoff as YYYY-MM-DD for the end_date_min filter
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    while True:
        time.sleep(REQUEST_DELAY)
        data = _safe_get(
            POLYMARKET_MARKETS_URL,
            params={
                "closed": "true",
                "limit": limit,
                "offset": offset,
                "order": "endDate",
                "ascending": "false",
                "end_date_min": cutoff_str,
            },
        )
        if not data or not isinstance(data, list):
            logger.warning("Polymarket: unexpected response at offset {}", offset)
            break
        if len(data) == 0:
            break

        for market in data:
            if not isinstance(market, dict):
                continue

            market_id = str(market.get("id") or market.get("conditionId") or "")
            if not market_id:
                continue
            if market_id in existing_ids:
                continue

            condition_id = str(market.get("conditionId") or market.get("condition_id") or "")
            question = str(market.get("question") or market.get("title") or "")
            category = market.get("category") or market.get("groupItemTitle") or None
            resolution = _parse_polymarket_resolution(market)

            open_date = _parse_dt(market.get("startDate") or market.get("createdAt"))
            close_date = _parse_dt(market.get("endDate") or market.get("closedAt"))
            resolution_date = _parse_dt(market.get("resolutionDate") or market.get("endDate"))

            # Filter by close_date within days_back
            if close_date is not None and close_date < cutoff:
                continue

            # Fetch price history
            price_history = _fetch_polymarket_price_history(condition_id)

            obj = HistoricalMarket(
                source="polymarket",
                market_id=market_id,
                question=question,
                category=str(category) if category else None,
                resolution=resolution,
                open_date=open_date,
                close_date=close_date,
                resolution_date=resolution_date,
                price_history=json.dumps(price_history),
            )
            results.append(obj)
            existing_ids.add(market_id)

        if len(data) < limit:
            break
        offset += limit

    return results


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------

def _resolve_kalshi_base_url() -> str | None:
    """Try each Kalshi base URL and return the first that responds."""
    for url in KALSHI_URLS:
        try:
            resp = requests.get(url, params={"status": "finalized", "limit": 1}, timeout=10)
            if resp.status_code < 500:
                return url
        except Exception:
            continue
    return None


def _fetch_kalshi_price_history(base_url: str, series_ticker: str, ticker: str) -> list[dict]:
    """Fetch candlestick price history from Kalshi."""
    if not series_ticker or not ticker:
        return []
    url = f"{base_url.rstrip('/')}"
    # Strip /markets suffix to get the base path
    if url.endswith("/markets"):
        url = url[: -len("/markets")]
    candlestick_url = f"{url}/series/{series_ticker}/markets/{ticker}/candlesticks"
    time.sleep(REQUEST_DELAY)
    data = _safe_get(candlestick_url)
    if not data or not isinstance(data, dict):
        return []
    candles = data.get("candlesticks", data.get("history", []))
    if not isinstance(candles, list):
        return []
    result = []
    for c in candles:
        if not isinstance(c, dict):
            continue
        # Kalshi candlesticks have ts (epoch) and yes_price or price fields
        ts = c.get("ts") or c.get("t") or c.get("end_period_ts")
        price = c.get("yes_price") or c.get("price") or c.get("p")
        if ts is not None and price is not None:
            try:
                result.append({"t": int(ts), "p": float(price)})
            except (TypeError, ValueError):
                continue
    return result


def _fetch_kalshi_resolved(days_back: int, existing_ids: set[str]) -> list[HistoricalMarket]:
    """Paginate through Kalshi finalized markets and return HistoricalMarket objects."""
    base_url = _resolve_kalshi_base_url()
    if not base_url:
        logger.warning("Kalshi: could not reach any API endpoint, skipping")
        return []

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_back)
    results: list[HistoricalMarket] = []
    cursor: str | None = None
    limit = 100

    while True:
        params: dict = {"status": "finalized", "limit": limit}
        if cursor:
            params["cursor"] = cursor

        time.sleep(REQUEST_DELAY)
        data = _safe_get(base_url, params=params)
        if not data or not isinstance(data, dict):
            logger.warning("Kalshi: unexpected response, stopping pagination")
            break

        markets = data.get("markets", [])
        if not markets:
            break

        for market in markets:
            if not isinstance(market, dict):
                continue

            ticker = str(market.get("ticker") or "")
            if not ticker:
                continue
            if ticker in existing_ids:
                continue

            question = str(market.get("title") or market.get("question") or "")
            category = market.get("category") or None
            result_val = market.get("result")
            if result_val == "yes":
                resolution = "YES"
            elif result_val == "no":
                resolution = "NO"
            else:
                resolution = None

            open_date = _parse_dt(market.get("open_time") or market.get("created_time"))
            close_date = _parse_dt(market.get("close_time") or market.get("expiration_time"))
            resolution_date = _parse_dt(
                market.get("resolution_time") or market.get("close_time") or market.get("expiration_time")
            )

            # Filter by close_date within days_back
            if close_date is not None and close_date < cutoff:
                continue

            series_ticker = str(market.get("series_ticker") or "")
            price_history = _fetch_kalshi_price_history(base_url, series_ticker, ticker)

            obj = HistoricalMarket(
                source="kalshi",
                market_id=ticker,
                question=question,
                category=str(category) if category else None,
                resolution=resolution,
                open_date=open_date,
                close_date=close_date,
                resolution_date=resolution_date,
                price_history=json.dumps(price_history),
            )
            results.append(obj)
            existing_ids.add(ticker)

        # Advance cursor
        cursor = data.get("cursor")
        if not cursor or len(markets) < limit:
            break

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def pull_history(days_back: int = 90) -> dict:
    """
    Pull resolved markets from Polymarket and Kalshi for the last `days_back` days.

    Skips markets already present in the DB (idempotent).

    Returns:
        {"polymarket": N, "kalshi": M, "total": N+M}
    """
    session = get_session()
    try:
        # Ensure tables exist
        from engines.prediction_markets.storage.db import init_db
        try:
            init_db()
        except Exception as exc:
            logger.warning("init_db warning (tables may already exist): {}", exc)

        poly_existing = _existing_ids(session, "polymarket")
        kalshi_existing = _existing_ids(session, "kalshi")

        poly_before = len(poly_existing)
        kalshi_before = len(kalshi_existing)

        logger.info(
            "Fetching Polymarket resolved markets (last {} days, {} already in DB)...",
            days_back,
            poly_before,
        )
        poly_markets = _fetch_polymarket_resolved(days_back, poly_existing)

        logger.info(
            "Fetching Kalshi finalized markets (last {} days, {} already in DB)...",
            days_back,
            kalshi_before,
        )
        kalshi_markets = _fetch_kalshi_resolved(days_back, kalshi_existing)

        # Persist new records
        poly_saved = 0
        for obj in poly_markets:
            session.add(obj)
            try:
                session.flush()
                poly_saved += 1
            except IntegrityError:
                session.rollback()
            except Exception as exc:
                logger.warning("Error saving Polymarket market {}: {}", obj.market_id, exc)
                session.rollback()

        kalshi_saved = 0
        for obj in kalshi_markets:
            session.add(obj)
            try:
                session.flush()
                kalshi_saved += 1
            except IntegrityError:
                session.rollback()
            except Exception as exc:
                logger.warning("Error saving Kalshi market {}: {}", obj.market_id, exc)
                session.rollback()

        try:
            session.commit()
        except Exception as exc:
            logger.error("Commit failed: {}", exc)
            session.rollback()

        already_existed = poly_before + kalshi_before
        total = poly_saved + kalshi_saved
        logger.info(
            "Pulled {} Polymarket + {} Kalshi resolved markets ({} already existed)",
            poly_saved,
            kalshi_saved,
            already_existed,
        )
        return {"polymarket": poly_saved, "kalshi": kalshi_saved, "total": total}

    finally:
        session.close()


if __name__ == "__main__":
    result = pull_history(days_back=90)
    print(result)
