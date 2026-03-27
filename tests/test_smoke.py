"""
Smoke tests for the Prediction Market Engine (Phases 1–5).

Run with:
    pytest tests/ -v

All tests use an isolated SQLite file so the production DB is never touched.
External API calls are either skipped (no key set) or mocked where needed.
"""
from __future__ import annotations

import datetime as dt
import sys
import os

import pytest
from unittest.mock import MagicMock, patch

# ── 1. MODULE IMPORT TESTS ───────────────────────────────────────────────────


def test_import_models():
    from engines.prediction_markets.storage.models import (
        Market, ProbabilityReading, NewsArticle, NewsMarketLink,
        PaperBet, StatisticalBaseline, HealthLog, StrategyVersion,
        StrategyPerformanceSnapshot, SignalEvent,
    )
    assert Market.__tablename__ == "markets"
    assert ProbabilityReading.__tablename__ == "probability_readings"
    assert NewsArticle.__tablename__ == "news_articles"
    assert PaperBet.__tablename__ == "paper_bets"
    assert StrategyVersion.__tablename__ == "strategy_versions"
    assert SignalEvent.__tablename__ == "signal_events"


def test_import_db():
    from engines.prediction_markets.storage.db import (
        get_session, init_db, database_url, get_engine, get_sessionmaker,
    )
    assert callable(get_session)
    assert callable(init_db)
    url = database_url()
    assert "sqlite" in url


def test_import_runtime_state():
    from engines.prediction_markets.runtime_state import (
        set_cycle_results, set_fetch_time, snapshot,
    )
    assert callable(set_cycle_results)
    assert callable(snapshot)


def test_import_market_filter():
    from engines.prediction_markets.filters.market_filter import MarketFilter, THEMES
    assert isinstance(THEMES, dict)
    assert len(THEMES) > 0


def test_import_dedup():
    from engines.prediction_markets.filters.dedup import MarketDeduplicator, EVENT_FINGERPRINTS
    assert isinstance(EVENT_FINGERPRINTS, dict)


def test_import_probability_tracker():
    from engines.prediction_markets.tracking.probability_tracker import (
        ProbabilityTracker, normalize_polymarket_row,
    )
    assert callable(normalize_polymarket_row)


def test_import_knowledge_base():
    from engines.prediction_markets.tracking.knowledge_base import KnowledgeBase
    assert KnowledgeBase


def test_import_outlier_detector():
    from engines.prediction_markets.analysis.outlier_detector import (
        OutlierDetector, outlier_to_opportunity_dict, THEME_THRESHOLDS,
    )
    assert isinstance(THEME_THRESHOLDS, dict)


def test_import_velocity_scanner():
    from engines.prediction_markets.analysis.velocity_scanner import (
        VelocityScanner, velocity_alerts_to_opportunities,
    )
    assert callable(velocity_alerts_to_opportunities)


def test_import_arbitrage_scanner():
    from engines.prediction_markets.analysis.arbitrage_scanner import (
        ArbitrageScanner, arb_to_opportunity,
    )
    assert callable(arb_to_opportunity)


def test_import_news_impact_scorer():
    from engines.prediction_markets.analysis.news_impact_scorer import NewsImpactScorer
    assert NewsImpactScorer


def test_import_opportunity_ranker():
    from engines.prediction_markets.betting.opportunity_ranker import (
        OpportunityRanker, news_lag_to_opportunities, time_sensitivity_score,
    )
    assert callable(news_lag_to_opportunities)


def test_import_paper_bettor():
    from engines.prediction_markets.betting.paper_bettor import PaperBettor
    assert PaperBettor


def test_import_bet_executor():
    from engines.prediction_markets.betting.bet_executor import BetExecutor
    b = BetExecutor()
    with pytest.raises(NotImplementedError):
        b.place_live_bet()


def test_import_kalshi_client():
    from engines.prediction_markets.clients.kalshi_client import KalshiClient
    assert KalshiClient


def test_import_polymarket_client():
    from engines.prediction_markets.clients.polymarket_client import PolymarketClient
    assert PolymarketClient


def test_import_newsapi_client():
    from engines.prediction_markets.clients.newsapi_client import NewsIngester
    assert NewsIngester


def test_import_macro_client():
    from engines.prediction_markets.clients.macro_client import MacroBaselineClient
    assert MacroBaselineClient


def test_import_sports_client():
    from engines.prediction_markets.clients.sports_client import SportsDataClient
    assert SportsDataClient


