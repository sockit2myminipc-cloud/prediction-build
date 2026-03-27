from __future__ import annotations

import datetime as dt

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from engines.prediction_markets.clients.kalshi_client import KalshiClient
from engines.prediction_markets.clients.polymarket_client import PolymarketClient
from engines.prediction_markets.storage.models import Market, PaperBet, ProbabilityReading


class PaperBettor:
    def __init__(self, session: Session, polymarket: PolymarketClient | None = None, kalshi: KalshiClient | None = None):
        self.session = session
        self.polymarket = polymarket or PolymarketClient()
        self.kalshi = kalshi or KalshiClient()

    def place_paper_bet(
        self,
        market_db_id: int,
        direction: str,
        trigger_type: str,
        trigger_detail: dict | None,
        stake: float = 1.0,
    ) -> int | None:
        row = self.session.get(Market, market_db_id)
        if not row:
            return None
        pr = self.session.scalars(
            select(ProbabilityReading)
            .where(ProbabilityReading.market_id == market_db_id)
            .order_by(ProbabilityReading.timestamp.desc())
            .limit(1)
        ).first()
        if not pr:
            logger.warning("No probability reading for market {}", market_db_id)
            return None
        prob = float(pr.probability)
        direction = direction.upper()
        if direction == "YES":
            implied = 1.0 / prob if prob > 0 else 0.0
        else:
            implied = 1.0 / (1.0 - prob) if prob < 1 else 0.0
        bet = PaperBet(
            market_id=market_db_id,
            question=row.question,
            platform=row.platform,
            category=row.category,
            bet_direction=direction,
            probability_at_bet=prob,
            implied_odds=implied,
            stake_units=stake,
            trigger_type=trigger_type,
            trigger_detail_json=trigger_detail,
            placed_at=dt.datetime.now(dt.UTC),
            outcome="PENDING",
        )
        self.session.add(bet)
        self.session.flush()
        return bet.id

    def check_resolutions(self) -> int:
        pending = list(
            self.session.scalars(
                select(PaperBet)
                .where(PaperBet.outcome == "PENDING")
                .options(selectinload(PaperBet.market))
            ).all()
        )
        n = 0
        for bet in pending:
            m = bet.market
            end = m.end_date
            if end is not None and end.tzinfo is None:
                end = end.replace(tzinfo=dt.UTC)
            if not end or end > dt.datetime.now(dt.UTC):
                continue
            final = self._fetch_final_probability(m)
            if final is None:
                continue
            outcome, pnl = self._resolve_pnl(bet, final)
            bet.outcome = outcome
            bet.pnl_units = pnl
            bet.resolved_at = dt.datetime.now(dt.UTC)
            n += 1
        if n:
            self.session.commit()
        return n

    def _fetch_final_probability(self, m: Market) -> float | None:
        if m.platform == "polymarket":
            raw = self.polymarket.get_market_by_id(m.market_id)
            if not raw:
                return None
            p = PolymarketClient.extract_probability(raw)
            return p
        if m.platform == "kalshi":
            markets = self.kalshi.get_all_markets(limit=500)
            for raw in markets:
                if raw.get("ticker") == m.market_id:
                    norm = KalshiClient.normalize_market(raw)
                    return float(norm.get("probability") or 0)
        return None

    @staticmethod
    def _resolve_pnl(bet: PaperBet, final_prob: float) -> tuple[str, float]:
        if final_prob >= 0.95:
            market_yes = True
        elif final_prob <= 0.05:
            market_yes = False
        else:
            return "VOID", 0.0
        won = (bet.bet_direction == "YES" and market_yes) or (bet.bet_direction == "NO" and not market_yes)
        stake = float(bet.stake_units)
        if won:
            return "WIN", stake * (float(bet.implied_odds) - 1.0)
        return "LOSS", -stake

    def get_stats(
        self,
        category: str | None = None,
        signal_type: str | None = None,
        days: int = 90,
    ) -> dict:
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
        q = select(PaperBet).where(
            PaperBet.resolved_at.isnot(None),
            PaperBet.resolved_at >= since,
            PaperBet.outcome.in_(("WIN", "LOSS")),
        )
        if category:
            q = q.where(PaperBet.category == category)
        rows = list(self.session.scalars(q).all())
        if signal_type:
            rows = [r for r in rows if (r.trigger_detail_json or {}).get("signal_type") == signal_type]
        if not rows:
            return {
                "total_bets": 0,
                "win_rate": 0.0,
                "total_pnl_units": 0.0,
                "avg_ev_at_bet": 0.0,
                "roi_percent": 0.0,
                "max_drawdown_units": 0.0,
                "best_category": None,
                "best_signal_type": None,
                "avg_odds_bet": 0.0,
                "avg_odds_won": 0.0,
            }
        wins = sum(1 for r in rows if r.outcome == "WIN")
        pnls = [float(r.pnl_units or 0) for r in rows]
        evs = [float((r.trigger_detail_json or {}).get("ev_estimate") or 0) for r in rows]
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        by_cat: dict[str, list[PaperBet]] = {}
        for r in rows:
            by_cat.setdefault(r.category or "unknown", []).append(r)
        best_cat = max(by_cat, key=lambda c: sum(float(x.pnl_units or 0) for x in by_cat[c]))
        sigs: dict[str, list[PaperBet]] = {}
        for r in rows:
            st = (r.trigger_detail_json or {}).get("signal_type") or r.trigger_type
            sigs.setdefault(str(st), []).append(r)
        best_sig = max(sigs, key=lambda c: sum(float(x.pnl_units or 0) for x in sigs[c]))
        won_odds = [float(r.implied_odds) for r in rows if r.outcome == "WIN"]
        return {
            "total_bets": len(rows),
            "win_rate": wins / len(rows),
            "total_pnl_units": sum(pnls),
            "avg_ev_at_bet": sum(evs) / len(evs) if evs else 0.0,
            "roi_percent": (sum(pnls) / len(rows) * 100) if rows else 0.0,
            "max_drawdown_units": max_dd,
            "best_category": best_cat,
            "best_signal_type": best_sig,
            "avg_odds_bet": sum(float(r.implied_odds) for r in rows) / len(rows),
            "avg_odds_won": sum(won_odds) / len(won_odds) if won_odds else 0.0,
        }
