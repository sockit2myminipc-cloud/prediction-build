from __future__ import annotations

import collections
import datetime as dt
import itertools
import json
import math
import statistics
from typing import NamedTuple

from loguru import logger
from sqlalchemy import delete, select

from engines.prediction_markets.storage.db import get_session, init_db
from engines.prediction_markets.storage.models import (
    BacktestResult,
    CalibrationResult,
    HistoricalMarket,
    MomentumResult,
)

# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

class SignalFire(NamedTuple):
    signal_type: str          # "outlier" | "velocity" | "news_lag"
    market_id: int            # DB primary key
    source: str
    category: str | None
    prob_at_signal: float
    final_resolution: float   # 0.0 or 1.0 (or NaN if unknown)
    days_before_resolution: float
    implied_pnl: float
    signal_ts: int            # unix timestamp of the signal point
    direction: str            # "YES" | "NO"


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _resolution_to_float(resolution: str | None) -> float:
    """Convert a resolution string to 0.0/1.0/NaN."""
    if resolution is None:
        return math.nan
    r = resolution.strip().upper()
    if r == "YES":
        return 1.0
    if r == "NO":
        return 0.0
    try:
        return float(resolution)
    except (ValueError, TypeError):
        return math.nan


def _parse_price_history(raw: str | None) -> list[dict]:
    """Parse a JSON price history string into a list of {t, p} dicts, sorted by t."""
    if not raw:
        return []
    try:
        history = json.loads(raw)
        if not isinstance(history, list):
            return []
        valid = []
        for point in history:
            if isinstance(point, dict) and "t" in point and "p" in point:
                try:
                    valid.append({"t": int(point["t"]), "p": float(point["p"])})
                except (TypeError, ValueError):
                    continue
        valid.sort(key=lambda x: x["t"])
        return valid
    except Exception:
        return []


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0 or math.isnan(denominator) or math.isinf(denominator):
        return default
    return numerator / denominator


# ---------------------------------------------------------------------------
# Signal simulation
# ---------------------------------------------------------------------------

def _compute_implied_pnl(prob: float, resolution: float, direction: str) -> float:
    """
    Compute implied P&L for a bet.

    direction="YES" means we bet the price is too low (prob < 0.5, expect YES).
    direction="NO"  means we bet the price is too high (prob > 0.5, expect NO).

    Returns profit/loss in units (bet 1 unit):
      - Win: return is (1/prob - 1) for YES bets, (1/(1-prob) - 1) for NO bets.
      - Loss: -1 unit.
    """
    if math.isnan(resolution):
        return 0.0

    if direction == "YES":
        won = resolution >= 0.5
        if won:
            prob_clamped = max(prob, 1e-6)
            return (1.0 / prob_clamped) - 1.0
        else:
            return -1.0
    else:  # direction == "NO"
        won = resolution < 0.5
        if won:
            inv_prob = max(1.0 - prob, 1e-6)
            return (1.0 / inv_prob) - 1.0
        else:
            return -1.0


