from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Any

import requests
from loguru import logger
from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Scheduled events reference data
# ---------------------------------------------------------------------------

SCHEDULED_EVENTS: list[dict] = [
    # FOMC meetings 2025-2026
    {"name": "FOMC Meeting", "date": dt.date(2025, 1, 29), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2025, 3, 19), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2025, 5, 7),  "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2025, 6, 18), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2025, 7, 30), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2025, 9, 17), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2025, 10, 29), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2025, 12, 10), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2026, 1, 28), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    {"name": "FOMC Meeting", "date": dt.date(2026, 3, 18), "keywords": ["fed", "fomc", "interest rate", "federal reserve"]},
    # US elections
    {"name": "US Midterm Elections", "date": dt.date(2026, 11, 3), "keywords": ["election", "senate", "house", "congress", "midterm", "republican", "democrat"]},
    # Super Bowl
    {"name": "Super Bowl LIX", "date": dt.date(2025, 2, 9),  "keywords": ["super bowl", "nfl", "championship", "football"]},
    {"name": "Super Bowl LX",  "date": dt.date(2026, 2, 8),  "keywords": ["super bowl", "nfl", "championship", "football"]},
    # NBA Finals
    {"name": "NBA Finals 2025", "date": dt.date(2025, 6, 5),  "keywords": ["nba finals", "nba championship", "basketball finals"]},
    {"name": "NBA Finals 2026", "date": dt.date(2026, 6, 4),  "keywords": ["nba finals", "nba championship", "basketball finals"]},
    # World Cup
    {"name": "FIFA World Cup 2026", "date": dt.date(2026, 6, 11), "keywords": ["world cup", "fifa", "soccer world", "football world"]},
    # CPI releases (approximate monthly)
    {"name": "CPI Release", "date": dt.date(2025, 4, 10),  "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2025, 5, 13),  "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2025, 6, 11),  "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2025, 7, 11),  "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2025, 8, 12),  "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2025, 9, 10),  "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2025, 10, 15), "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2025, 11, 12), "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2025, 12, 10), "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2026, 1, 14),  "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2026, 2, 11),  "keywords": ["cpi", "inflation", "consumer price"]},
    {"name": "CPI Release", "date": dt.date(2026, 3, 11),  "keywords": ["cpi", "inflation", "consumer price"]},
]

# ---------------------------------------------------------------------------
# Stop words for entity extraction
# ---------------------------------------------------------------------------

_ENTITY_STOP_WORDS: frozenset[str] = frozenset([
    "This", "That", "What", "Will", "Does", "When", "Who", "Which",
    "With", "From", "Than", "Have", "Been", "Were", "They", "Their",
])


# ---------------------------------------------------------------------------
# Signal 1: Related Market Divergence
# ---------------------------------------------------------------------------

def find_related_divergence(markets: list[dict], min_gap: float = 0.25) -> list[dict]:
    """
    Find pairs of markets with similar questions but divergent probabilities.
    Uses rapidfuzz token_set_ratio for fuzzy matching.
    """
    if not markets:
        return []

    try:
        # Filter to markets with valid probabilities in the tradeable range
        valid: list[dict] = [
            m for m in markets
            if m.get("probability") is not None
            and 0.05 <= float(m["probability"]) <= 0.95
        ]

        # Cap at 200 markets to avoid O(n²) blowup
        if len(valid) > 200:
            valid = valid[:200]

        signals: list[dict] = []

        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                m_a = valid[i]
                m_b = valid[j]

                q_a = str(m_a.get("question", ""))
                q_b = str(m_b.get("question", ""))
                if not q_a or not q_b:
                    continue

                # Only compare markets in the same category/theme if available
                cat_a = m_a.get("category") or m_a.get("theme")
                cat_b = m_b.get("category") or m_b.get("theme")
                if cat_a and cat_b and cat_a != cat_b:
                    continue

                try:
                    similarity = fuzz.token_set_ratio(q_a, q_b)
                except Exception:
                    continue

                if similarity <= 70:
                    continue

                prob_a = float(m_a["probability"])
                prob_b = float(m_b["probability"])
                gap = abs(prob_a - prob_b)

                if gap <= min_gap:
                    continue

                # Lower-probability market is the one to bet up
                if prob_a <= prob_b:
                    underdog, other = m_a, m_b
                    underdog_prob, other_prob = prob_a, prob_b
                else:
                    underdog, other = m_b, m_a
                    underdog_prob, other_prob = prob_b, prob_a

                ev_estimate = (gap - 0.05) * 0.5

                signals.append({
                    "signal_type": "related_divergence",
                    "market_id": underdog.get("_db_id"),
                    "question": underdog.get("question", ""),
                    "platform": underdog.get("platform", ""),
                    "category": underdog.get("category"),
                    "theme": underdog.get("theme"),
                    "probability": underdog_prob,
                    "liquidity": underdog.get("liquidity"),
                    "ev_estimate": round(ev_estimate, 4),
                    "expected_bet": "YES",
                    "detail": {
                        "matched_question": other.get("question", ""),
                        "gap": round(gap, 4),
                        "similarity": similarity,
                    },
                    "confidence": "ok",
                })

        return signals

    except Exception as e:
        logger.warning("find_related_divergence error: {}", e)
        return []


