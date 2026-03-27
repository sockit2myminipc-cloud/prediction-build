from __future__ import annotations

import datetime as dt
import threading
from typing import Any

_lock = threading.Lock()
_last_opportunities: list[dict] = []
_last_health: dict[str, Any] = {}
_last_cycle_at: dt.datetime | None = None
_last_market_fetch: dict[str, dt.datetime | None] = {"polymarket": None, "kalshi": None}
_last_news_fetch: dict[str, dt.datetime | None] = {}
_filter_stats: dict[str, Any] = {}
_newsapi_status: dict[str, Any] = {}


def set_cycle_results(opportunities: list[dict], health: dict, alerts_delta: int = 0) -> None:
    global _last_opportunities, _last_health, _last_cycle_at
    with _lock:
        _last_opportunities = opportunities
        _last_health = health
        _last_cycle_at = dt.datetime.now(dt.UTC)
        _ = alerts_delta


def set_filter_stats(stats: dict) -> None:
    global _filter_stats
    with _lock:
        _filter_stats = dict(stats)


def set_newsapi_status(calls_today: int, last_fetch: dt.datetime | None) -> None:
    global _newsapi_status
    with _lock:
        _newsapi_status = {"calls_today": calls_today, "daily_limit": 80, "last_fetch": last_fetch.isoformat() if last_fetch else None}


def set_fetch_time(source: str, when: dt.datetime | None) -> None:
    with _lock:
        if source in _last_market_fetch:
            _last_market_fetch[source] = when
        _last_news_fetch[source] = when


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "opportunities": list(_last_opportunities),
            "health": dict(_last_health),
            "last_cycle_at": _last_cycle_at,
            "last_market_fetch": dict(_last_market_fetch),
            "last_news_fetch": dict(_last_news_fetch),
            "alerts_24h": _last_health.get("alerts_last_24h", 0) if _last_health else 0,
            "filter_stats": dict(_filter_stats),
            "newsapi_status": dict(_newsapi_status),
        }