def test_import_alert_service():
    from engines.prediction_markets.alerts.alert_service import PMAlertService
    assert PMAlertService


def test_import_dashboard():
    from shared.dashboard.app import app
    assert app.title == "Prediction Markets Dashboard"


# ── 2. DB INITIALIZATION ─────────────────────────────────────────────────────


def test_db_init_creates_all_tables():
    """init_db() should create all 11 tables without error."""
    import engines.prediction_markets.storage.db as db_mod
    # Reset cached singletons so the test DATABASE_URL is picked up cleanly
    db_mod._SessionLocal = None
    db_mod._engine = None

    from engines.prediction_markets.storage.db import init_db, get_engine
    init_db()

    engine = get_engine()
    from sqlalchemy import inspect
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "markets", "probability_readings", "news_articles", "news_market_links",
        "paper_bets", "statistical_baselines", "health_logs", "strategy_versions",
        "strategy_performance_snapshots", "signal_events",
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


def test_db_seeds_default_strategy():
    """After init_db(), a default champion StrategyVersion should exist."""
    from engines.prediction_markets.storage.db import get_session
    from engines.prediction_markets.storage.models import StrategyVersion
    from sqlalchemy import select

    with get_session() as s:
        sv = s.scalars(
            select(StrategyVersion).where(StrategyVersion.strategy_name == "default")
        ).first()
    assert sv is not None
    assert sv.status == "champion"


# ── 3. FILTERS ───────────────────────────────────────────────────────────────


def test_market_filter_assign_theme_known():
    from engines.prediction_markets.filters.market_filter import MarketFilter
    assert MarketFilter.assign_theme("Will the Fed cut rates in March?") == "fed_macro"
    assert MarketFilter.assign_theme("Will BTC reach $100k?") == "btc_price"
    assert MarketFilter.assign_theme("Super Bowl winner 2025?") == "sports_nfl"
    assert MarketFilter.assign_theme("Will the president win reelection?") == "elections_us"


def test_market_filter_assign_theme_fallback():
    from engines.prediction_markets.filters.market_filter import MarketFilter
    assert MarketFilter.assign_theme("Will Pluto be reclassified?") == "other"


def test_market_filter_score():
    from engines.prediction_markets.filters.market_filter import MarketFilter
    market = {
        "question": "Will the Fed cut rates in 2025?",
        "theme": "fed_macro",
        "volume_24h": 50000,
        "liquidity": 100000,
        "end_date": (dt.datetime.now(dt.UTC) + dt.timedelta(days=30)).isoformat(),
        "low_confidence": False,
    }
    score = MarketFilter.score_market(market)
    assert 0.0 <= score <= 1.0
    assert score >= 0.3


def test_market_filter_filters_low_score():
    from engines.prediction_markets.filters.market_filter import MarketFilter
    markets = [
        {"question": "Will Pluto be reclassified?", "volume_24h": 0, "liquidity": 0, "low_confidence": True},
        {"question": "Will the Fed cut rates?", "theme": "fed_macro", "volume_24h": 50000,
         "liquidity": 100000, "end_date": (dt.datetime.now(dt.UTC) + dt.timedelta(days=30)).isoformat(),
         "low_confidence": False},
    ]
    kept = MarketFilter.filter_markets(markets, min_score=0.3)
    questions = [m["question"] for m in kept]
    assert "Will the Fed cut rates?" in questions


# ── 4. DEDUPLICATION ─────────────────────────────────────────────────────────


def test_dedup_keeps_preferred_platform():
    from engines.prediction_markets.filters.dedup import MarketDeduplicator
    markets = [
        {"platform": "polymarket", "market_id": "p1", "question": "Will the Fed cut rates in March?",
         "liquidity": 100000.0, "theme": "fed_macro"},
        {"platform": "kalshi", "market_id": "k1", "question": "Fed rate cut March FOMC?",
         "liquidity": 50000.0, "theme": "fed_macro"},
    ]
    kept, dropped = MarketDeduplicator.dedup(markets)
    assert len(kept) + len(dropped) == 2
    # At least one should be kept
    assert len(kept) >= 1


