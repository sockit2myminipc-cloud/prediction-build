from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from engines.prediction_markets.storage.models import Market, StatisticalBaseline


@dataclass
class OutlierAlert:
    market: dict
    baseline_source: str
    market_prob: float
    baseline_prob: float
    divergence: float
    direction: str
    expected_bet: str
    ev_estimate: float
    theme: str
    confidence: str


THEME_THRESHOLDS: dict[str, float] = {
    "sports_nfl": 0.08,
    "sports_nba": 0.08,
    "sports_mlb": 0.08,
    # Guide: sports_* — one threshold
    "sports_soccer": 0.08,
    "sports_combat": 0.08,
    "sports_golf": 0.08,
    "elections_us": 0.10,
    "elections_intl": 0.10,
    "fed_macro": 0.05,
    "cpi_macro": 0.07,
    "btc_price": 0.10,
    "recession": 0.12,
}


class OutlierDetector:
    @staticmethod
    def theme_threshold(theme: str) -> float:
        if theme in THEME_THRESHOLDS:
            return THEME_THRESHOLDS[theme]
        if theme.startswith("sports"):
            return 0.08
        if theme.startswith("elections"):
            return 0.10
        return 0.15

    @staticmethod
    def calculate_ev(market_prob: float, baseline_prob: float, bet_yes: bool) -> float:
        mp = min(max(market_prob, 1e-6), 1.0 - 1e-6)
        bp = min(max(baseline_prob, 1e-6), 1.0 - 1e-6)
        if bet_yes:
            return bp * (1.0 / mp - 1.0) - (1.0 - bp)
        return (1.0 - bp) * (1.0 / (1.0 - mp) - 1.0) - bp

    def find_outliers(self, session: Session, markets: list[dict]) -> list[OutlierAlert]:
        baselines = list(session.scalars(select(StatisticalBaseline)).all())
        alerts: list[OutlierAlert] = []
        for m in markets:
            theme = m.get("theme") or "other"
            mp = float(m.get("probability") or 0)
            mid = m.get("_db_id")
            candidates: list[StatisticalBaseline] = []
            for b in baselines:
                if b.market_id is not None and mid is not None and b.market_id == mid:
                    candidates.append(b)
                elif b.theme_key and b.theme_key == theme:
                    candidates.append(b)
            if not candidates:
                continue
            b = max(candidates, key=lambda x: x.fetched_at.timestamp() if x.fetched_at else 0)
            bp = float(b.baseline_probability)
            div = abs(mp - bp)
            th = self.theme_threshold(theme)
            if div < th:
                continue
            direction = "market_too_high" if mp > bp else "market_too_low"
            expected_bet = "NO" if direction == "market_too_high" else "YES"
            bet_yes = expected_bet == "YES"
            ev = self.calculate_ev(mp, bp, bet_yes)
            if ev <= 0.05:
                continue
            conf = "low" if m.get("low_confidence") else "ok"
            alerts.append(
                OutlierAlert(
                    market=m,
                    baseline_source=b.source_name,
                    market_prob=mp,
                    baseline_prob=bp,
                    divergence=div,
                    direction=direction,
                    expected_bet=expected_bet,
                    ev_estimate=ev,
                    theme=theme,
                    confidence=conf,
                )
            )
        return alerts


def outlier_to_opportunity_dict(a: OutlierAlert) -> dict:
    m = a.market
    return {
        "signal_type": "outlier",
        "market_id": m.get("_db_id"),
        "platform": m.get("platform"),
        "question": m.get("question"),
        "category": m.get("category"),
        "theme": a.theme,
        "expected_bet": a.expected_bet,
        "ev_estimate": a.ev_estimate,
        "confidence": a.confidence,
        "probability": m.get("probability"),
        "liquidity": m.get("liquidity"),
        "volume_24h": m.get("volume_24h"),
        "spread": m.get("spread"),
        "low_confidence": m.get("low_confidence"),
        "divergence_from_baseline": a.divergence,
        "relevance_score": 0.0,
        "detail": {
            "baseline_source": a.baseline_source,
            "baseline_prob": a.baseline_prob,
            "direction": a.direction,
        },
    }
