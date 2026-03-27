from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger
from rapidfuzz import fuzz

from engines.prediction_markets.filters.dedup import EVENT_FINGERPRINTS


@dataclass
class ArbitrageAlert:
    kind: str
    market_a: dict | None
    market_b: dict | None
    description: str
    inconsistency: float
    suggested_bet: str | None
    ev_estimate: float


class ArbitrageScanner:
    def find_cross_platform_arb(self, polymarket_markets: list[dict], kalshi_markets: list[dict]) -> list[ArbitrageAlert]:
        alerts: list[ArbitrageAlert] = []
        for fp, spec in EVENT_FINGERPRINTS.items():
            kws = spec["keywords"]
            pm_cands = [m for m in polymarket_markets if any(k in (m.get("question") or "").lower() for k in kws)]
            k_cands = [m for m in kalshi_markets if any(k in (m.get("question") or "").lower() for k in kws)]
            for pm in pm_cands:
                best = None
                best_sc = 70
                for k in k_cands:
                    sc = fuzz.token_set_ratio(
                        (pm.get("question") or "").lower(),
                        (k.get("question") or "").lower(),
                    )
                    if sc > best_sc:
                        best_sc = sc
                        best = k
                if best is None:
                    continue
                pm_p = float(pm.get("probability") or 0)
                kl_p = float(best.get("probability") or 0)
                spread = abs(pm_p - kl_p)
                if spread > 0.05:
                    alerts.append(
                        ArbitrageAlert(
                            kind="cross_platform",
                            market_a=pm,
                            market_b=best,
                            description=f"cross-platform spread {spread:.2%} ({fp})",
                            inconsistency=spread,
                            suggested_bet=None,
                            ev_estimate=spread * 0.5,
                        )
                    )
        if alerts:
            logger.info("Cross-platform arb candidates: {}", len(alerts))
        return alerts

    def find_correlated_arb(self, markets: list[dict]) -> list[ArbitrageAlert]:
        alerts: list[ArbitrageAlert] = []
        btc = [m for m in markets if "btc" in (m.get("theme") or "") or "bitcoin" in (m.get("question") or "").lower()]
        alerts.extend(_btc_ladder_check(btc))
        alerts.extend(_mutually_exclusive_three_way(markets))
        return alerts


def _btc_ladder_check(btc_markets: list[dict]) -> list[ArbitrageAlert]:
    out: list[ArbitrageAlert] = []

    def extract_thresh(q: str) -> float | None:
        q = q.lower()
        m = re.search(r"\$?\s*(\d+)\s*k", q)
        if m:
            return float(m.group(1))
        return None

    scored: list[tuple[float, dict]] = []
    for m in btc_markets:
        t = extract_thresh(m.get("question") or "")
        if t:
            scored.append((t, m))
    scored.sort(key=lambda x: x[0])
    for i in range(len(scored) - 1):
        t1, m1 = scored[i]
        t2, m2 = scored[i + 1]
        if t1 >= t2:
            continue
        p_low, p_high = float(m1.get("probability") or 0), float(m2.get("probability") or 0)
        if p_high > p_low + 0.06:
            inc = p_high - p_low
            out.append(
                ArbitrageAlert(
                    kind="correlated",
                    market_a=m1,
                    market_b=m2,
                    description=f"BTC ladder invalid: P({t2}k)={p_high:.2f} > P({t1}k)={p_low:.2f}",
                    inconsistency=inc,
                    suggested_bet="NO on higher threshold market" if p_high > p_low else None,
                    ev_estimate=inc,
                )
            )
    return out


def _mutually_exclusive_three_way(markets: list[dict]) -> list[ArbitrageAlert]:
    """Detect simple 3-way same-event overround from title similarity cluster — heuristic."""
    out: list[ArbitrageAlert] = []
    poli = [
        m
        for m in markets
        if (m.get("theme") or "").startswith("elections")
        or "president" in (m.get("question") or "").lower()
    ]
    if len(poli) < 3:
        return out
    probs = sorted([float(m.get("probability") or 0) for m in poli[:8]])
    if len(probs) >= 3:
        top3 = probs[-3:]
        s = sum(top3)
        if s > 1.0 + 0.06:
            inc = s - 1.0
            out.append(
                ArbitrageAlert(
                    kind="correlated",
                    market_a=None,
                    market_b=None,
                    description=f"Mutually exclusive cluster sum {s:.2f} > 1",
                    inconsistency=inc,
                    suggested_bet=None,
                    ev_estimate=inc,
                )
            )
    return out


def arb_to_opportunity(a: ArbitrageAlert) -> dict | None:
    m = a.market_a or a.market_b
    if not m:
        return {
            "signal_type": "arbitrage",
            "market_id": None,
            "platform": None,
            "question": a.description,
            "category": None,
            "theme": None,
            "expected_bet": "MANUAL",
            "ev_estimate": a.ev_estimate,
            "confidence": "ok",
            "probability": None,
            "liquidity": None,
            "volume_24h": None,
            "spread": None,
            "low_confidence": True,
            "divergence_from_baseline": a.inconsistency,
            "relevance_score": 0.0,
            "detail": {"kind": a.kind, "description": a.description},
        }
    return {
        "signal_type": "arbitrage",
        "market_id": m.get("_db_id"),
        "platform": m.get("platform"),
        "question": m.get("question"),
        "category": m.get("category"),
        "theme": m.get("theme"),
        "expected_bet": a.suggested_bet or "MANUAL",
        "ev_estimate": a.ev_estimate,
        "confidence": "ok",
        "probability": m.get("probability"),
        "liquidity": m.get("liquidity"),
        "volume_24h": m.get("volume_24h"),
        "spread": m.get("spread"),
        "low_confidence": m.get("low_confidence"),
        "divergence_from_baseline": a.inconsistency,
        "relevance_score": 0.0,
        "detail": {
            "kind": a.kind,
            "description": a.description,
            "pair": (
                (a.market_a or {}).get("question"),
                (a.market_b or {}).get("question"),
            ),
        },
    }
