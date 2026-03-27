from __future__ import annotations

import csv
import io
import os
import datetime as dt

import requests
from dotenv import load_dotenv
from loguru import logger
from sqlalchemy.orm import Session

from engines.prediction_markets.storage.models import StatisticalBaseline

load_dotenv()


class MacroBaselineClient:
    def __init__(self, session: Session):
        self.session = session
        self.fred_key = os.environ.get("FRED_API_KEY", "")

    def get_fed_cut_probability(self) -> float | None:
        """Rough proxy: use last fed funds level; real FedWatch scrape can replace this."""
        if not self.fred_key:
            logger.debug("FRED_API_KEY not set; using neutral 0.5 for fed baseline proxy")
            return 0.5
        try:
            url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
            r = requests.get(url, params={"id": "FEDFUNDS"}, timeout=30)
            r.raise_for_status()
            rows = list(csv.reader(io.StringIO(r.text)))
            if len(rows) < 2:
                return None
            last = float(rows[-1][1])
            # Map level to a soft probability of "cut soon" — placeholder heuristic
            if last >= 5.5:
                return 0.35
            if last <= 2.0:
                return 0.65
            return 0.5
        except Exception as e:
            logger.warning("FRED fetch failed: {}", e)
            return None

    def get_election_poll_average(self, candidate_or_party: str) -> float | None:
        """Placeholder — wire to real polling API or scrape when available."""
        _ = candidate_or_party
        return None

    def save_baseline(
        self,
        *,
        market_id: int | None,
        source_name: str,
        baseline_probability: float,
        baseline_type: str,
        notes: str | None = None,
        theme_key: str | None = None,
    ) -> StatisticalBaseline:
        row = StatisticalBaseline(
            market_id=market_id,
            source_name=source_name,
            baseline_probability=baseline_probability,
            baseline_type=baseline_type,
            fetched_at=dt.datetime.now(dt.UTC),
            notes=notes,
            theme_key=theme_key,
        )
        self.session.add(row)
        return row

    def seed_theme_baselines(self) -> None:
        """Ensure at least one baseline row per major theme for outlier testing."""
        themes = [
            ("fed_macro", "fred_proxy", self.get_fed_cut_probability() or 0.5, "futures"),
            ("sports_nfl", "odds_api_placeholder", 0.52, "vegas"),
            ("btc_price", "deriv_placeholder", 0.45, "model"),
        ]
        for theme_key, src, prob, btype in themes:
            self.save_baseline(
                market_id=None,
                source_name=src,
                baseline_probability=prob,
                baseline_type=btype,
                notes="theme-level seed",
                theme_key=theme_key,
            )
