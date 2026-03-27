from __future__ import annotations

import datetime as dt
import math
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from loguru import logger

from engines.prediction_markets.storage.models import (
    SignalEvent,
    StrategyPerformanceSnapshot,
    StrategyVersion,
)

# ── Market quality filter constants (backtest-derived) ───────────────────────
MIN_LIQUIDITY = 5_000       # $5k minimum — only 883/41k markets pass this
MIN_PRICE_MOVEMENT = 0.03   # 3% price range across stored probability_readings
MAX_DAYS_TO_RESOLUTION = 30 # resolution-window boost reference (not a hard cutoff)
MIN_VOLUME_24H = 500        # $500 minimum 24h volume

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "crypto": ["bitcoin", "ethereum", "solana", "crypto", "btc", "eth", "sol", "xrp", "bnb"],
    "sports": ["win", "match", "game", "player", "team", "nfl", "nba", "mlb", "nhl",
               "soccer", "tennis", "football"],
    "politics": ["president", "election", "congress", "senate", "vote", "trump", "biden",
                 "israel", "iran", "russia", "ukraine"],
    "finance": ["stock", "market", "s&p", "dow", "nasdaq", "fed", "interest rate", "gdp",
                "recession"],
    "weather": ["temperature", "°c", "°f", "weather", "rain", "snow", "celsius"],
}


def _infer_category(question: str) -> str:
    """Assign a category from question text when the API provides none."""
    q = question.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return cat
    return "other"


def _resolution_boost(end_date: object) -> float:
    """Return +0.15/+0.05/0 boost to final_score based on days until resolution."""
    if not isinstance(end_date, dt.datetime):
        return 0.0
    now = dt.datetime.now(dt.UTC)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=dt.UTC)
    days = (end_date - now).total_seconds() / 86400
    if 7 <= days <= 30:
        return 0.15
    elif 0 < days < 7:
        return 0.05
    return 0.0


def filter_markets(markets: list[dict], session: Session) -> list[dict]:
    """
    Apply 4-rule quality filter to market dicts before ranking.

    Rules:
    1. Drop markets where liquidity < MIN_LIQUIDITY AND volume_24h < MIN_VOLUME_24H.
    2. Drop markets where MAX(probability) - MIN(probability) < MIN_PRICE_MOVEMENT
       across all stored probability_readings.
    3. Attach _resolution_boost (+0.15 for 7-30 days, +0.05 for 0-7 days, 0 otherwise).
    4. Infer category from question text for markets where category is NULL/empty.

    Returns a filtered subset with _resolution_boost and category attached.
    """
    # Rule 1: liquidity OR volume threshold
    after_liq: list[dict] = []
    dropped_liq = 0
    for m in markets:
        liq = float(m.get("liquidity") or 0)
        vol = float(m.get("volume_24h") or 0)
        if liq >= MIN_LIQUIDITY or vol >= MIN_VOLUME_24H:
            after_liq.append(m)
        else:
            dropped_liq += 1
    if dropped_liq:
        logger.info(
            "filter_markets: dropped {} markets below liquidity/volume threshold ({} remain)",
            dropped_liq, len(after_liq),
        )

    # Rule 2: price movement check via stored probability_readings
    after_movement: list[dict] = []
    dropped_movement = 0
    for m in after_liq:
        db_id = m.get("_db_id")
        if db_id is None:
            # Not yet in DB — let through so it can be seeded
            after_movement.append(m)
            continue
        try:
            row = session.execute(
                text(
                    "SELECT MAX(probability) - MIN(probability) "
                    "FROM probability_readings WHERE market_id = :mid"
                ),
                {"mid": int(db_id)},
            ).fetchone()
            movement = float(row[0]) if row and row[0] is not None else 0.0
        except Exception:
            movement = 0.0
        if movement >= MIN_PRICE_MOVEMENT:
            after_movement.append(m)
        else:
            dropped_movement += 1
    if dropped_movement:
        logger.info(
            "filter_markets: dropped {} markets below price movement threshold ({} remain)",
            dropped_movement, len(after_movement),
        )

    # Rules 3 & 4: resolution boost + keyword category tagging
    result: list[dict] = []
    for m in after_movement:
        boost = _resolution_boost(m.get("end_date"))
        cat = m.get("category") or ""
        if not cat:
            cat = _infer_category(m.get("question") or "")
            m = {**m, "category": cat}
        result.append({**m, "_resolution_boost": boost})

    logger.info("filter_markets: {} markets passed all 4 rules", len(result))
    return result


def _liquidity_score(liq: float | None) -> float:
    x = max(float(liq or 0), 1.0)
    return min(1.0, math.log10(x / 1e3) / 2.0)


