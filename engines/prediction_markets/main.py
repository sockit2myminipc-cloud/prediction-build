from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import schedule
from loguru import logger
from sqlalchemy import select

from engines.prediction_markets.analysis.arbitrage_scanner import ArbitrageScanner, arb_to_opportunity
from engines.prediction_markets.analysis.news_impact_scorer import NewsImpactScorer, ensure_ollama_running
from engines.prediction_markets.analysis.new_signals import (
    find_cross_category_momentum,
    find_related_divergence,
    find_scheduled_proximity,
    find_thin_liquidity,
)
from engines.prediction_markets.analysis.outlier_detector import OutlierDetector, outlier_to_opportunity_dict
from engines.prediction_markets.analysis.velocity_scanner import VelocityScanner, velocity_alerts_to_opportunities
from engines.prediction_markets.betting.opportunity_ranker import (
    OpportunityRanker,
    filter_markets as quality_filter_markets,
    news_lag_to_opportunities,
)
from engines.prediction_markets.betting.paper_bettor import PaperBettor
from engines.prediction_markets.clients.kalshi_client import KalshiClient
from engines.prediction_markets.clients.macro_client import MacroBaselineClient
from engines.prediction_markets.clients.newsapi_client import NewsIngester
from engines.prediction_markets.clients.polymarket_client import PolymarketClient
from engines.prediction_markets.filters.dedup import MarketDeduplicator
from engines.prediction_markets.filters.market_filter import MarketFilter
from engines.prediction_markets.runtime_state import set_cycle_results, set_fetch_time, set_filter_stats
from engines.prediction_markets.storage.db import get_session, init_db
from engines.prediction_markets.storage.models import HealthLog, NewsArticle, StatisticalBaseline
from engines.prediction_markets.tracking.knowledge_base import KnowledgeBase
from engines.prediction_markets.tracking.probability_tracker import ProbabilityTracker, normalize_polymarket_row

_last_news_fetch: dt.datetime | None = None
_NEWS_MIN_INTERVAL = dt.timedelta(minutes=30)


def _parse_end(v: object) -> dt.datetime | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.UTC)
    if isinstance(v, str):
        try:
            return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def fetch_and_normalize() -> tuple[list[dict], list[dict]]:
    pm_c = PolymarketClient()
    ks_c = KalshiClient()
    pm_raw = pm_c.get_all_active_markets(limit=500)
    ks_raw = ks_c.get_all_markets(limit=10000)
    set_fetch_time("polymarket", dt.datetime.now(dt.UTC))
    set_fetch_time("kalshi", dt.datetime.now(dt.UTC))

    pm_rows: list[dict] = []
    for raw in pm_raw:
        row = normalize_polymarket_row(raw, pm_c)
        if row:
            pm_rows.append(row)

    ks_rows: list[dict] = []
    for raw in ks_raw:
        n = KalshiClient.normalize_market(raw)
        ks_rows.append(
            {
                **n,
                "end_date": _parse_end(n.get("end_date")),
            }
        )
    return pm_rows, ks_rows


def persist_markets(session, markets: list[dict]) -> list[dict]:
    tr = ProbabilityTracker(session)
    enriched = []
    for m in markets:
        theme = MarketFilter.assign_theme(m.get("question", ""))
        end = m.get("end_date")
        row = tr.upsert_market_row(
            m["platform"],
            m["market_id"],
            m["question"],
            category=m.get("category"),
            theme=theme,
            slug=m.get("slug"),
            liquidity=m.get("liquidity"),
            volume_24h=m.get("volume_24h"),
            end_date=end if isinstance(end, dt.datetime) else _parse_end(end),
            low_confidence=bool(m.get("low_confidence")),
        )
        tr.record_reading(
            row.id,
            float(m["probability"]),
            source_platform=m["platform"],
            volume=m.get("volume_24h"),
            quality="ok",
        )
        enriched.append({**m, "theme": theme, "_db_id": row.id})
    session.commit()
    return enriched


