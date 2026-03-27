from __future__ import annotations

from datetime import UTC, datetime

THEMES: dict[str, list[str]] = {
    "sports_nfl": ["nfl", "super bowl", "quarterback", "touchdown", "playoff"],
    "sports_nba": ["nba", "finals", "mvp", "basketball", "championship"],
    "sports_mlb": ["mlb", "world series", "baseball", "pitcher"],
    "sports_soccer": ["premier league", "champions league", "la liga", "world cup", "soccer"],
    "sports_combat": ["ufc", "boxing", "mma", "fight", "knockout"],
    "sports_golf": ["masters", "pga", "golf", "open championship"],
    "elections_us": ["president", "senate", "congress", "governor", "election", "vote"],
    "elections_intl": ["prime minister", "chancellor", "parliament", "referendum"],
    "btc_price": ["bitcoin", "btc", "$100k", "$120k", "price"],
    "eth_events": ["ethereum", "eth", "etf", "staking"],
    "other_crypto": ["solana", "sol", "xrp", "ripple", "altcoin", "crypto"],
    "fed_macro": ["fed", "fomc", "rate cut", "rate hike", "interest rate", "powell"],
    "cpi_macro": ["cpi", "inflation", "consumer price"],
    "recession": ["recession", "gdp", "unemployment"],
    "geopolitics": ["war", "ukraine", "russia", "china", "taiwan", "sanctions"],
    "tech_ai": ["openai", "gpt", "ai model", "artificial intelligence"],
    "entertainment": ["oscar", "grammy", "box office", "award"],
}


class MarketFilter:
    @staticmethod
    def assign_theme(question_text: str) -> str:
        q = question_text.lower()
        for theme, kws in THEMES.items():
            if any(kw in q for kw in kws):
                return theme
        return "other"

    @staticmethod
    def score_market(market_dict: dict) -> float:
        score = 0.0
        theme = market_dict.get("theme") or MarketFilter.assign_theme(market_dict.get("question", ""))
        if theme != "other":
            score += 0.3
        vol = float(market_dict.get("volume_24h") or 0)
        liq = float(market_dict.get("liquidity") or 0)
        if vol > 10000:
            score += 0.2
        if liq > 5000:
            score += 0.2
        end = market_dict.get("end_date")
        end_dt = _parse_end(end)
        if end_dt:
            days = (end_dt - datetime.now(UTC)).total_seconds() / 86400
            if days >= 3:
                score += 0.2
        if not market_dict.get("low_confidence"):
            score += 0.1
        return min(score, 1.0)

    @staticmethod
    def filter_markets(markets_list: list[dict], min_score: float = 0.3) -> list[dict]:
        scored = []
        for m in markets_list:
            if "theme" not in m:
                m = {**m, "theme": MarketFilter.assign_theme(m.get("question", ""))}
            s = MarketFilter.score_market(m)
            if s >= min_score:
                scored.append({**m, "_relevance_score": s})
        scored.sort(key=lambda x: x["_relevance_score"], reverse=True)
        return scored


def _parse_end(end: object) -> datetime | None:
    if end is None:
        return None
    if isinstance(end, datetime):
        return end if end.tzinfo else end.replace(tzinfo=UTC)
    if isinstance(end, str):
        try:
            return datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