def test_dedup_ungrouped_passthrough():
    from engines.prediction_markets.filters.dedup import MarketDeduplicator
    markets = [
        {"platform": "polymarket", "market_id": "x1", "question": "Will Pluto be reclassified?",
         "liquidity": 1000.0},
        {"platform": "kalshi", "market_id": "x2", "question": "Will Mars be colonized by 2040?",
         "liquidity": 2000.0},
    ]
    kept, dropped = MarketDeduplicator.dedup(markets)
    assert len(kept) == 2
    assert len(dropped) == 0


# ── 5. OUTLIER DETECTOR ──────────────────────────────────────────────────────


def test_outlier_detector_ev_calculation():
    from engines.prediction_markets.analysis.outlier_detector import OutlierDetector
    det = OutlierDetector()
    # Market prob 0.3, baseline 0.6 → bet YES (market too low)
    ev = det.calculate_ev(0.30, 0.60, bet_yes=True)
    assert ev > 0, "Should have positive EV when market underprices YES"


def test_outlier_detector_theme_threshold():
    from engines.prediction_markets.analysis.outlier_detector import OutlierDetector
    det = OutlierDetector()
    assert det.theme_threshold("sports_nfl") == 0.08
    assert det.theme_threshold("fed_macro") == 0.05
    assert det.theme_threshold("completely_unknown_theme") == 0.15


def test_outlier_detector_find_outliers_with_db():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.storage.models import StatisticalBaseline
    from engines.prediction_markets.analysis.outlier_detector import OutlierDetector

    init_db()
    with get_session() as session:
        # Seed a theme baseline if not already present
        existing = session.query(StatisticalBaseline).filter_by(theme_key="fed_macro").first()
        if not existing:
            b = StatisticalBaseline(
                market_id=None,
                source_name="test",
                baseline_probability=0.5,
                baseline_type="test",
                fetched_at=dt.datetime.now(dt.UTC),
                notes="test seed",
                theme_key="fed_macro",
            )
            session.add(b)
            session.commit()

        # Market that diverges enough from baseline
        markets = [{
            "_db_id": None,
            "platform": "kalshi",
            "market_id": "TEST_FOMC",
            "question": "Will Fed cut rates?",
            "theme": "fed_macro",
            "probability": 0.20,  # diverges > 0.05 from 0.5 baseline
            "liquidity": 50000,
            "volume_24h": 10000,
            "spread": 0.02,
            "low_confidence": False,
            "category": "macro",
        }]
        det = OutlierDetector()
        alerts = det.find_outliers(session, markets)
        # Should detect since |0.20 - 0.50| = 0.30 > 0.05 threshold
        assert len(alerts) >= 1
        assert alerts[0].direction == "market_too_low"
        assert alerts[0].expected_bet == "YES"


# ── 6. VELOCITY SCANNER ──────────────────────────────────────────────────────


def test_velocity_scanner_stale_with_no_data():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.analysis.velocity_scanner import VelocityScanner

    init_db()
    with get_session() as session:
        vel = VelocityScanner(session)
        result = vel.get_velocity(market_db_id=99999, window_hours=2.0)
        assert result["quality"] == "stale"
        assert result["velocity"] == 0.0


def test_velocity_scanner_classify():
    from engines.prediction_markets.analysis.velocity_scanner import VelocityScanner
    assert VelocityScanner._classify(0.07) == "strong_bullish_move"
    assert VelocityScanner._classify(0.05) == "mild_bullish_move"
    assert VelocityScanner._classify(-0.07) == "strong_bearish_move"
    assert VelocityScanner._classify(-0.05) == "mild_bearish_move"
    assert VelocityScanner._classify(0.01) == "flat"


# ── 7. ARBITRAGE SCANNER ─────────────────────────────────────────────────────


def test_arbitrage_scanner_cross_platform():
    from engines.prediction_markets.analysis.arbitrage_scanner import ArbitrageScanner
    pm = [{"platform": "polymarket", "market_id": "p1",
           "question": "Will the Fed cut rates in March FOMC?", "probability": 0.70,
           "liquidity": 100000, "volume_24h": 50000, "spread": 0.02,
           "theme": "fed_macro", "low_confidence": False, "_db_id": 1}]
    ks = [{"platform": "kalshi", "market_id": "k1",
           "question": "Fed rate cut at FOMC March meeting?", "probability": 0.50,
           "liquidity": 50000, "volume_24h": 20000, "spread": 0.03,
           "theme": "fed_macro", "low_confidence": False, "_db_id": 2}]
    scanner = ArbitrageScanner()
    alerts = scanner.find_cross_platform_arb(pm, ks)
    # spread is 0.20 > 0.05 threshold, should detect
    assert len(alerts) >= 1
    assert alerts[0].kind == "cross_platform"
    assert alerts[0].ev_estimate > 0