def run_cycle() -> dict:
    init_db()
    session = get_session()
    try:
        baselines_n = session.scalar(select(StatisticalBaseline.id).limit(1))
        if baselines_n is None:
            macro = MacroBaselineClient(session)
            macro.seed_theme_baselines()
            session.commit()
            logger.info("Seeded theme statistical baselines")

        pm_rows, ks_rows = fetch_and_normalize()
        all_synced = persist_markets(session, pm_rows + ks_rows)

        scored = MarketFilter.filter_markets(all_synced, min_score=0.3)
        kept, _dropped = MarketDeduplicator.dedup(scored)

        # Apply backtest-derived quality filter: liquidity, price movement, resolution boost
        kept = quality_filter_markets(kept, session)
        set_filter_stats({
            "total_fetched": len(pm_rows) + len(ks_rows),
            "after_liquidity": len(scored),
            "after_movement": len(kept),
            "final_passed": len(kept),
            "kalshi_passed": sum(1 for m in kept if m.get("platform") == "kalshi"),
            "polymarket_passed": sum(1 for m in kept if m.get("platform") == "polymarket"),
        })
        resolution_boost_map = {
            int(m["_db_id"]): m.get("_resolution_boost", 0.0)
            for m in kept if m.get("_db_id") is not None
        }

        pm_only = [m for m in all_synced if m["platform"] == "polymarket"]
        ks_only = [m for m in all_synced if m["platform"] == "kalshi"]

        det = OutlierDetector()
        outliers = [outlier_to_opportunity_dict(a) for a in det.find_outliers(session, kept)]

        arb_s = ArbitrageScanner()
        arb_raw = arb_s.find_cross_platform_arb(pm_only, ks_only)
        arb_raw.extend(arb_s.find_correlated_arb(all_synced))
        arb_ops = [a for a in (arb_to_opportunity(x) for x in arb_raw) if a]

        vel = VelocityScanner(session)
        movers = vel.find_movers(kept, min_velocity=0.04, min_liquidity=5000)
        vel_raw = vel.detect_velocity_opportunities(movers)
        vel_ops = velocity_alerts_to_opportunities(vel_raw)

        kb = KnowledgeBase(session)
        news_ops: list[dict] = []

        global _last_news_fetch
        now_utc = dt.datetime.now(dt.UTC)
        _skip_news = (
            _last_news_fetch is not None
            and now_utc - _last_news_fetch < _NEWS_MIN_INTERVAL
        )
        if _skip_news:
            logger.debug(
                "News fetch skipped — last fetch was {:.0f}s ago (min interval {}s)",
                (now_utc - _last_news_fetch).total_seconds(),
                _NEWS_MIN_INTERVAL.total_seconds(),
            )
            new_count, new_article_ids = 0, []
        else:
            ing = NewsIngester(session)
            new_count, new_article_ids = ing.run_cycle()
            _last_news_fetch = now_utc
        set_fetch_time("news", now_utc)
        scorer = NewsImpactScorer(session)
        if new_count:

            def readings_loader(mid: int):
                since = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(minutes=30)
                return kb.readings_since(mid, since)

            market_views = [
                {
                    **m,
                    "id": m["_db_id"],
                }
                for m in kept
            ]
            for aid in new_article_ids:
                try:
                    _e, _matches, alerts = scorer.process_article(aid, market_views, readings_loader)
                    news_ops.extend(news_lag_to_opportunities(alerts))
                except Exception as e:
                    logger.warning("News process article {} failed: {}", aid, e)
                    try:
                        session.rollback()
                    except Exception:
                        pass
            try:
                session.commit()
            except Exception as e:
                logger.warning("News session commit failed (rolling back): {}", e)
                try:
                    session.rollback()
                except Exception:
                    pass

        reading_ts = {}
        for m in kept:
            lr = kb.latest_reading(int(m["_db_id"]))
            if lr:
                reading_ts[int(m["_db_id"])] = lr.timestamp

        # ── New signal types (Phase 6) ────────────────────────────────────
        velocity_ids: set[int] = {
            int(o["market_id"]) for o in vel_ops if o.get("market_id") is not None
        }
        try:
            related_div_ops = find_related_divergence(kept)
            logger.info("New signal related_divergence: {} alerts", len(related_div_ops))
        except Exception as e:
            logger.warning("find_related_divergence failed: {}", e)
            related_div_ops = []
        try:
            sched_prox_ops = find_scheduled_proximity(kept, velocity_market_ids=velocity_ids)
            logger.info("New signal scheduled_proximity: {} alerts", len(sched_prox_ops))
        except Exception as e:
            logger.warning("find_scheduled_proximity failed: {}", e)
            sched_prox_ops = []
        try:
            thin_liq_ops = find_thin_liquidity(kept)
            logger.info("New signal thin_liquidity: {} alerts", len(thin_liq_ops))
        except Exception as e:
            logger.warning("find_thin_liquidity failed: {}", e)
            thin_liq_ops = []
        try:
            cross_cat_ops = find_cross_category_momentum(kept)
            logger.info("New signal cross_category_momentum: {} alerts", len(cross_cat_ops))
        except Exception as e:
            logger.warning("find_cross_category_momentum failed: {}", e)
            cross_cat_ops = []

        extra_ops = related_div_ops + sched_prox_ops + thin_liq_ops + cross_cat_ops

        ranker = OpportunityRanker(session)
        nl_ops = news_ops
        ranked = ranker.rank_opportunities(
            outliers,
            nl_ops,
            vel_ops,
            arb_ops,
            extra_ops,
            reading_timestamps=reading_ts,
            resolution_boost_map=resolution_boost_map,
        )
        session.commit()

        pb = PaperBettor(session)
        if os.environ.get("AUTO_PAPER_BET", "").lower() == "true":
            for o in ranked:
                if o.get("signal_type") == "arbitrage":
                    continue
                if (o.get("confidence") or "").lower() == "low":
                    continue
                if float(o.get("final_score") or 0) <= 0.7 or float(o.get("trust_score") or 0) <= 0.35:
                    continue
                if float(o.get("ev_estimate") or 0) <= 0.08:
                    continue
                mid = o.get("market_id")
                direction = o.get("expected_bet")
                if mid and direction in ("YES", "NO"):
                    pb.place_paper_bet(
                        int(mid),
                        direction,
                        str(o.get("signal_type")),
                        {
                            "ev_estimate": o.get("ev_estimate"),
                            "signal_type": o.get("signal_type"),
                            "final_score": o.get("final_score"),
                        },
                    )
            session.commit()

        pb.check_resolutions()

        ok_m = sum(1 for m in kept if not m.get("low_confidence"))
        stale_m = len(kept) - ok_m
        open_bets = len(kb.open_paper_bets())
        health = {
            "markets_tracked": len(kept),
            "ok_markets": ok_m,
            "stale_markets": stale_m,
            "news_articles_today": kb.count_news_today(),
            "open_paper_bets": open_bets,
            "last_poll_time": dt.datetime.now(dt.UTC).isoformat(),
            "is_ready": len(kept) > 0,
            "alerts_last_24h": len(news_ops) + len(outliers) + len(extra_ops),
        }
        session.add(
            HealthLog(
                timestamp=dt.datetime.now(dt.UTC),
                markets_tracked=health["markets_tracked"],
                ok_markets=health["ok_markets"],
                stale_markets=health["stale_markets"],
                news_articles_today=health["news_articles_today"],
                paper_bets_open=health["open_paper_bets"],
                last_signal_score=ranked[0]["final_score"] if ranked else None,
                notes=None,
            )
        )
        session.commit()

        set_cycle_results(ranked, health, alerts_delta=len(news_ops))
        return {"health": health, "opportunities": len(ranked), "dedup_dropped": len(_dropped)}
    finally:
        session.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prediction market research engine")
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        metavar="MINUTES",
        help="Run for this many minutes then exit cleanly (omit to run indefinitely)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Log to this file in addition to stdout",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Configure file logging if requested
    if args.log_file:
        log_dir = os.path.dirname(args.log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        logger.add(args.log_file, level="DEBUG", encoding="utf-8")

    logger.info("Prediction market engine starting")
    ensure_ollama_running()

    if args.duration is not None:
        logger.info("Running in timed mode: will stop after {} minutes", args.duration)
        deadline = time.time() + args.duration * 60
    else:
        deadline = None

    init_db()
    run_cycle()
    schedule.every(10).minutes.do(run_cycle)

    while True:
        if deadline is not None and time.time() >= deadline:
            logger.info("Timed run complete. Exiting cleanly.")
            sys.exit(0)
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