def _simulate_signals(
    market: HistoricalMarket,
    price_history: list[dict],
) -> list[SignalFire]:
    """Fire all three signal types on the price history of one market."""
    fires: list[SignalFire] = []

    if not price_history or len(price_history) < 1:
        return fires

    final_res = _resolution_to_float(market.resolution)
    close_ts: int | None = None
    if market.close_date is not None:
        close_ts = int(market.close_date.timestamp())

    for i, point in enumerate(price_history):
        p = point["p"]
        t = point["t"]

        # days before resolution
        if close_ts is not None:
            days_before = max(0.0, (close_ts - t) / 86400.0)
        else:
            days_before = 0.0

        # ── Outlier signal ──────────────────────────────────────────────────
        if abs(p - 0.5) > 0.15:
            direction = "YES" if p < 0.5 else "NO"
            pnl = _compute_implied_pnl(p, final_res, direction)
            fires.append(
                SignalFire(
                    signal_type="outlier",
                    market_id=market.id,
                    source=market.source,
                    category=market.category,
                    prob_at_signal=p,
                    final_resolution=final_res,
                    days_before_resolution=days_before,
                    implied_pnl=pnl,
                    signal_ts=t,
                    direction=direction,
                )
            )

        # ── Velocity signal ─────────────────────────────────────────────────
        if i > 0:
            prev = price_history[i - 1]
            hours_elapsed = max((t - prev["t"]) / 3600.0, 1e-6)
            velocity = (p - prev["p"]) / hours_elapsed
            if abs(velocity) > 0.04:
                direction = "YES" if velocity < 0 else "NO"
                # Falling price = market pricing event as less likely -> bet YES if we think signal is noise
                # Rising price  = market pricing higher -> could be overpriced -> bet NO
                # We align with velocity direction: momentum bet
                direction_v = "YES" if velocity > 0 else "NO"
                pnl = _compute_implied_pnl(p, final_res, direction_v)
                fires.append(
                    SignalFire(
                        signal_type="velocity",
                        market_id=market.id,
                        source=market.source,
                        category=market.category,
                        prob_at_signal=p,
                        final_resolution=final_res,
                        days_before_resolution=days_before,
                        implied_pnl=pnl,
                        signal_ts=t,
                        direction=direction_v,
                    )
                )

        # ── News lag signal ─────────────────────────────────────────────────
        if i > 0:
            jump = abs(p - price_history[i - 1]["p"])
            if jump > 0.05:
                direction_n = "YES" if p > price_history[i - 1]["p"] else "NO"
                pnl = _compute_implied_pnl(p, final_res, direction_n)
                fires.append(
                    SignalFire(
                        signal_type="news_lag",
                        market_id=market.id,
                        source=market.source,
                        category=market.category,
                        prob_at_signal=p,
                        final_resolution=final_res,
                        days_before_resolution=days_before,
                        implied_pnl=pnl,
                        signal_ts=t,
                        direction=direction_n,
                    )
                )

    return fires


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------

def _compute_metrics(pnl_list: list[float]) -> dict:
    """Given a list of per-trade P&Ls, return a dict of performance metrics."""
    n = len(pnl_list)
    if n == 0:
        return {
            "hit_rate": 0.0,
            "avg_edge": 0.0,
            "ev": 0.0,
            "roi": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "sample_size": 0,
        }

    wins = [x for x in pnl_list if x > 0]
    losses = [x for x in pnl_list if x <= 0]
    hit_rate = len(wins) / n
    avg_edge = statistics.mean(pnl_list)
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = abs(statistics.mean(losses)) if losses else 1.0

    ev = hit_rate * avg_win - (1 - hit_rate) * avg_loss
    roi = _safe_div(sum(pnl_list), n)

    try:
        std = statistics.stdev(pnl_list) if n > 1 else 0.0
    except Exception:
        std = 0.0
    sharpe = _safe_div(avg_edge, std) if std > 0 else 0.0

    # Max drawdown (from running cumulative peak)
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnl_list:
        running += pnl
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    return {
        "hit_rate": hit_rate,
        "avg_edge": avg_edge,
        "ev": ev,
        "roi": roi,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "sample_size": n,
    }


# ---------------------------------------------------------------------------
# Kelly Criterion simulation (5b)
# ---------------------------------------------------------------------------

def _kelly_fraction_value(hit_rate: float, avg_win: float, avg_loss: float) -> float:
    """Compute full Kelly fraction f = (p*b - q) / b where b = avg_win/avg_loss."""
    if avg_loss <= 0 or avg_win <= 0:
        return 0.0
    b = _safe_div(avg_win, avg_loss, 0.0)
    if b <= 0:
        return 0.0
    q = 1.0 - hit_rate
    f = _safe_div(hit_rate * b - q, b, 0.0)
    return max(0.0, min(f, 1.0))  # clamp to [0, 1]


