from __future__ import annotations

import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from engines.prediction_markets.storage.models import (
    Market,
    NewsArticle,
    NewsMarketLink,
    PaperBet,
    ProbabilityReading,
    StatisticalBaseline,
)


class KnowledgeBase:
    def __init__(self, session: Session):
        self.session = session

    def get_market_by_platform_id(self, platform: str, market_id: str) -> Market | None:
        return self.session.scalars(
            select(Market).where(Market.platform == platform, Market.market_id == market_id).limit(1)
        ).first()

    def active_markets(self) -> list[Market]:
        return list(self.session.scalars(select(Market).where(Market.is_active == True)).all())

    def latest_reading(self, market_db_id: int) -> ProbabilityReading | None:
        return self.session.scalars(
            select(ProbabilityReading)
            .where(ProbabilityReading.market_id == market_db_id)
            .order_by(ProbabilityReading.timestamp.desc())
            .limit(1)
        ).first()

    def readings_since(self, market_db_id: int, since: dt.datetime) -> list[ProbabilityReading]:
        return list(
            self.session.scalars(
                select(ProbabilityReading)
                .where(
                    ProbabilityReading.market_id == market_db_id,
                    ProbabilityReading.timestamp >= since,
                )
                .order_by(ProbabilityReading.timestamp.asc())
            ).all()
        )

    def recent_news(self, limit: int = 20) -> list[NewsArticle]:
        return list(
            self.session.scalars(select(NewsArticle).order_by(NewsArticle.fetched_at.desc()).limit(limit)).all()
        )

    def baselines_for_market(self, market_db_id: int) -> list[StatisticalBaseline]:
        return list(
            self.session.scalars(
                select(StatisticalBaseline).where(StatisticalBaseline.market_id == market_db_id)
            ).all()
        )

    def open_paper_bets(self) -> list[PaperBet]:
        return list(self.session.scalars(select(PaperBet).where(PaperBet.outcome == "PENDING")).all())

    def news_links_for_article(self, article_id: int) -> list[NewsMarketLink]:
        return list(
            self.session.scalars(select(NewsMarketLink).where(NewsMarketLink.article_id == article_id)).all()
        )

    def count_news_today(self) -> int:
        start = dt.datetime.now(dt.UTC).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        return int(
            self.session.scalar(
                select(func.count()).select_from(NewsArticle).where(NewsArticle.fetched_at >= start)
            )
            or 0
        )