# ---------------------------------------------------------------------------
# Signal 2: Scheduled Event Proximity
# ---------------------------------------------------------------------------

def find_scheduled_proximity(
    markets: list[dict],
    velocity_market_ids: set[int] | None = None,
    days_window: int = 7,
) -> list[dict]:
    """
    Find markets whose questions relate to a known upcoming scheduled event
    within days_window days.
    """
    if not markets:
        return []

    if velocity_market_ids is None:
        velocity_market_ids = set()

    today = dt.date.today()
    signals: list[dict] = []

    try:
        for market in markets:
            question_lower = str(market.get("question", "")).lower()
            if not question_lower:
                continue

            for event in SCHEDULED_EVENTS:
                event_date = event["date"]
                days_until = (event_date - today).days

                if not (0 <= days_until <= days_window):
                    continue

                keywords: list[str] = event.get("keywords", [])
                if not any(kw in question_lower for kw in keywords):
                    continue

                db_id = market.get("_db_id")
                velocity_boosted = bool(db_id is not None and int(db_id) in velocity_market_ids) if db_id is not None else False
                ev_estimate = 0.14 if velocity_boosted else 0.08

                prob = market.get("probability")
                try:
                    prob_f = float(prob) if prob is not None else 0.5
                except (TypeError, ValueError):
                    prob_f = 0.5

                signals.append({
                    "signal_type": "scheduled_proximity",
                    "market_id": db_id,
                    "question": market.get("question", ""),
                    "platform": market.get("platform", ""),
                    "category": market.get("category"),
                    "theme": market.get("theme"),
                    "probability": prob,
                    "liquidity": market.get("liquidity"),
                    "ev_estimate": ev_estimate,
                    "expected_bet": "YES" if prob_f < 0.5 else "NO",
                    "detail": {
                        "event_name": event["name"],
                        "days_until": days_until,
                        "velocity_boosted": velocity_boosted,
                    },
                    "confidence": "ok",
                })
                # Match at most one event per market to avoid duplicates
                break

    except Exception as e:
        logger.warning("find_scheduled_proximity error: {}", e)
        return signals

    return signals


# ---------------------------------------------------------------------------
# Signal 3: Liquidity Thin Spot
# ---------------------------------------------------------------------------