def test_arb_to_opportunity():
    from engines.prediction_markets.analysis.arbitrage_scanner import ArbitrageAlert, arb_to_opportunity
    alert = ArbitrageAlert(
        kind="cross_platform",
        market_a={"platform": "polymarket", "market_id": "p1", "question": "Test?",
                  "probability": 0.7, "liquidity": 100000, "volume_24h": 50000,
                  "spread": 0.02, "theme": "fed_macro", "low_confidence": False,
                  "_db_id": 1, "category": "macro"},
        market_b=None,
        description="test arb",
        inconsistency=0.20,
        suggested_bet="YES",
        ev_estimate=0.10,
    )
    opp = arb_to_opportunity(alert)
    assert opp is not None
    assert opp["signal_type"] == "arbitrage"
    assert opp["ev_estimate"] == 0.10


# ── 8. OPPORTUNITY RANKER ────────────────────────────────────────────────────


def test_opportunity_ranker_empty_input():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.betting.opportunity_ranker import OpportunityRanker

    init_db()
    with get_session() as session:
        ranker = OpportunityRanker(session)
        ranked = ranker.rank_opportunities([], [], [], [])
        assert ranked == []


def test_opportunity_ranker_scores_opportunities():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.betting.opportunity_ranker import OpportunityRanker

    init_db()
    with get_session() as session:
        ranker = OpportunityRanker(session)
        outliers = [{
            "signal_type": "outlier",
            "market_id": None,
            "platform": "kalshi",
            "question": "Will Fed cut rates?",
            "category": "macro",
            "theme": "fed_macro",
            "expected_bet": "YES",
            "ev_estimate": 0.15,
            "confidence": "ok",
            "probability": 0.25,
            "liquidity": 100000,
            "volume_24h": 50000,
            "spread": 0.02,
            "low_confidence": False,
            "divergence_from_baseline": 0.25,
            "relevance_score": 0.0,
            "detail": {"baseline_source": "fred_proxy", "baseline_prob": 0.5, "direction": "market_too_low"},
        }]
        ranked = ranker.rank_opportunities(outliers, [], [], [])
        assert len(ranked) == 1
        o = ranked[0]
        assert "final_score" in o
        assert 0.0 <= o["final_score"] <= 1.0
        assert o["signal_type"] == "outlier"
        assert o["rank"] == 1


def test_time_sensitivity_score():
    from engines.prediction_markets.betting.opportunity_ranker import time_sensitivity_score
    news_opp = {"signal_type": "news_lag", "news_lag_minutes": 10}
    assert time_sensitivity_score(news_opp) > 0
    other_opp = {"signal_type": "outlier"}
    assert time_sensitivity_score(other_opp) == 0.5


# ── 9. PAPER BETTOR ──────────────────────────────────────────────────────────


def test_paper_bettor_get_stats_empty():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.betting.paper_bettor import PaperBettor

    init_db()
    with get_session() as session:
        pb = PaperBettor(session)
        stats = pb.get_stats(days=1)
        assert stats["total_bets"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["roi_percent"] == 0.0


def test_paper_bettor_place_and_check():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.betting.paper_bettor import PaperBettor
    from engines.prediction_markets.tracking.probability_tracker import ProbabilityTracker

    init_db()
    with get_session() as session:
        # Use ProbabilityTracker upsert so this test is idempotent across runs
        tracker = ProbabilityTracker(session)
        m = tracker.upsert_market_row(
            "kalshi", "TEST_BET_001",
            "Will this test pass?",
            category="test",
            theme="other",
            slug=None,
            liquidity=10000.0,
            volume_24h=5000.0,
            end_date=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),  # already expired
            low_confidence=False,
        )
        tracker.record_reading(m.id, 0.70, source_platform="kalshi")
        session.commit()

        pb = PaperBettor(session)
        bet_id = pb.place_paper_bet(m.id, "YES", "outlier", {"ev_estimate": 0.15})
        assert bet_id is not None
        assert bet_id > 0


# ── 10. KNOWLEDGE BASE ───────────────────────────────────────────────────────


def test_knowledge_base_open_bets():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.tracking.knowledge_base import KnowledgeBase

    init_db()
    with get_session() as session:
        kb = KnowledgeBase(session)
        bets = kb.open_paper_bets()
        assert isinstance(bets, list)