def _simulate_kelly(pnl_list: list[float], fraction: float, starting_bankroll: float = 100.0) -> dict:
    """
    Simulate bankroll growth with Kelly-scaled bet sizes.

    fraction: the portion of Kelly to use (1.0=full, 0.5=half, etc.)
              or 0 to mean flat betting (1 unit per trade).
    """
    bankroll = starting_bankroll
    peak = starting_bankroll
    max_dd = 0.0
    bets_per_trade = 1.0  # flat

    wins = [x for x in pnl_list if x > 0]
    losses = [x for x in pnl_list if x <= 0]
    avg_win = statistics.mean(wins) if wins else 1.0
    avg_loss = abs(statistics.mean(losses)) if losses else 1.0
    hit_rate = len(wins) / len(pnl_list) if pnl_list else 0.0

    if fraction > 0:
        full_kelly = _kelly_fraction_value(hit_rate, avg_win, avg_loss)
        kelly_f = full_kelly * fraction
    else:
        kelly_f = 0.0  # flat bet

    for pnl in pnl_list:
        if fraction > 0 and kelly_f > 0:
            bet_size = bankroll * kelly_f
        else:
            bet_size = bets_per_trade
        if pnl > 0:
            bankroll += bet_size * (pnl / avg_win if avg_win > 0 else pnl)
        else:
            bankroll -= bet_size
        bankroll = max(bankroll, 0.0)  # ruin floor
        if bankroll > peak:
            peak = bankroll
        dd = peak - bankroll
        if dd > max_dd:
            max_dd = dd

    roi = _safe_div(bankroll - starting_bankroll, starting_bankroll)
    return {
        "final_bankroll": bankroll,
        "roi": roi,
        "max_drawdown": max_dd,
    }


# ---------------------------------------------------------------------------
# Entry window buckets (5c)
# ---------------------------------------------------------------------------

_ENTRY_WINDOWS = [
    ("30+", 30, float("inf")),
    ("14-30", 14, 30),
    ("7-14", 7, 14),
    ("3-7", 3, 7),
    ("1-3", 1, 3),
    ("same-day", 0, 1),
]


def _classify_entry_window(days_before: float) -> str:
    for label, lo, hi in _ENTRY_WINDOWS:
        if lo <= days_before < hi:
            return label
    return "30+"


# ---------------------------------------------------------------------------
# Probability bucket helper (5d)
# ---------------------------------------------------------------------------

