from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from engines.prediction_markets.storage.models import NewsMarketLink, ProbabilityReading


class VelocityScanner:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _utcnow() -> dt.datetime:
        """Return current UTC time as a naive datetime (matches SQLite storage)."""
        return dt.datetime.now(dt.UTC).replace(tzinfo=None)

    @staticmethod
    def _strip_tz(d: dt.datetime) -> dt.datetime:
        """Ensure a datetime is naive (strip tzinfo if present)."""
        return d.replace(tzinfo=None) if d.tzinfo is not None else d

    def get_velocity(self, market_db_id: int, window_hours: float = 2.0) -> dict:
        now = self._utcnow()
        start = now - dt.timedelta(hours=window_hours)
        prev_start = start - dt.timedelta(hours=window_hours)
        rows = list(
            self.session.scalars(
                select(ProbabilityReading)
                .where(
                    ProbabilityReading.market_id == market_db_id,
                    ProbabilityReading.quality == "ok",
                    ProbabilityReading.timestamp >= prev_start,
                )
                .order_by(ProbabilityReading.timestamp.asc())
            ).all()
        )
        if len(rows) < 2:
            return {
                "delta": 0.0,
                "velocity": 0.0,
                "acceleration": 0.0,
                "reading_count": len(rows),
                "quality": "stale",
            }
        in_win = [r for r in rows if self._strip_tz(r.timestamp) >= start]
        prev_win = [r for r in rows if prev_start <= self._strip_tz(r.timestamp) < start]
        latest = rows[-1].probability
        old = next((r.probability for r in rows if self._strip_tz(r.timestamp) <= start), rows[0].probability)
        delta = latest - old
        velocity = delta / window_hours if window_hours else 0.0
        v_prev = 0.0
        if len(prev_win) >= 2:
            v_prev = (prev_win[-1].probability - prev_win[0].probability) / window_hours
        acceleration = velocity - v_prev
        qual = "ok" if len(in_win) >= 2 else "stale"
        return {
            "delta": delta,
            "velocity": velocity,
            "acceleration": acceleration,
            "reading_count": len(rows),
            "quality": qual,
        }

    def find_movers(self, markets: list[dict], min_velocity: float = 0.04, min_liquidity: float = 5000) -> list[dict]:
        movers: list[dict] = []
        for m in markets:
            if float(m.get("liquidity") or 0) < min_liquidity:
                continue
            dbid = m.get("_db_id")
            if not dbid:
                continue
            v = self.get_velocity(int(dbid), window_hours=2.0)
            if v["quality"] == "stale":
                continue
            vel = v["velocity"]
            if abs(vel) < min_velocity:
                continue
            tag = self._classify(vel)
            mom = "momentum_increasing" if abs(v["acceleration"]) >= 0.01 else "steady"
            movers.append({**m, "_velocity": v, "move_class": tag, "momentum_tag": mom})
        return movers

    @staticmethod
    def _classify(velocity: float) -> str:
        if velocity > 0.06:
            return "strong_bullish_move"
        if velocity > 0.04:
            return "mild_bullish_move"
        if velocity < -0.06:
            return "strong_bearish_move"
        if velocity < -0.04:
            return "mild_bearish_move"
        return "flat"

    def has_recent_news_lag(self, market_db_id: int) -> bool:
        # Use timezone-aware datetime to match the DateTime(timezone=True) column.
        since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=6)
        row = self.session.scalars(
            select(NewsMarketLink).where(
                NewsMarketLink.market_id == market_db_id,
                NewsMarketLink.created_at >= since,
                NewsMarketLink.relevance_score > 0.4,
            ).limit(1)
        ).first()
        return row is not None

    def detect_velocity_opportunities(self, movers: list[dict]) -> list[dict]:
        alerts: list[dict] = []
        for m in movers:
            tag = m.get("move_class", "")
            if "mild" not in tag and "strong" not in tag:
                continue
            dbid = int(m.get("_db_id"))
            news = self.has_recent_news_lag(dbid)
            v = m["_velocity"]["velocity"]
            base_ev = 0.62 if abs(v) >= 0.06 else 0.55
            ev = max(0.0, base_ev - 0.5) + abs(v) * 0.5
            alerts.append(
                {
                    "signal_type": "velocity",
                    "market": m,
                    "type": "velocity",
                    "ev_estimate": min(ev, 0.35),
                    "detail": {"news_linked": news, "velocity": v, "move_class": tag},
                }
            )
        return alerts


def velocity_alerts_to_opportunities(alerts: list[dict]) -> list[dict]:
    out = []
    for a in alerts:
        m = a["market"]
        out.append(
            {
                "signal_type": "velocity",
                "market_id": m.get("_db_id"),
                "platform": m.get("platform"),
                "question": m.get("question"),
                "category": m.get("category"),
                "theme": m.get("theme"),
                "expected_bet": "YES" if (m["_velocity"]["velocity"] > 0) else "NO" if m["_velocity"]["velocity"] < 0 else "UNKNOWN",
                "ev_estimate": a["ev_estimate"],
                "confidence": "ok",
                "probability": m.get("probability"),
                "liquidity": m.get("liquidity"),
                "volume_24h": m.get("volume_24h"),
                "spread": m.get("spread"),
                "low_confidence": m.get("low_confidence"),
                "divergence_from_baseline": 0.0,
                "relevance_score": 0.0,
                "velocity": m["_velocity"]["velocity"],
                "acceleration": m["_velocity"]["acceleration"],
                "detail": a.get("detail"),
            }
        )
    return out