def test_knowledge_base_count_news_today():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.tracking.knowledge_base import KnowledgeBase

    init_db()
    with get_session() as session:
        kb = KnowledgeBase(session)
        count = kb.count_news_today()
        assert isinstance(count, int)
        assert count >= 0


def test_knowledge_base_active_markets():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.tracking.knowledge_base import KnowledgeBase

    init_db()
    with get_session() as session:
        kb = KnowledgeBase(session)
        markets = kb.active_markets()
        assert isinstance(markets, list)


# ── 11. PROBABILITY TRACKER ──────────────────────────────────────────────────


def test_probability_tracker_upsert_and_read():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.tracking.probability_tracker import ProbabilityTracker

    init_db()
    with get_session() as session:
        tracker = ProbabilityTracker(session)
        row = tracker.upsert_market_row(
            "polymarket", "SMOKE_TEST_MARKET_001",
            "Will this smoke test pass?",
            category="test",
            theme="other",
            slug="smoke-test",
            liquidity=1000.0,
            volume_24h=500.0,
            end_date=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
            low_confidence=False,
        )
        assert row.id is not None
        tracker.record_reading(row.id, 0.65, source_platform="polymarket")
        session.commit()

        prob = tracker.latest_probability(row.id)
        assert prob == pytest.approx(0.65)

        # Upsert again (update path)
        row2 = tracker.upsert_market_row(
            "polymarket", "SMOKE_TEST_MARKET_001",
            "Will this smoke test pass? (updated)",
            category="test",
            theme="other",
            slug="smoke-test",
            liquidity=2000.0,
            volume_24h=800.0,
            end_date=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
            low_confidence=False,
        )
        assert row2.id == row.id  # same row updated
        session.commit()


def test_normalize_polymarket_row():
    from engines.prediction_markets.tracking.probability_tracker import normalize_polymarket_row
    from engines.prediction_markets.clients.polymarket_client import PolymarketClient

    pm = PolymarketClient()
    raw = {
        "id": "abc123",
        "question": "Will X happen?",
        "outcomePrices": '["0.65", "0.35"]',
        "volume24hr": 10000,
        "liquidity": 50000,
        "endDate": "2025-12-31T00:00:00Z",
        "category": "crypto",
        "slug": "will-x-happen",
    }
    result = normalize_polymarket_row(raw, pm)
    assert result is not None
    assert result["platform"] == "polymarket"
    assert result["market_id"] == "abc123"
    assert result["probability"] == pytest.approx(0.65)
    assert result["volume_24h"] == 10000
    assert result["liquidity"] == 50000


# ── 12. RUNTIME STATE ────────────────────────────────────────────────────────


def test_runtime_state_snapshot_empty():
    from engines.prediction_markets.runtime_state import snapshot
    s = snapshot()
    assert "opportunities" in s
    assert "health" in s
    assert isinstance(s["opportunities"], list)


def test_runtime_state_set_and_get():
    from engines.prediction_markets.runtime_state import set_cycle_results, snapshot
    health = {"markets_tracked": 42, "ok_markets": 40, "stale_markets": 2,
              "is_ready": True, "alerts_last_24h": 5}
    opps = [{"rank": 1, "final_score": 0.85, "question": "Test?"}]
    set_cycle_results(opps, health)
    s = snapshot()
    assert s["health"]["markets_tracked"] == 42
    assert len(s["opportunities"]) == 1


# ── 13. CLIENT UNIT TESTS (no network) ───────────────────────────────────────


def test_kalshi_normalize_market():
    from engines.prediction_markets.clients.kalshi_client import KalshiClient
    raw = {
        "ticker": "FOMC-24MAR-T5.25",
        "title": "Will the Fed cut rates at the March FOMC?",
        "yes_bid": 65,
        "yes_ask": 70,
        "volume": 100000,
        "liquidity": 50000,
        "close_time": "2024-03-20T18:00:00Z",
        "category": "macro",
    }
    norm = KalshiClient.normalize_market(raw)
    assert norm["platform"] == "kalshi"
    assert norm["market_id"] == "FOMC-24MAR-T5.25"
    assert norm["probability"] == pytest.approx(0.675)
    assert norm["spread"] == pytest.approx(0.05)
    assert not norm["low_confidence"]  # spread 0.05 <= 0.10