def find_thin_liquidity(markets: list[dict]) -> list[dict]:
    """
    Find Polymarket markets with thin liquidity (< 5000) AND wide spread or
    low_confidence flag. These are high-variance opportunities.
    """
    if not markets:
        return []

    signals: list[dict] = []

    try:
        for market in markets:
            if market.get("platform") != "polymarket":
                continue

            liquidity = market.get("liquidity")
            try:
                liquidity_f = float(liquidity) if liquidity is not None else 0.0
            except (TypeError, ValueError):
                liquidity_f = 0.0

            if liquidity_f >= 5000:
                continue

            spread = market.get("spread")
            try:
                spread_f = float(spread) if spread is not None else 0.0
            except (TypeError, ValueError):
                spread_f = 0.0

            low_confidence = bool(market.get("low_confidence", False))

            if not (spread_f > 0.10 or low_confidence):
                continue

            prob = market.get("probability")
            try:
                prob_f = float(prob) if prob is not None else 0.5
            except (TypeError, ValueError):
                prob_f = 0.5

            signals.append({
                "signal_type": "thin_liquidity",
                "market_id": market.get("_db_id"),
                "question": market.get("question", ""),
                "platform": market.get("platform", ""),
                "category": market.get("category"),
                "theme": market.get("theme"),
                "probability": prob,
                "liquidity": liquidity,
                "ev_estimate": 0.05,
                "expected_bet": "YES" if prob_f < 0.5 else "NO",
                "detail": {
                    "spread": spread_f,
                    "liquidity": liquidity_f,
                    "note": "thin market — high variance",
                },
                "confidence": "low",
            })

    except Exception as e:
        logger.warning("find_thin_liquidity error: {}", e)
        return signals

    return signals


# ---------------------------------------------------------------------------
# Signal 4: Cross-Category Momentum
# ---------------------------------------------------------------------------

_TITLE_CASE_RE = re.compile(r"\b([A-Z][a-z]{3,})\b")


def _extract_entities(question: str) -> list[str]:
    """Extract title-cased words of length >= 4, filtering stop words."""
    return [
        word for word in _TITLE_CASE_RE.findall(question)
        if word not in _ENTITY_STOP_WORDS
    ]


def find_cross_category_momentum(markets: list[dict]) -> list[dict]:
    """
    Find entities (proper nouns) that appear across 3+ markets spanning 2+
    different categories/themes. Select the most extreme-probability market
    per entity as the signal candidate.
    """
    if not markets:
        return []

    signals: list[dict] = []

    try:
        # Build entity -> list of markets mapping
        entity_markets: dict[str, list[dict]] = defaultdict(list)

        for market in markets:
            question = str(market.get("question", ""))
            entities = _extract_entities(question)
            for entity in set(entities):  # deduplicate within market
                entity_markets[entity].append(market)

        for entity, entity_mkt_list in entity_markets.items():
            if len(entity_mkt_list) < 3:
                continue

            # Collect unique categories/themes
            categories: set[str] = set()
            for m in entity_mkt_list:
                cat = m.get("category") or m.get("theme") or ""
                if cat:
                    categories.add(str(cat))

            if len(categories) < 2:
                continue

            # Select market with most extreme probability from 0.5
            best_market: dict | None = None
            best_distance = -1.0

            for m in entity_mkt_list:
                prob = m.get("probability")
                try:
                    prob_f = float(prob) if prob is not None else 0.5
                except (TypeError, ValueError):
                    prob_f = 0.5
                distance = abs(prob_f - 0.5)
                if distance > best_distance:
                    best_distance = distance
                    best_market = m

            if best_market is None:
                continue

            prob = best_market.get("probability")
            try:
                prob_f = float(prob) if prob is not None else 0.5
            except (TypeError, ValueError):
                prob_f = 0.5

            signals.append({
                "signal_type": "cross_category_momentum",
                "market_id": best_market.get("_db_id"),
                "question": best_market.get("question", ""),
                "platform": best_market.get("platform", ""),
                "category": best_market.get("category"),
                "theme": best_market.get("theme"),
                "probability": prob,
                "liquidity": best_market.get("liquidity"),
                "ev_estimate": 0.06,
                "expected_bet": "YES" if prob_f < 0.5 else "NO",
                "detail": {
                    "entity": entity,
                    "market_count": len(entity_mkt_list),
                    "category_count": len(categories),
                    "categories": sorted(categories),
                },
                "confidence": "ok",
            })

    except Exception as e:
        logger.warning("find_cross_category_momentum error: {}", e)
        return signals

    return signals


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "find_related_divergence",
    "find_scheduled_proximity",
    "find_thin_liquidity",
    "find_cross_category_momentum",
    "SCHEDULED_EVENTS",
]