def _spread_score(spread: float | None) -> float:
    s = float(spread or 0)
    return max(0.0, 1.0 - min(s / 0.25, 1.0))


def _normalize(v: float, floor: float, ceil: float) -> float:
    if ceil <= floor:
        return 0.5
    return max(0.0, min(1.0, (v - floor) / (ceil - floor)))


class OpportunityRanker:
    def __init__(self, session: Session):
        self.session = session

    def _default_strategy_version_id(self) -> int:
        champ = self.session.scalars(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_name == "default", StrategyVersion.status == "champion")
            .limit(1)
        ).first()
        if champ:
            return champ.id
        v = self.session.scalars(select(StrategyVersion).limit(1)).first()
        return v.id if v else 1

    def _trust_score(self, strategy_version_id: int, category: str | None, signal_type: str) -> float:
        cat = category or "unknown"
        snap = self.session.scalars(
            select(StrategyPerformanceSnapshot)
            .where(
                StrategyPerformanceSnapshot.strategy_version_id == strategy_version_id,
                StrategyPerformanceSnapshot.category == cat,
                StrategyPerformanceSnapshot.signal_type == signal_type,
            )
            .order_by(StrategyPerformanceSnapshot.as_of_date.desc())
            .limit(1)
        ).first()
        if not snap or snap.bets_count < 10:
            return 0.40
        roi_c = _normalize(float(snap.roi_percent or 0), -20.0, 30.0)
        wr_c = _normalize(float(snap.win_rate or 0.5) * 100, 40.0, 70.0)
        cal = 1.0 - min(float(snap.calibration_error or 0.5), 1.0)
        return max(0.0, min(1.0, roi_c * 0.50 + wr_c * 0.30 + cal * 0.20))

    def _signal_score(self, opp: dict) -> float:
        st = opp.get("signal_type")
        ev = float(opp.get("ev_estimate") or 0)
        s = ev * 0.50
        if st == "outlier":
            s += float(opp.get("divergence_from_baseline") or 0) * 0.30
        elif st == "news_lag":
            s += float(opp.get("relevance_score") or 0) * 0.20
        elif st == "velocity":
            s += abs(float(opp.get("velocity") or 0)) * 0.20
        elif st == "related_divergence":
            gap = float((opp.get("detail") or {}).get("gap") or 0)
            s += gap * 0.25
        elif st == "scheduled_proximity":
            detail = opp.get("detail") or {}
            if detail.get("velocity_boosted"):
                s += 0.10
            days = float(detail.get("days_until") or 7)
            s += max(0.0, (7 - days) / 7.0) * 0.08  # closer = higher bonus
        elif st == "cross_category_momentum":
            cats = len((opp.get("detail") or {}).get("categories") or [])
            s += min(cats * 0.03, 0.12)
        # thin_liquidity uses ev * 0.50 only (already conservative ev=0.05)
        return max(0.0, min(1.0, s))

    def _execution_score(self, opp: dict, reading_ts: dt.datetime | None) -> float:
        liq = _liquidity_score(opp.get("liquidity"))
        spr = _spread_score(opp.get("spread"))
        now = dt.datetime.now(dt.UTC)
        if reading_ts:
            if reading_ts.tzinfo is None:
                reading_ts = reading_ts.replace(tzinfo=dt.UTC)
            age_m = (now - reading_ts).total_seconds() / 60.0
            if age_m < 5:
                staleness = 1.0
            elif age_m >= 30:
                staleness = 0.0
            else:
                staleness = 1.0 - (age_m - 5) / 25.0
        else:
            staleness = 0.5
        age_score = 1.0
        ex = min(1.0, liq * 0.40 + spr * 0.30 + staleness * 0.20 + age_score * 0.10)
        return max(0.0, min(1.0, ex))

    def rank_opportunities(
        self,
        outlier_alerts: list[dict],
        news_lag_alerts: list[dict],
        velocity_alerts: list[dict],
        arb_alerts: list[dict],
        extra_alerts: list[dict] | None = None,
        *,
        reading_timestamps: dict[int, dt.datetime] | None = None,
        resolution_boost_map: dict[int, float] | None = None,
    ) -> list[dict]:
        reading_timestamps = reading_timestamps or {}
        resolution_boost_map = resolution_boost_map or {}
        comb = (
            list(outlier_alerts)
            + list(news_lag_alerts)
            + list(velocity_alerts)
            + list(arb_alerts)
            + list(extra_alerts or [])
        )
        sv = self._default_strategy_version_id()
        ranked: list[dict] = []
        for i, opp in enumerate(comb):
            mid = opp.get("market_id")
            ts = reading_timestamps.get(int(mid)) if mid else None
            sig = self._signal_score(opp)
            exe = self._execution_score(opp, ts)
            tr = self._trust_score(sv, opp.get("category") or opp.get("theme"), str(opp.get("signal_type")))
            boost = resolution_boost_map.get(int(mid), 0.0) if mid else 0.0
            final = sig * 0.45 + exe * 0.30 + tr * 0.25 + boost
            item = {
                "rank": 0,
                "market_id": mid,
                "question": opp.get("question"),
                "category": opp.get("category"),
                "theme": opp.get("theme"),
                "signal_type": opp.get("signal_type"),
                "expected_bet": opp.get("expected_bet"),
                "ev_estimate": opp.get("ev_estimate"),
                "signal_score": round(sig, 4),
                "execution_score": round(exe, 4),
                "trust_score": round(tr, 4),
                "final_score": round(final, 4),
                "platform": opp.get("platform"),
                "time_sensitive": opp.get("signal_type") in ("news_lag", "scheduled_proximity"),
                "expires_at": None,
                "detail": opp.get("detail"),
                "liquidity": opp.get("liquidity"),
                "probability": opp.get("probability"),
                "confidence": opp.get("confidence"),
            }
            ranked.append(item)

        def sort_key(o: dict) -> float:
            return float(o.get("final_score") or 0)

        ranked.sort(key=sort_key, reverse=True)
        for idx, o in enumerate(ranked[:10], start=1):
            o["rank"] = idx
        top = ranked[:10]

        for o in top:
            self._log_signal_event(o, sv)

        return top

    def _log_signal_event(self, o: dict, strategy_version_id: int) -> None:
        mid = o.get("market_id")
        if not mid:
            return
        ev = SignalEvent(
            market_id=int(mid),
            strategy_version_id=strategy_version_id,
            signal_type=str(o.get("signal_type")),
            fired_at=dt.datetime.now(dt.UTC),
            snapshot_probability=o.get("probability"),
            liquidity=o.get("liquidity"),
            spread=(o.get("detail") or {}).get("spread") if isinstance(o.get("detail"), dict) else None,
            volume_24h=None,
            theme=o.get("theme"),
            relevance_score=0.0,
            ev_estimate=o.get("ev_estimate"),
            velocity=None,
            acceleration=None,
            recent_news_count=None,
            baseline_probability=(
                (o.get("detail") or {}).get("baseline_prob") if isinstance(o.get("detail"), dict) else None
            ),
            divergence=(
                (o.get("detail") or {}).get("divergence") if isinstance(o.get("detail"), dict) else None
            ),
            expected_direction=o.get("expected_bet"),
            signal_score=o.get("signal_score"),
            execution_score=o.get("execution_score"),
            trust_score=o.get("trust_score"),
            final_score=o.get("final_score"),
            detail_json=o.get("detail") if isinstance(o.get("detail"), dict) else {},
        )
        self.session.add(ev)