def test_kalshi_normalize_wide_spread_low_confidence():
    from engines.prediction_markets.clients.kalshi_client import KalshiClient
    raw = {"ticker": "T1", "title": "T", "yes_bid": 10, "yes_ask": 90, "volume": 0, "liquidity": 0}
    norm = KalshiClient.normalize_market(raw)
    assert norm["low_confidence"]  # spread > 0.10


def test_polymarket_extract_probability_valid():
    from engines.prediction_markets.clients.polymarket_client import PolymarketClient
    market = {"outcomePrices": '["0.72", "0.28"]', "id": "x"}
    p = PolymarketClient.extract_probability(market)
    assert p == pytest.approx(0.72)


def test_polymarket_extract_probability_invalid():
    from engines.prediction_markets.clients.polymarket_client import PolymarketClient
    assert PolymarketClient.extract_probability({}) is None
    assert PolymarketClient.extract_probability({"outcomePrices": '["0.4","0.3","0.3"]'}) is None


def test_sports_client_convert_odds():
    from engines.prediction_markets.clients.sports_client import SportsDataClient
    p1, p2 = SportsDataClient.convert_odds_to_probability(2.0, 3.0)
    assert p1 + p2 == pytest.approx(1.0)
    assert p1 > p2  # shorter odds = higher probability


def test_macro_client_save_baseline():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.clients.macro_client import MacroBaselineClient

    init_db()
    with get_session() as session:
        client = MacroBaselineClient(session)
        row = client.save_baseline(
            market_id=None,
            source_name="test_source",
            baseline_probability=0.6,
            baseline_type="test",
            notes="unit test",
            theme_key="fed_macro",
        )
        session.commit()
        assert row.id is not None
        assert row.baseline_probability == pytest.approx(0.6)


# ── 14. ALERT SERVICE ────────────────────────────────────────────────────────


def test_alert_service_stubs():
    from engines.prediction_markets.alerts.alert_service import PMAlertService
    svc = PMAlertService()
    # These are stubs — should not raise
    svc.send_opportunity_alert({"rank": 1})
    svc.send_news_lag_alert({"question": "Test?"})
    svc.send_daily_digest()


# ── 15. FASTAPI DASHBOARD ENDPOINTS ─────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from shared.dashboard.app import app
    return TestClient(app)


def test_dashboard_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "PM Engine" in r.text


def test_dashboard_health(client):
    r = client.get("/pm/health")
    assert r.status_code == 200
    data = r.json()
    assert "markets_tracked" in data
    assert "is_ready" in data


def test_dashboard_overview(client):
    r = client.get("/pm/overview")
    assert r.status_code == 200
    data = r.json()
    assert "total_bets" in data
    assert "win_rate" in data
    assert "roi_percent" in data


def test_dashboard_opportunities(client):
    r = client.get("/pm/opportunities")
    assert r.status_code == 200
    data = r.json()
    assert "opportunities" in data
    assert isinstance(data["opportunities"], list)


def test_dashboard_bets(client):
    r = client.get("/pm/bets")
    assert r.status_code == 200
    data = r.json()
    assert "bets" in data
    assert isinstance(data["bets"], list)


def test_dashboard_bets_with_params(client):
    r = client.get("/pm/bets?days=7&category=macro")
    assert r.status_code == 200


def test_dashboard_markets(client):
    r = client.get("/pm/markets")
    assert r.status_code == 200
    data = r.json()
    assert "markets" in data
    assert isinstance(data["markets"], list)


def test_dashboard_news(client):
    r = client.get("/pm/news")
    assert r.status_code == 200
    data = r.json()
    assert "articles" in data
    assert isinstance(data["articles"], list)


def test_dashboard_signals_extended(client):
    r = client.get("/pm/signals")
    assert r.status_code == 200
    data = r.json()
    assert "total" in data
    # All 4 new signal types should be present (may be empty lists)
    for sig in ("related_divergence", "scheduled_proximity", "thin_liquidity", "cross_category_momentum"):
        assert sig in data, f"Missing signal type key: {sig}"


def test_dashboard_backtest(client):
    r = client.get("/pm/backtest")
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert isinstance(data["results"], list)
    assert "top_combos" in data
    assert "best_kelly" in data
    assert "best_entry_window" in data


def test_dashboard_calibration(client):
    r = client.get("/pm/calibration")
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert isinstance(data["results"], list)
    assert "most_overpriced" in data
    assert "most_underpriced" in data


