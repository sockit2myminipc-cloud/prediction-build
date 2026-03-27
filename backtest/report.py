"""Query and print all backtest result tables with summary stats."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, func
from engines.prediction_markets.storage.db import get_session, init_db
from engines.prediction_markets.storage.models import BacktestResult, CalibrationResult, MomentumResult


def fmt(val, decimals=4):
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}"


def print_section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def main():
    init_db()
    session = get_session()

    try:
        # ── BACKTEST RESULTS ──────────────────────────────────────────────
        print_section("BACKTEST RESULTS (backtest_results table)")
        rows = session.scalars(select(BacktestResult).order_by(
            BacktestResult.signal_type, BacktestResult.kelly_fraction
        )).all()

        # Group into sections
        base_signals = [r for r in rows if r.signal_type in ("outlier", "velocity", "news_lag") and r.kelly_fraction is None]
        combo_signals = [r for r in rows if r.signal_type == "combo"]
        kelly_rows = [r for r in rows if r.kelly_fraction is not None]
        entry_window_rows = [r for r in rows if r.signal_type.startswith("entry_window_")]
        hour_rows = [r for r in rows if r.signal_type.startswith("hour_")]
        weekday_rows = [r for r in rows if r.signal_type.startswith("weekday_")]

        print("\n--- Base Signal Performance ---")
        print(f"{'Signal':<20} {'HitRate':>8} {'EV':>8} {'ROI':>8} {'Sharpe':>8} {'MaxDD':>8} {'N':>6}")
        print("-" * 70)
        for r in base_signals:
            print(f"{r.signal_type:<20} {fmt(r.hit_rate):>8} {fmt(r.ev):>8} {fmt(r.roi):>8} {fmt(r.sharpe):>8} {fmt(r.max_drawdown):>8} {r.sample_size:>6}")

        print("\n--- Signal Combo Performance ---")
        print(f"{'Combo':<35} {'HitRate':>8} {'EV':>8} {'ROI':>8} {'N':>6}")
        print("-" * 70)
        for r in combo_signals:
            print(f"{(r.signal_combo or r.signal_type):<35} {fmt(r.hit_rate):>8} {fmt(r.ev):>8} {fmt(r.roi):>8} {r.sample_size:>6}")

        print("\n--- Kelly Sizing Per Signal ---")
        print(f"{'Signal':<20} {'Fraction':<10} {'KellyROI':>10} {'FinalBank':>10} {'KellyDD':>10}")
        print("-" * 70)
        for r in sorted(kelly_rows, key=lambda x: (x.signal_type, x.kelly_fraction or "")):
            print(f"{r.signal_type:<20} {(r.kelly_fraction or ''):<10} {fmt(r.kelly_roi):>10} {fmt(r.kelly_final_bankroll, 2):>10} {fmt(r.kelly_max_drawdown, 2):>10}")

        print("\n--- Entry Window Performance (best by EV) ---")
        print(f"{'Window':<15} {'Signal':<15} {'HitRate':>8} {'EV':>8} {'ROI':>8} {'N':>6}")
        print("-" * 70)
        entry_sorted = sorted(entry_window_rows, key=lambda x: (x.ev or 0), reverse=True)
        for r in entry_sorted[:15]:
            window = r.best_entry_window or r.signal_type.replace("entry_window_", "")
            print(f"{window:<15} {(r.signal_combo or ''):<15} {fmt(r.hit_rate):>8} {fmt(r.ev):>8} {fmt(r.roi):>8} {r.sample_size:>6}")

        print("\n--- Time-of-Day Performance (top 10 hours by EV) ---")
        print(f"{'Hour (UTC)':<15} {'HitRate':>8} {'EV':>8} {'Sharpe':>8} {'N':>6}")
        print("-" * 70)
        hour_sorted = sorted([r for r in hour_rows if r.sample_size > 0], key=lambda x: (x.ev or 0), reverse=True)
        for r in hour_sorted[:10]:
            hour_num = r.signal_type.replace("hour_", "")
            print(f"Hour {hour_num:>2} UTC     {fmt(r.hit_rate):>8} {fmt(r.ev):>8} {fmt(r.sharpe):>8} {r.sample_size:>6}")

        print("\n--- Weekday Performance ---")
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        print(f"{'Day':<12} {'HitRate':>8} {'EV':>8} {'Sharpe':>8} {'N':>6}")
        print("-" * 55)
        for r in sorted([r for r in weekday_rows if r.sample_size > 0], key=lambda x: int(x.signal_type.split("_")[1])):
            day_idx = int(r.signal_type.split("_")[1])
            print(f"{day_names[day_idx]:<12} {fmt(r.hit_rate):>8} {fmt(r.ev):>8} {fmt(r.sharpe):>8} {r.sample_size:>6}")

        # ── CALIBRATION RESULTS ───────────────────────────────────────────
        print_section("CALIBRATION RESULTS (calibration_results table)")
        calib_rows = session.scalars(select(CalibrationResult).order_by(
            CalibrationResult.source, CalibrationResult.category, CalibrationResult.prob_bucket
        )).all()

        print(f"\n{'Source':<12} {'Category':<15} {'Bucket':<10} {'MktProb':>8} {'ActualRes':>10} {'Bias':>8} {'N':>5}")
        print("-" * 75)
        for r in calib_rows:
            bias_marker = " <<UNDERPRICED" if (r.bias or 0) > 0.2 else (" >>OVERPRICED" if (r.bias or 0) < -0.2 else "")
            print(f"{r.source:<12} {(r.category or 'N/A'):<15} {r.prob_bucket:<10} {fmt(r.avg_market_prob):>8} {fmt(r.actual_resolution_rate):>10} {fmt(r.bias):>8} {r.sample_size:>5}{bias_marker}")

        # ── MOMENTUM RESULTS ──────────────────────────────────────────────
        print_section("MOMENTUM RESULTS (momentum_results table)")
        mom_rows_list = session.scalars(select(MomentumResult).order_by(
            MomentumResult.category, MomentumResult.window_hours
        )).all()

        if not mom_rows_list:
            print("\n  No momentum results (requires multi-point velocity spikes in price history).")
        else:
            print(f"\n{'Category':<15} {'Window(h)':>10} {'MomFrac':>8} {'RevFrac':>8} {'AvgCont':>8} {'Behavior':<16} {'N':>5}")
            print("-" * 75)
            for r in mom_rows_list:
                print(f"{r.category:<15} {r.window_hours:>10} {fmt(r.momentum_fraction):>8} {fmt(r.mean_reversion_fraction):>8} {fmt(r.avg_continuation):>8} {(r.behavior or 'N/A'):<16} {r.sample_size:>5}")

        # ── SUMMARY STATS PER SIGNAL TYPE ─────────────────────────────────
        print_section("SUMMARY STATISTICS")

        print("\n--- Signal P&L Breakdown ---")
        for sig in ["outlier", "velocity", "news_lag"]:
            sig_rows = [r for r in rows if r.signal_type == sig and r.kelly_fraction is None]
            if sig_rows:
                r = sig_rows[0]
                print(f"\n  {sig.upper()}:")
                print(f"    Sample Size  : {r.sample_size}")
                print(f"    Hit Rate     : {fmt(r.hit_rate)} ({(r.hit_rate or 0)*100:.1f}%)")
                print(f"    Avg Edge     : {fmt(r.avg_edge)}")
                print(f"    EV           : {fmt(r.ev)}")
                print(f"    ROI          : {fmt(r.roi)} ({(r.roi or 0)*100:.1f}%)")
                print(f"    Sharpe       : {fmt(r.sharpe)}")
                print(f"    Max Drawdown : {fmt(r.max_drawdown)}")

        print("\n--- Best Signal Combo by EV ---")
        combo_sorted = sorted(combo_signals, key=lambda x: x.ev or 0, reverse=True)
        if combo_sorted:
            best = combo_sorted[0]
            print(f"  {best.signal_combo}: EV={fmt(best.ev)}, HitRate={fmt(best.hit_rate)}, N={best.sample_size}")

        print("\n--- Kelly Recommendation ---")
        sig_kelly = {}
        for r in kelly_rows:
            key = r.signal_type
            if key not in sig_kelly:
                sig_kelly[key] = []
            sig_kelly[key].append(r)
        for sig, kr in sig_kelly.items():
            best_k = max(kr, key=lambda x: x.kelly_roi or 0)
            print(f"  {sig}: best kelly fraction = {best_k.kelly_fraction}, ROI={fmt(best_k.kelly_roi)}, FinalBank={fmt(best_k.kelly_final_bankroll, 2)}")

        print("\n--- Calibration Extremes ---")
        all_calib = sorted(calib_rows, key=lambda x: abs(x.bias or 0), reverse=True)
        print("  Most mispriced (by |bias|):")
        for r in all_calib[:8]:
            direction = "UNDERPRICED" if (r.bias or 0) > 0 else "OVERPRICED"
            print(f"    {r.source}/{r.category}/{r.prob_bucket}: bias={fmt(r.bias)} ({direction}), N={r.sample_size}")

        print(f"\n{'='*70}")
        print("  Report complete.")
        print(f"{'='*70}\n")

    finally:
        session.close()


if __name__ == "__main__":
    main()