def news_lag_to_opportunities(alerts: list[dict]) -> list[dict]:
    out = []
    now = dt.datetime.now(dt.UTC)
    for a in alerts:
        m = a["market"]
        # a["article"] is a plain dict snapshot (id, headline, published_at, fetched_at)
        # extracted in detect_news_lag() — never an ORM object.
        article = a["article"]
        pub = article.get("published_at") or article.get("fetched_at")
        if pub and pub.tzinfo is None:
            pub = pub.replace(tzinfo=dt.UTC)
        mins = (now - pub).total_seconds() / 60.0 if pub else 30.0
        out.append(
            {
                "signal_type": "news_lag",
                "market_id": m.get("_db_id"),
                "platform": m.get("platform"),
                "question": m.get("question"),
                "category": m.get("category"),
                "theme": m.get("theme"),
                "expected_bet": a.get("expected_direction", "unknown"),
                "ev_estimate": float(a.get("relevance_score") or 0) * 0.15,
                "confidence": "ok",
                "probability": a.get("current_probability"),
                "liquidity": m.get("liquidity"),
                "volume_24h": m.get("volume_24h"),
                "spread": m.get("spread"),
                "low_confidence": m.get("low_confidence"),
                "divergence_from_baseline": 0.0,
                "relevance_score": float(a.get("relevance_score") or 0),
                "news_lag_minutes": mins,
                "detail": {"article_id": article.get("id"), "headline": article.get("headline")},
            }
        )
    return out


def time_sensitivity_score(opp: dict) -> float:
    if opp.get("signal_type") != "news_lag":
        return 0.5
    mins = float(opp.get("news_lag_minutes") or 30)
    return max(0.0, 1.0 - mins / 60.0)
