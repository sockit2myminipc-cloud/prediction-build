from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from engines.prediction_markets.storage.models import Market, ProbabilityReading


class ProbabilityTracker:
    def __init__(self, session: Session):
        self.session = session

    def upsert_market_row(
        self,
        platform: str,
        market_id: str,
        question: str,
        *,
        category: str | None,
        theme: str | None,
        slug: str | None,
        liquidity: float | None,
        volume_24h: float | None,
        end_date: dt.datetime | None,
        low_confidence: bool,
        is_active: bool = True,
    ) -> Market:
        row = self.session.scalars(
            select(Market).where(Market.platform == platform, Market.market_id == market_id).limit(1)
        ).first()
        now = dt.datetime.now(dt.UTC)
        if row:
            row.question = question
            row.category = category
            row.theme = theme
            row.slug = slug
            row.liquidity = liquidity
            row.volume_24h = volume_24h
            row.end_date = end_date
            row.low_confidence = low_confidence
            row.is_active = is_active
            row.last_updated = now
            m = row
        else:
            m = Market(
                platform=platform,
                market_id=market_id,
                question=question,
                category=category,
                theme=theme,
                slug=slug,
                liquidity=liquidity,
                volume_24h=volume_24h,
                end_date=end_date,
                low_confidence=low_confidence,
                is_active=is_active,
                first_seen=now,
                last_updated=now,
            )
            self.session.add(m)
            self.session.flush()
        return m

    def record_reading(
        self,
        market_db_id: int,
        probability: float,
        *,
        source_platform: str,
        volume: float | None = None,
        quality: str = "ok",
        at: dt.datetime | None = None,
    ) -> ProbabilityReading:
        ts = at or dt.datetime.now(dt.UTC)
        pr = ProbabilityReading(
            market_id=market_db_id,
            timestamp=ts,
            probability=probability,
            volume=volume,
            quality=quality,
            source_platform=source_platform,
        )
        self.session.add(pr)
        return pr

    def latest_probability(self, market_db_id: int) -> float | None:
        r = self.session.scalars(
            select(ProbabilityReading)
            .where(ProbabilityReading.market_id == market_db_id)
            .order_by(ProbabilityReading.timestamp.desc())
            .limit(1)
        ).first()
        return r.probability if r else None


def normalize_polymarket_row(raw: dict, pm: object) -> dict | None:
    prob = pm.extract_probability(raw) if hasattr(pm, "extract_probability") else None
    if prob is None:
        return None
    eid = str(raw.get("id", ""))
    if not eid:
        return None
    end = raw.get("endDate") or raw.get("end_date")
    end_dt = None
    if isinstance(end, str):
        try:
            end_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            end_dt = None
    vol = raw.get("volume24hr") or raw.get("volume") or 0
    liq = raw.get("liquidity") or 0
    cat = raw.get("category")
    if isinstance(cat, list):
        cat = cat[0] if cat else None
    if isinstance(cat, dict):
        cat = cat.get("name") or cat.get("slug")
    slug = raw.get("slug")
    return {
        "platform": "polymarket",
        "market_id": eid,
        "question": raw.get("question") or raw.get("title") or "",
        "probability": float(prob),
        "volume_24h": float(vol or 0),
        "liquidity": float(liq or 0),
        "end_date": end_dt,
        "category": str(cat) if cat else None,
        "slug": str(slug) if slug else None,
        "low_confidence": False,
    }