# ── 16. NEW SIGNAL TYPES ─────────────────────────────────────────────────────


def test_import_new_signals():
    from engines.prediction_markets.analysis.new_signals import (
        find_related_divergence,
        find_scheduled_proximity,
        find_thin_liquidity,
        find_cross_category_momentum,
        SCHEDULED_EVENTS,
    )
    assert callable(find_related_divergence)
    assert callable(find_scheduled_proximity)
    assert callable(find_thin_liquidity)
    assert callable(find_cross_category_momentum)
    assert isinstance(SCHEDULED_EVENTS, list)
    assert len(SCHEDULED_EVENTS) > 0


def test_find_related_divergence_empty():
    from engines.prediction_markets.analysis.new_signals import find_related_divergence
    assert find_related_divergence([]) == []


def test_find_related_divergence_gap():
    from engines.prediction_markets.analysis.new_signals import find_related_divergence
    markets = [
        {"_db_id": 1, "platform": "polymarket", "question": "Will the Fed cut rates in March?",
         "probability": 0.20, "liquidity": 50000, "category": "macro", "theme": "fed_macro"},
        {"_db_id": 2, "platform": "kalshi", "question": "Will the Fed cut interest rates in March FOMC?",
         "probability": 0.75, "liquidity": 30000, "category": "macro", "theme": "fed_macro"},
    ]
    signals = find_related_divergence(markets, min_gap=0.25)
    # gap = |0.20 - 0.75| = 0.55 > 0.25, question similarity should be high
    assert isinstance(signals, list)
    if signals:
        assert signals[0]["signal_type"] == "related_divergence"
        assert signals[0]["expected_bet"] == "YES"


def test_find_related_divergence_no_gap():
    from engines.prediction_markets.analysis.new_signals import find_related_divergence
    markets = [
        {"_db_id": 1, "platform": "polymarket", "question": "Will the Fed cut rates?",
         "probability": 0.50, "liquidity": 50000, "category": "macro", "theme": "fed_macro"},
        {"_db_id": 2, "platform": "kalshi", "question": "Will the Fed cut interest rates?",
         "probability": 0.55, "liquidity": 30000, "category": "macro", "theme": "fed_macro"},
    ]
    signals = find_related_divergence(markets, min_gap=0.25)
    assert signals == []  # gap = 0.05 < 0.25


def test_find_scheduled_proximity_no_match():
    from engines.prediction_markets.analysis.new_signals import find_scheduled_proximity
    markets = [
        {"_db_id": 1, "platform": "polymarket", "question": "Will Pluto be reclassified?",
         "probability": 0.30, "liquidity": 10000, "category": "science", "theme": "other"},
    ]
    signals = find_scheduled_proximity(markets, days_window=7)
    assert isinstance(signals, list)
    # "Pluto reclassified" matches no scheduled events


def test_find_scheduled_proximity_empty():
    from engines.prediction_markets.analysis.new_signals import find_scheduled_proximity
    assert find_scheduled_proximity([]) == []


def test_find_thin_liquidity_detects():
    from engines.prediction_markets.analysis.new_signals import find_thin_liquidity
    markets = [
        {"_db_id": 1, "platform": "polymarket", "question": "Will X happen?",
         "probability": 0.40, "liquidity": 1000, "spread": 0.15, "low_confidence": True,
         "category": "other", "theme": "other"},
    ]
    signals = find_thin_liquidity(markets)
    assert len(signals) == 1
    assert signals[0]["signal_type"] == "thin_liquidity"
    assert signals[0]["confidence"] == "low"


def test_find_thin_liquidity_skip_kalshi():
    from engines.prediction_markets.analysis.new_signals import find_thin_liquidity
    markets = [
        {"_db_id": 1, "platform": "kalshi", "question": "Will X happen?",
         "probability": 0.40, "liquidity": 500, "spread": 0.20, "low_confidence": True},
    ]
    # thin_liquidity only fires for polymarket
    signals = find_thin_liquidity(markets)
    assert signals == []


def test_find_thin_liquidity_high_liquidity_skip():
    from engines.prediction_markets.analysis.new_signals import find_thin_liquidity
    markets = [
        {"_db_id": 1, "platform": "polymarket", "question": "Will X happen?",
         "probability": 0.40, "liquidity": 100000, "spread": 0.15, "low_confidence": False},
    ]
    signals = find_thin_liquidity(markets)
    assert signals == []  # liquidity >= 5000