def _prob_bucket(prob: float) -> str:
    pct = prob * 100.0
    bucket_idx = min(int(pct // 10), 9)  # 0..9
    lo = bucket_idx * 10
    hi = lo + 10
    return f"{lo}-{hi}%"


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest() -> dict:
    """
    Load all HistoricalMarkets, simulate signals, compute all metrics,
    persist results, and return a summary dict.
    """
    run_date = dt.datetime.now(dt.timezone.utc)

    # Ensure tables exist
    try:
        init_db()
    except Exception as exc:
        logger.warning("init_db warning: {}", exc)

    session = get_session()
    try:
        # ── Load all historical markets ──────────────────────────────────
        markets: list[HistoricalMarket] = session.scalars(select(HistoricalMarket)).all()
        logger.info("Loaded {} historical markets for backtesting", len(markets))

        if not markets:
            logger.warning("No historical markets found. Run pull_history() first.")
            return {
                "markets_analyzed": 0,
                "signal_fires": {"outlier": 0, "velocity": 0, "news_lag": 0},
                "top_combos": [],
                "best_kelly": {},
                "best_entry_window": {},
                "calibration_summary": [],
                "momentum_summary": [],
                "run_date": run_date.isoformat(),
            }

        # ── Simulate all signals ─────────────────────────────────────────
        all_fires: list[SignalFire] = []
        # Also keep per-market fires for combo stacking
        fires_by_market: dict[int, list[SignalFire]] = collections.defaultdict(list)

        for market in markets:
            ph = _parse_price_history(market.price_history)
            mkt_fires = _simulate_signals(market, ph)
            all_fires.extend(mkt_fires)
            for f in mkt_fires:
                fires_by_market[market.id].append(f)

        fire_counts = collections.Counter(f.signal_type for f in all_fires)
        logger.info(
            "Signal fires — outlier: {}, velocity: {}, news_lag: {}",
            fire_counts["outlier"],
            fire_counts["velocity"],
            fire_counts["news_lag"],
        )

        # ── Clear old backtest results ───────────────────────────────────
        session.execute(delete(BacktestResult))
        session.execute(delete(CalibrationResult))
        session.execute(delete(MomentumResult))
        session.flush()

        # ── Per-signal base metrics ──────────────────────────────────────
        backtest_rows: list[BacktestResult] = []

        signal_types = ["outlier", "velocity", "news_lag"]
        per_signal_pnl: dict[str, list[float]] = {s: [] for s in signal_types}
        for f in all_fires:
            if not math.isnan(f.final_resolution):
                per_signal_pnl[f.signal_type].append(f.implied_pnl)

        per_signal_metrics: dict[str, dict] = {}
        for sig in signal_types:
            pnls = per_signal_pnl[sig]
            m = _compute_metrics(pnls)
            per_signal_metrics[sig] = m
            row = BacktestResult(
                signal_type=sig,
                signal_combo=sig,
                hit_rate=m["hit_rate"],
                avg_edge=m["avg_edge"],
                ev=m["ev"],
                roi=m["roi"],
                sharpe=m["sharpe"],
                max_drawdown=m["max_drawdown"],
                sample_size=m["sample_size"],
                run_date=run_date,
            )
            backtest_rows.append(row)

        # ── Signal stacking (5a) ─────────────────────────────────────────
        combo_definitions = [
            ("outlier+velocity", ["outlier", "velocity"]),
            ("outlier+news_lag", ["outlier", "news_lag"]),
            ("velocity+news_lag", ["velocity", "news_lag"]),
            ("outlier+velocity+news_lag", ["outlier", "velocity", "news_lag"]),
        ]

        combo_results: list[dict] = []

        for combo_name, required_signals in combo_definitions:
            combo_pnl: list[float] = []
            # For each market, find co-fires within 24h
            for mkt_id, mkt_fires in fires_by_market.items():
                by_signal: dict[str, list[SignalFire]] = collections.defaultdict(list)
                for f in mkt_fires:
                    by_signal[f.signal_type].append(f)

                # Check if all required signal types fired
                if not all(sig in by_signal for sig in required_signals):
                    continue

                # Find time windows where all required signals fired within 24h
                # Take the Cartesian product of fires across required signal types
                signal_groups = [by_signal[sig] for sig in required_signals]
                for combo_fires in itertools.product(*signal_groups):
                    timestamps = [f.signal_ts for f in combo_fires]
                    window = max(timestamps) - min(timestamps)
                    if window <= 86400:  # within 24 hours
                        # Use the last fire's P&L (or average)
                        valid = [f for f in combo_fires if not math.isnan(f.final_resolution)]
                        if valid:
                            combo_pnl.append(statistics.mean([f.implied_pnl for f in valid]))
                        break  # count each market once per combo

            m = _compute_metrics(combo_pnl)
            combo_results.append({"combo": combo_name, **m})
            row = BacktestResult(
                signal_type="combo",
                signal_combo=combo_name,
                hit_rate=m["hit_rate"],
                avg_edge=m["avg_edge"],
                ev=m["ev"],
                roi=m["roi"],
                sharpe=m["sharpe"],
                max_drawdown=m["max_drawdown"],
                sample_size=m["sample_size"],
                run_date=run_date,
            )
            backtest_rows.append(row)

        # Sort combos by EV descending for summary
        combo_results.sort(key=lambda x: x.get("ev", 0.0), reverse=True)
        top_combos = [
            {
                "combo": r["combo"],
                "hit_rate": round(r["hit_rate"], 4),
                "ev": round(r["ev"], 4),
                "sample_size": r["sample_size"],
            }
            for r in combo_results[:5]
        ]

        # ── Kelly Criterion (5b) ─────────────────────────────────────────
        kelly_fractions = [
            ("flat", 0.0),
            ("full", 1.0),
            ("half", 0.5),
            ("quarter", 0.25),
        ]
        kelly_summary: list[dict] = []
        # Use "outlier" signal as reference (or all combined)
        all_pnl_combined = [f.implied_pnl for f in all_fires if not math.isnan(f.final_resolution)]

        for fraction_name, fraction_val in kelly_fractions:
            if not all_pnl_combined:
                break
            ks = _simulate_kelly(all_pnl_combined, fraction_val)
            kelly_summary.append(
                {
                    "fraction": fraction_name,
                    "roi": round(ks["roi"], 4),
                    "final_bankroll": round(ks["final_bankroll"], 2),
                    "max_drawdown": round(ks["max_drawdown"], 2),
                }
            )
            # Attach Kelly data to per-signal rows
            for sig in signal_types:
                pnls = per_signal_pnl[sig]
                if not pnls:
                    continue
                ks_sig = _simulate_kelly(pnls, fraction_val)
                m = per_signal_metrics[sig]
                row = BacktestResult(
                    signal_type=sig,
                    signal_combo=sig,
                    hit_rate=m["hit_rate"],
                    avg_edge=m["avg_edge"],
                    ev=m["ev"],
                    roi=m["roi"],
                    sharpe=m["sharpe"],
                    max_drawdown=m["max_drawdown"],
                    sample_size=m["sample_size"],
                    kelly_fraction=fraction_name,
                    kelly_roi=ks_sig["roi"],
                    kelly_final_bankroll=ks_sig["final_bankroll"],
                    kelly_max_drawdown=ks_sig["max_drawdown"],
                    run_date=run_date,
                )
                backtest_rows.append(row)

        # Best Kelly strategy by ROI
        best_kelly: dict = {}
        if kelly_summary:
            best_kelly = max(kelly_summary, key=lambda x: x["roi"])

        # ── Entry windows (5c) ───────────────────────────────────────────
        # Group fires by window, then by signal_type
        window_pnl: dict[str, dict[str, list[float]]] = collections.defaultdict(
            lambda: collections.defaultdict(list)
        )
        for f in all_fires:
            if math.isnan(f.final_resolution):
                continue
            window = _classify_entry_window(f.days_before_resolution)
            window_pnl[window][f.signal_type].append(f.implied_pnl)

        window_summary: list[dict] = []
        for window_label, sig_pnls in window_pnl.items():
            for sig, pnls in sig_pnls.items():
                m = _compute_metrics(pnls)
                window_summary.append(
                    {
                        "window": window_label,
                        "signal": sig,
                        "hit_rate": m["hit_rate"],
                        "ev": m["ev"],
                        "sample_size": m["sample_size"],
                    }
                )
                row = BacktestResult(
                    signal_type=f"entry_window_{window_label}",
                    signal_combo=sig,
                    hit_rate=m["hit_rate"],
                    avg_edge=m["avg_edge"],
                    ev=m["ev"],
                    roi=m["roi"],
                    sharpe=m["sharpe"],
                    max_drawdown=m["max_drawdown"],
                    sample_size=m["sample_size"],
                    best_entry_window=window_label,
                    run_date=run_date,
                )
                backtest_rows.append(row)

        # Best entry window by EV
        best_entry_window: dict = {}
        if window_summary:
            best_window = max(window_summary, key=lambda x: x["ev"])
            best_entry_window = {
                "window": best_window["window"],
                "hit_rate": round(best_window["hit_rate"], 4),
                "ev": round(best_window["ev"], 4),
            }

        # ── Calibration analysis (5d) ────────────────────────────────────
        # Group by (source, category, bucket) — use final resolution price point
        CalibKey = collections.namedtuple("CalibKey", ["source", "category", "bucket"])
        calib_groups: dict[CalibKey, list[tuple[float, float]]] = collections.defaultdict(list)

        for market in markets:
            ph = _parse_price_history(market.price_history)
            final_res = _resolution_to_float(market.resolution)
            if math.isnan(final_res):
                continue
            # Use last price point as the "resolution-time" probability
            if ph:
                last_p = ph[-1]["p"]
            else:
                continue

            bucket = _prob_bucket(last_p)
            key = CalibKey(
                source=market.source,
                category=market.category or "unknown",
                bucket=bucket,
            )
            calib_groups[key].append((last_p, final_res))

        calib_rows: list[CalibrationResult] = []
        calib_summary_by_source: dict[str, dict[str, list]] = collections.defaultdict(
            lambda: collections.defaultdict(list)
        )

        for key, pairs in calib_groups.items():
            probs = [p for p, _ in pairs]
            resolutions = [r for _, r in pairs]
            avg_market_prob = statistics.mean(probs) if probs else 0.0
            actual_res_rate = statistics.mean(resolutions) if resolutions else 0.0
            bias = actual_res_rate - avg_market_prob

            calib_rows.append(
                CalibrationResult(
                    source=key.source,
                    category=key.category,
                    prob_bucket=key.bucket,
                    avg_market_prob=avg_market_prob,
                    actual_resolution_rate=actual_res_rate,
                    sample_size=len(pairs),
                    bias=bias,
                    run_date=run_date,
                )
            )
            calib_summary_by_source[key.source][key.category].append(
                {"bucket": key.bucket, "bias": bias, "sample_size": len(pairs)}
            )

        # Calibration summary: most underpriced bucket per source/category
        calibration_summary: list[dict] = []
        for source, cats in calib_summary_by_source.items():
            for cat, entries in cats.items():
                if not entries:
                    continue
                most_underpriced = max(entries, key=lambda x: x["bias"])
                calibration_summary.append(
                    {
                        "source": source,
                        "category": cat,
                        "most_underpriced_bucket": most_underpriced["bucket"],
                        "bias": round(most_underpriced["bias"], 4),
                    }
                )

        # ── Momentum analysis (5e) ───────────────────────────────────────
        # For each velocity spike, measure price change over 24h/48h/168h windows
        WINDOWS_HOURS = [24, 48, 168]
        MomKey = collections.namedtuple("MomKey", ["category", "window"])
        mom_groups: dict[MomKey, list[tuple[bool, float]]] = collections.defaultdict(list)
        # (is_momentum: bool, continuation_magnitude: float)

        for market in markets:
            ph = _parse_price_history(market.price_history)
            category = market.category or "unknown"

            for i in range(1, len(ph)):
                prev = ph[i - 1]
                curr = ph[i]
                hours_elapsed = max((curr["t"] - prev["t"]) / 3600.0, 1e-6)
                velocity = (curr["p"] - prev["p"]) / hours_elapsed

                if abs(velocity) <= 0.04:
                    continue

                spike_direction = 1 if velocity > 0 else -1

                for window_h in WINDOWS_HOURS:
                    target_ts = curr["t"] + window_h * 3600
                    # Find the price closest to target_ts after the spike
                    future_points = [pt for pt in ph[i + 1:] if pt["t"] <= target_ts]
                    if not future_points:
                        continue
                    future_price = future_points[-1]["p"]
                    price_change = future_price - curr["p"]
                    continuation = price_change * spike_direction  # positive = momentum
                    is_momentum = continuation > 0
                    key = MomKey(category=category, window=window_h)
                    mom_groups[key].append((is_momentum, continuation))

        mom_rows: list[MomentumResult] = []
        momentum_summary: list[dict] = []

        for key, observations in mom_groups.items():
            n = len(observations)
            if n == 0:
                continue
            mom_frac = sum(1 for is_m, _ in observations if is_m) / n
            rev_frac = 1.0 - mom_frac
            avg_cont = statistics.mean(c for _, c in observations)

            if mom_frac > 0.55:
                behavior = "momentum"
            elif mom_frac < 0.45:
                behavior = "mean_reversion"
            else:
                behavior = "neutral"

            mom_rows.append(
                MomentumResult(
                    category=key.category,
                    window_hours=key.window,
                    momentum_fraction=mom_frac,
                    mean_reversion_fraction=rev_frac,
                    avg_continuation=avg_cont,
                    sample_size=n,
                    behavior=behavior,
                    run_date=run_date,
                )
            )
            momentum_summary.append(
                {
                    "category": key.category,
                    "window": key.window,
                    "behavior": behavior,
                    "momentum_fraction": round(mom_frac, 4),
                    "sample_size": n,
                }
            )

        # ── Time-of-day patterns (5g) ────────────────────────────────────
        hour_pnl: dict[int, list[float]] = collections.defaultdict(list)
        weekday_pnl: dict[int, list[float]] = collections.defaultdict(list)

        for f in all_fires:
            if math.isnan(f.final_resolution):
                continue
            try:
                fire_dt = dt.datetime.fromtimestamp(f.signal_ts, tz=dt.timezone.utc)
                hour_pnl[fire_dt.hour].append(f.implied_pnl)
                weekday_pnl[fire_dt.weekday()].append(f.implied_pnl)
            except Exception:
                continue

        for hour in range(24):
            pnls = hour_pnl.get(hour, [])
            m = _compute_metrics(pnls)
            row = BacktestResult(
                signal_type=f"hour_{hour}",
                signal_combo=None,
                hit_rate=m["hit_rate"],
                avg_edge=m["avg_edge"],
                ev=m["ev"],
                roi=m["roi"],
                sharpe=m["sharpe"],
                max_drawdown=m["max_drawdown"],
                sample_size=m["sample_size"],
                run_date=run_date,
            )
            backtest_rows.append(row)

        for weekday in range(7):
            pnls = weekday_pnl.get(weekday, [])
            m = _compute_metrics(pnls)
            row = BacktestResult(
                signal_type=f"weekday_{weekday}",
                signal_combo=None,
                hit_rate=m["hit_rate"],
                avg_edge=m["avg_edge"],
                ev=m["ev"],
                roi=m["roi"],
                sharpe=m["sharpe"],
                max_drawdown=m["max_drawdown"],
                sample_size=m["sample_size"],
                run_date=run_date,
            )
            backtest_rows.append(row)

        # ── Persist all rows ─────────────────────────────────────────────
        for row in backtest_rows:
            session.add(row)
        for row in calib_rows:
            session.add(row)
        for row in mom_rows:
            session.add(row)

        try:
            session.commit()
            logger.info(
                "Backtest complete — {} BacktestResult rows, {} CalibrationResult rows, {} MomentumResult rows",
                len(backtest_rows),
                len(calib_rows),
                len(mom_rows),
            )
        except Exception as exc:
            logger.error("Failed to commit backtest results: {}", exc)
            session.rollback()
            raise

        # ── Build summary ────────────────────────────────────────────────
        summary = {
            "markets_analyzed": len(markets),
            "signal_fires": {
                "outlier": fire_counts.get("outlier", 0),
                "velocity": fire_counts.get("velocity", 0),
                "news_lag": fire_counts.get("news_lag", 0),
            },
            "top_combos": top_combos,
            "best_kelly": best_kelly,
            "best_entry_window": best_entry_window,
            "calibration_summary": calibration_summary[:10],
            "momentum_summary": momentum_summary[:10],
            "run_date": run_date.isoformat(),
        }
        return summary

    finally:
        session.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json

    result = run_backtest()
    print(_json.dumps(result, indent=2, default=str))
