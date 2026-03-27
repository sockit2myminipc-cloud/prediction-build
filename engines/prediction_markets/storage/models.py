from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (UniqueConstraint("platform", "market_id", name="uq_platform_market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    market_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    theme: Mapped[str | None] = mapped_column(String(128), nullable=True)
    slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
    liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
    last_updated: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), onupdate=lambda: dt.datetime.now(dt.UTC)
    )

    readings: Mapped[list[ProbabilityReading]] = relationship(back_populates="market")
    paper_bets: Mapped[list[PaperBet]] = relationship(back_populates="market")
    baselines: Mapped[list[StatisticalBaseline]] = relationship(back_populates="market")


class ProbabilityReading(Base):
    __tablename__ = "probability_readings"
    __table_args__ = (
        Index("ix_prob_market_ts", "market_id", "timestamp"),
        Index("ix_prob_market_quality_ts", "market_id", "quality", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str] = mapped_column(String(16), default="ok")
    source_platform: Mapped[str] = mapped_column(String(32), nullable=False)

    market: Mapped[Market] = relationship(back_populates="readings")


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
    entities_json: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)

    links: Mapped[list[NewsMarketLink]] = relationship(back_populates="article")


class NewsMarketLink(Base):
    __tablename__ = "news_market_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id"), nullable=False)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )

    article: Mapped[NewsArticle] = relationship(back_populates="links")
    market: Mapped[Market] = relationship()


class PaperBet(Base):
    __tablename__ = "paper_bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bet_direction: Mapped[str] = mapped_column(String(8), nullable=False)
    probability_at_bet: Mapped[float] = mapped_column(Float, nullable=False)
    implied_odds: Mapped[float] = mapped_column(Float, nullable=False)
    stake_units: Mapped[float] = mapped_column(Float, default=1.0)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_detail_json: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    placed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), default="PENDING")
    pnl_units: Mapped[float | None] = mapped_column(Float, nullable=True)

    market: Mapped[Market] = relationship(back_populates="paper_bets")


class StatisticalBaseline(Base):
    __tablename__ = "statistical_baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[int | None] = mapped_column(ForeignKey("markets.id"), nullable=True)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_probability: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_type: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    theme_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    market: Mapped[Market | None] = relationship(back_populates="baselines")


class HealthLog(Base):
    __tablename__ = "health_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    markets_tracked: Mapped[int] = mapped_column(Integer, default=0)
    ok_markets: Mapped[int] = mapped_column(Integer, default=0)
    stale_markets: Mapped[int] = mapped_column(Integer, default=0)
    news_articles_today: Mapped[int] = mapped_column(Integer, default=0)
    paper_bets_open: Mapped[int] = mapped_column(Integer, default=0)
    last_signal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_set_json: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    rule_params_json: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    snapshots: Mapped[list[StrategyPerformanceSnapshot]] = relationship(back_populates="strategy_version")


class StrategyPerformanceSnapshot(Base):
    __tablename__ = "strategy_performance_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"), nullable=False)
    as_of_date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    bets_count: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_ev: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibration_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_lag_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    strategy_version: Mapped[StrategyVersion] = relationship(back_populates="snapshots")


class SignalEvent(Base):
    __tablename__ = "signal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), nullable=False)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    fired_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    theme: Mapped[str | None] = mapped_column(String(128), nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    velocity: Mapped[float | None] = mapped_column(Float, nullable=True)
    acceleration: Mapped[float | None] = mapped_column(Float, nullable=True)
    recent_news_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    baseline_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    divergence: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    signal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    trust_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(SQLiteJSON, nullable=True)


# ── Backtest tables ──────────────────────────────────────────────────────────

class HistoricalMarket(Base):
    """Resolved markets fetched for backtesting."""
    __tablename__ = "historical_markets"
    __table_args__ = (UniqueConstraint("source", "market_id", name="uq_hist_source_market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)       # polymarket / kalshi
    market_id: Mapped[str] = mapped_column(String(128), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)   # YES / NO / %
    open_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price_history: Mapped[str | None] = mapped_column(Text, nullable=True)      # JSON [{t, p}, …]
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )


class BacktestResult(Base):
    """Per-signal (and per-signal-combo) backtest performance metrics."""
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_combo: Mapped[str | None] = mapped_column(String(256), nullable=True)   # e.g. "outlier+velocity"
    hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    kelly_fraction: Mapped[str | None] = mapped_column(String(32), nullable=True)   # flat/full/half/quarter
    kelly_roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    kelly_final_bankroll: Mapped[float | None] = mapped_column(Float, nullable=True)
    kelly_max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_entry_window: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "7-14d"
    run_date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CalibrationResult(Base):
    """Per-source × category × probability-bucket calibration data."""
    __tablename__ = "calibration_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prob_bucket: Mapped[str] = mapped_column(String(32), nullable=False)     # "0-10%"
    avg_market_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_resolution_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    bias: Mapped[float | None] = mapped_column(Float, nullable=True)         # actual - market (positive = underpriced)
    run_date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MomentumResult(Base):
    """Momentum vs mean-reversion by category after velocity spikes."""
    __tablename__ = "momentum_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    window_hours: Mapped[int] = mapped_column(Integer, nullable=False)       # 24 / 48 / 168
    momentum_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_reversion_fraction: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_continuation: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    behavior: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "momentum" / "mean_reversion"
    run_date: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
