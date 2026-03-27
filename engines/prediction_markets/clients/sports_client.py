from __future__ import annotations

import os

import requests
from dotenv import load_dotenv
from loguru import logger
from rapidfuzz import fuzz

load_dotenv()

SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "epl": "soccer_epl",
}


class SportsDataClient:
    def __init__(self):
        self.api_key = os.environ.get("ODDS_API_KEY", "")

    def _get_odds(self, sport_key: str) -> list[dict]:
        if not self.api_key:
            logger.debug("ODDS_API_KEY not set")
            return []
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        params = {"apiKey": self.api_key, "regions": "us", "markets": "h2h", "oddsFormat": "decimal"}
        try:
            r = requests.get(url, params=params, timeout=45)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("Odds API {} failed: {}", sport_key, e)
            return []

    @staticmethod
    def convert_odds_to_probability(decimal_odds_a: float, decimal_odds_b: float) -> tuple[float, float]:
        ia = 1.0 / decimal_odds_a if decimal_odds_a else 0.0
        ib = 1.0 / decimal_odds_b if decimal_odds_b else 0.0
        s = ia + ib
        if s <= 0:
            return 0.5, 0.5
        return ia / s, ib / s

    def get_game_odds(self, sport: str, team_a: str, team_b: str) -> dict | None:
        sk = SPORT_KEYS.get(sport.lower())
        if not sk:
            return None
        events = self._get_odds(sk)
        best = None
        best_score = 75
        for ev in events:
            home = ev.get("home_team") or ""
            away = ev.get("away_team") or ""
            ha = fuzz.token_set_ratio(team_a.lower(), home.lower()) + fuzz.token_set_ratio(team_b.lower(), away.lower())
            ah = fuzz.token_set_ratio(team_a.lower(), away.lower()) + fuzz.token_set_ratio(team_b.lower(), home.lower())
            sc = max(ha, ah)
            if sc > best_score:
                book = (ev.get("bookmakers") or [{}])[0]
                market = next((m for m in book.get("markets", []) if m.get("key") == "h2h"), None)
                if not market:
                    continue
                outcomes = market.get("outcomes") or []
                if len(outcomes) < 2:
                    continue
                o1, o2 = float(outcomes[0]["price"]), float(outcomes[1]["price"])
                p1, p2 = self.convert_odds_to_probability(o1, o2)
                best = {"team_a_prob": p1, "team_b_prob": p2, "event": ev}
                best_score = sc
        return best