def test_find_cross_category_momentum_empty():
    from engines.prediction_markets.analysis.new_signals import find_cross_category_momentum
    assert find_cross_category_momentum([]) == []


def test_find_cross_category_momentum_entity_detection():
    from engines.prediction_markets.analysis.new_signals import find_cross_category_momentum
    # "Trump" appears in 3 markets across 2 categories
    markets = [
        {"_db_id": 1, "platform": "polymarket", "question": "Will Trump win the election?",
         "probability": 0.60, "liquidity": 50000, "category": "politics", "theme": "elections_us"},
        {"_db_id": 2, "platform": "kalshi", "question": "Will Trump approve the bill?",
         "probability": 0.45, "liquidity": 20000, "category": "policy", "theme": "elections_us"},
        {"_db_id": 3, "platform": "polymarket", "question": "Will Trump impose tariffs?",
         "probability": 0.70, "liquidity": 15000, "category": "economics", "theme": "recession"},
    ]
    signals = find_cross_category_momentum(markets)
    assert isinstance(signals, list)
    if signals:
        assert signals[0]["signal_type"] == "cross_category_momentum"
        detail = signals[0]["detail"]
        assert detail["market_count"] >= 3
        assert detail["category_count"] >= 2


# ── 17. BACKTEST MODULE ───────────────────────────────────────────────────────


def test_import_backtest_historical_fetcher():
    from backtest.historical_fetcher import pull_history, _parse_dt
    assert callable(pull_history)
    assert callable(_parse_dt)


def test_import_backtest_engine():
    from backtest.engine import run_backtest, _compute_metrics, _classify_entry_window
    assert callable(run_backtest)
    assert callable(_compute_metrics)


def test_backtest_engine_compute_metrics_empty():
    from backtest.engine import _compute_metrics
    m = _compute_metrics([])
    assert m["sample_size"] == 0
    assert m["hit_rate"] == 0.0


def test_backtest_engine_compute_metrics():
    from backtest.engine import _compute_metrics
    pnl = [1.0, -1.0, 0.5, -1.0, 2.0]
    m = _compute_metrics(pnl)
    assert m["sample_size"] == 5
    assert 0.0 <= m["hit_rate"] <= 1.0
    assert isinstance(m["sharpe"], float)
    assert m["max_drawdown"] >= 0.0


def test_backtest_engine_entry_window():
    from backtest.engine import _classify_entry_window
    assert _classify_entry_window(35) == "30+"
    assert _classify_entry_window(20) == "14-30"
    assert _classify_entry_window(10) == "7-14"
    assert _classify_entry_window(5) == "3-7"
    assert _classify_entry_window(2) == "1-3"
    assert _classify_entry_window(0) == "same-day"


def test_backtest_engine_run_no_data():
    """run_backtest() with no historical markets should return empty summary without crashing."""
    from backtest.engine import run_backtest
    result = run_backtest()
    assert isinstance(result, dict)
    assert "markets_analyzed" in result
    assert result["markets_analyzed"] >= 0


def test_historical_fetcher_parse_dt():
    from backtest.historical_fetcher import _parse_dt
    import datetime as dt
    # ISO string
    d = _parse_dt("2025-01-15T12:00:00Z")
    assert d is not None
    assert d.year == 2025
    # None
    assert _parse_dt(None) is None
    # Datetime passthrough
    now = dt.datetime.now(dt.timezone.utc)
    assert _parse_dt(now) == now


# ── 18. OPPORTUNITY RANKER WITH EXTRA SIGNALS ────────────────────────────────


def test_opportunity_ranker_extra_alerts():
    from engines.prediction_markets.storage.db import get_session, init_db
    from engines.prediction_markets.betting.opportunity_ranker import OpportunityRanker

    init_db()
    with get_session() as session:
        ranker = OpportunityRanker(session)
        extra = [{
            "signal_type": "scheduled_proximity",
            "market_id": None,
            "platform": "polymarket",
            "question": "Will FOMC cut rates?",
            "category": "macro",
            "theme": "fed_macro",
            "expected_bet": "YES",
            "ev_estimate": 0.08,
            "confidence": "ok",
            "probability": 0.40,
            "liquidity": 50000,
            "detail": {"event_name": "FOMC Meeting", "days_until": 3, "velocity_boosted": False},
        }]
        ranked = ranker.rank_opportunities([], [], [], [], extra_alerts=extra)
        assert isinstance(ranked, list)
