# src/backtest/metrics.py
"""
Backtest metrics reporting and visualization.
- Console report (always)
- Equity curve plot (matplotlib)
- R-distribution histogram
- Parameter sensitivity analysis
- In-sample vs out-of-sample comparison
"""
from __future__ import annotations
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from src.core.logging_setup import get_logger

if TYPE_CHECKING:
    from src.backtest.engine import BacktestResults, BacktestTrade

log = get_logger("metrics")


def print_report(results: "BacktestResults", title: str = "BACKTEST REPORT") -> None:
    """Print full backtest report to console."""
    sep = "=" * 65
    print(f"\n{sep}")
    print(f"  {title}")
    print(f"  Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(sep)

    # Show parameter set used
    if hasattr(results, 'rsi_threshold_used'):
        weekly_str = "with weekly EMA21" if results.weekly_regime_used else "without weekly EMA21"
        print(f"\n{'PARAMETER SET':}")
        print(f"  RSI threshold      : RSI < {results.rsi_threshold_used:.0f}")
        print(f"  Proximity          : within {results.proximity_pct_used:.0f}% of EMA20")
        print(f"  Weekly regime      : {weekly_str}")
        print(f"  Initial stop       : max(2.0×ATR, 3.5% floor)")
        print(f"  Scale-out          : +6.5% (50% of position)")
        print(f"  Trail              : 2.25×ATR (daily ATR, highest CLOSE)")
        print(f"  Fee per side       : 0.3% (maker)")

    print(f"\n{'OVERVIEW':}")
    print(f"  Total Trades       : {results.total_trades}")
    print(f"  Win Rate           : {results.win_rate:.1%}  ({results.winning_trades}W / {results.losing_trades}L)")
    print(f"  Profit Factor      : {results.profit_factor:.2f}  (target >= 1.3)")
    print(f"  Net Return         : {results.net_return_pct:.1%}")
    print(f"  Max Drawdown       : {results.max_drawdown_pct:.1%}  (limit 25%)")
    print(f"  Sharpe Ratio       : {results.sharpe_ratio:.2f}")
    print(f"  Avg Hold Days      : {results.avg_hold_days:.1f}")
    print(f"  Exposure           : {results.exposure_pct:.1%}")
    print(f"  Total Fees Paid    : ${results.total_fees_usd:.2f}")

    print(f"\n{'PER-TRADE STATS':}")
    print(f"  Avg Win            : {results.avg_win_pct:.2%}")
    print(f"  Avg Loss           : {results.avg_loss_pct:.2%}")
    print(f"  Avg R-Multiple     : {results.avg_r_multiple:.2f}R")

    if results.r_multiples:
        r = results.r_multiples
        print(f"  R-Multiple Range   : {min(r):.2f}R → {max(r):.2f}R")
        print(f"  R > 2              : {sum(1 for x in r if x > 2)} trades")
        print(f"  R < -1             : {sum(1 for x in r if x < -1)} trades")

    print(f"\n{'REGIME STATS':}")
    total_days = results.days_armed + results.days_flat
    if total_days > 0:
        print(f"  Days Armed (regime ok) : {results.days_armed} ({results.days_armed/total_days:.0%})")
        print(f"  Days Flat (filtered)   : {results.days_flat} ({results.days_flat/total_days:.0%})")

    print(f"\n{'EXIT BREAKDOWN':}")
    if results.trades:
        exit_counts: dict[str, int] = {}
        for t in results.trades:
            exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1
        for reason, count in sorted(exit_counts.items(), key=lambda x: -x[1]):
            pct = count / results.total_trades
            print(f"  {reason:<20}: {count:>3} ({pct:.0%})")

    # ---- Filter Attribution ----
    if hasattr(results, 'filter_attribution') and results.filter_attribution.total_considered > 0:
        fa = results.filter_attribution
        print(f"\n{'FILTER ATTRIBUTION':}")
        print(f"  {'Filter':<35} {'Days blocked':>12} {'% of eligible':>12}")
        print(f"  {'-'*61}")
        total = fa.total_considered
        print(f"  {'Total days considered for entry':<35} {fa.total_considered:>12} {'—':>12}")
        if total > 0:
            print(f"  {'EMA50 <= EMA200':<35} {fa.ema50_below_ema200:>12} {fa.ema50_below_ema200/total:>11.1%}")
        if total > 0:
            print(f"  {'Weekly EMA21 not rising':<35} {fa.weekly_ema_not_rising:>12} {fa.weekly_ema_not_rising/total:>11.1%}")
        if total > 0:
            print(f"  {'RSI >= threshold':<35} {fa.rsi_ge_50:>12} {fa.rsi_ge_50/total:>11.1%}")
        if total > 0:
            print(f"  {'Price beyond proximity':<35} {fa.price_beyond_5pct:>12} {fa.price_beyond_5pct/total:>11.1%}")
        if total > 0:
            print(f"  {'Passed ALL filters (entered)':<35} {fa.total_passed_all:>12} {fa.total_passed_all/total:>11.1%}")

    # ---- Year-by-Year Breakdown ----
    if results.yearly_stats:
        print(f"\n{'YEAR-BY-YEAR BREAKDOWN':}")
        print(f"  {'Year':<6} {'Trades':>7} {'Win Rate':>10} {'Net PnL ($)':>12}")
        print(f"  {'-'*37}")
        for yr in results.yearly_stats:
            print(f"  {yr['year']:<6} {yr['trades']:>7} {yr['win_rate']:>9.1%} ${yr['net_pnl_usd']:>+8.2f}")

    print(f"\n{'GO / NO-GO ASSESSMENT':}")
    pf_ok = "✓ PASS" if results.meets_profit_factor else "✗ FAIL"
    dd_ok = "✓ PASS" if results.meets_max_drawdown else "✗ FAIL"
    print(f"  Profit Factor >= 1.3   : {pf_ok}  ({results.profit_factor:.2f})")
    print(f"  Max DD <= 25%          : {dd_ok}  ({results.max_drawdown_pct:.1%})")
    go = "✓  GO — Ready for Phase 1 Paper Trading" if results.go_live else "✗  NO-GO — Tune strategy before proceeding"
    print(f"\n  VERDICT: {go}")

    # In-sample vs out-of-sample
    if results.in_sample_results and results.out_of_sample_results:
        print(f"\n{'IN-SAMPLE vs OUT-OF-SAMPLE (overfitting check)':}")
        ins = results.in_sample_results
        oos = results.out_of_sample_results
        print(f"  {'Metric':<22} {'In-Sample':>12} {'Out-of-Sample':>14}")
        print(f"  {'-'*48}")
        print(f"  {'Trades':<22} {ins.total_trades:>12} {oos.total_trades:>14}")
        print(f"  {'Win Rate':<22} {ins.win_rate:>11.1%} {oos.win_rate:>13.1%}")
        print(f"  {'Profit Factor':<22} {ins.profit_factor:>12.2f} {oos.profit_factor:>14.2f}")
        print(f"  {'Net Return':<22} {ins.net_return_pct:>11.1%} {oos.net_return_pct:>13.1%}")
        print(f"  {'Max DD':<22} {ins.max_drawdown_pct:>11.1%} {oos.max_drawdown_pct:>13.1%}")
        print(f"  {'Sharpe':<22} {ins.sharpe_ratio:>12.2f} {oos.sharpe_ratio:>14.2f}")

        # Degradation check
        pf_degrade = (ins.profit_factor - oos.profit_factor) / ins.profit_factor if ins.profit_factor > 0 else 0
        if pf_degrade > 0.30:
            print(f"\n  ⚠  WARNING: PF degraded {pf_degrade:.0%} in OOS — possible overfitting")
        else:
            print(f"\n  ✓  OOS degradation within acceptable range ({pf_degrade:.0%})")

    print(f"\n{sep}\n")


def print_trade_table(results: "BacktestResults", max_rows: int = 30) -> None:
    """Print individual trade table."""
    trades = results.trades
    if not trades:
        print("No trades to display.")
        return

    print(f"\n{'TRADE LOG':} ({len(trades)} trades, showing last {min(max_rows, len(trades))})")
    print(f"{'#':<4} {'Entry':>10} {'Exit':>10} {'Entry$':>8} {'Exit$':>8} "
          f"{'Net%':>7} {'R':>6} {'Days':>5} {'Exit Reason':<20}")
    print("-" * 85)

    for i, t in enumerate(trades[-max_rows:], 1):
        entry_str = t.entry_date.strftime("%Y-%m-%d") if t.entry_date else "N/A"
        exit_str = t.exit_date.strftime("%Y-%m-%d") if t.exit_date else "open"
        sign = "+" if t.net_pnl_pct >= 0 else ""
        print(
            f"{i:<4} {entry_str:>10} {exit_str:>10} "
            f"{t.entry_price:>8,.0f} {(t.exit_price or 0):>8,.0f} "
            f"{sign}{t.net_pnl_pct:>6.1%} {t.r_multiple:>6.2f} "
            f"{t.hold_days:>5.1f} {t.exit_reason:<20}"
        )


def save_equity_plot(
    results: "BacktestResults",
    output_path: str = "logs/backtest_equity.png",
    title: str = "BTC Swing Agent — Equity Curve",
) -> None:
    """Save equity curve + drawdown chart to PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        log.warning("matplotlib not installed — skipping equity plot")
        return

    if results.equity_curve.empty:
        return

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    df = results.equity_curve.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")

    # Drawdown series
    roll_max = df["equity"].cummax()
    drawdown = (df["equity"] - roll_max) / roll_max * 100

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#1a1a2e")
    for ax in (ax1, ax2):
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="#cccccc")
        ax.spines["bottom"].set_color("#444")
        ax.spines["top"].set_color("#444")
        ax.spines["left"].set_color("#444")
        ax.spines["right"].set_color("#444")

    # Equity curve
    ax1.plot(df.index, df["equity"], color="#00d4aa", linewidth=1.5, label="Equity")
    ax1.fill_between(df.index, df["equity"].min(), df["equity"], alpha=0.1, color="#00d4aa")

    # Mark trades
    for t in results.trades:
        if t.entry_date and t.exit_date:
            color = "#00ff88" if t.net_pnl_pct > 0 else "#ff4466"
            ax1.axvspan(
                pd.to_datetime(t.entry_date),
                pd.to_datetime(t.exit_date),
                alpha=0.08, color=color
            )

    # OOS split line
    if results.in_sample_results is not None:
        oos_trades = results.out_of_sample_results.trades if results.out_of_sample_results else []
        if oos_trades:
            split_date = pd.to_datetime(oos_trades[0].entry_date)
            ax1.axvline(split_date, color="#ffaa00", linestyle="--", alpha=0.7, label="OOS Split")

    ax1.set_title(title, color="#ffffff", fontsize=13, pad=10)
    ax1.set_ylabel("Equity (USD)", color="#cccccc")
    ax1.legend(facecolor="#1a1a2e", labelcolor="#cccccc", fontsize=9)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

    # Add metrics text box
    metrics_text = (
        f"Trades: {results.total_trades} | WR: {results.win_rate:.0%} | "
        f"PF: {results.profit_factor:.2f} | MaxDD: {results.max_drawdown_pct:.1%} | "
        f"Sharpe: {results.sharpe_ratio:.2f}"
    )
    ax1.text(
        0.01, 0.02, metrics_text,
        transform=ax1.transAxes,
        color="#aaaaaa", fontsize=8,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#0a0a1a", alpha=0.7)
    )

    # Drawdown
    ax2.fill_between(df.index, drawdown, 0, color="#ff4466", alpha=0.6, label="Drawdown")
    ax2.axhline(-25, color="#ffaa00", linestyle="--", alpha=0.5, linewidth=0.8, label="-25% limit")
    ax2.set_ylabel("Drawdown %", color="#cccccc")
    ax2.set_xlabel("Date", color="#cccccc")
    ax2.legend(facecolor="#1a1a2e", labelcolor="#cccccc", fontsize=8)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

    plt.tight_layout(pad=1.5)
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    log.info("Equity curve saved", path=output_path)
    print(f"  → Equity curve saved: {output_path}")


def save_r_distribution(
    results: "BacktestResults",
    output_path: str = "logs/backtest_r_dist.png",
) -> None:
    """Save R-multiple distribution histogram."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return

    if not results.r_multiples:
        return

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    r = results.r_multiples
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    colors = ["#00ff88" if x > 0 else "#ff4466" for x in r]
    ax.bar(range(len(r)), sorted(r), color=colors, alpha=0.8, width=0.8)
    ax.axhline(0, color="#888", linewidth=0.8)
    ax.axhline(results.avg_r_multiple, color="#ffaa00", linestyle="--",
               linewidth=1.2, label=f"Avg R = {results.avg_r_multiple:.2f}")

    ax.set_title("R-Multiple Distribution", color="#ffffff", fontsize=12)
    ax.set_xlabel("Trades (sorted)", color="#cccccc")
    ax.set_ylabel("R-Multiple", color="#cccccc")
    ax.tick_params(colors="#cccccc")
    ax.legend(facecolor="#1a1a2e", labelcolor="#cccccc")
    for spine in ax.spines.values():
        spine.set_color("#444")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"  → R-distribution saved: {output_path}")


def run_parameter_sensitivity(
    daily_df: pd.DataFrame,
    weekly_df: pd.DataFrame,
    base_cfg,
    output_path: str = "logs/sensitivity.csv",
) -> pd.DataFrame:
    """
    Run parameter sensitivity sweep across key thresholds.
    Tests robustness — if PF only works at exact params, it's overfit.

    Varies: signal threshold (60-80), RSI zone (35-55), stop pct (2-3.5%)
    """
    from src.backtest.engine import run_backtest

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    results_rows = []

    thresholds = [60, 65, 70, 75, 80]
    rsi_lows = [35, 40, 45]
    stop_pcts = [0.020, 0.025, 0.030, 0.035]

    import copy
    total = len(thresholds) * len(rsi_lows) * len(stop_pcts)
    count = 0

    print(f"\nRunning parameter sensitivity ({total} combinations)...")

    for threshold in thresholds:
        for rsi_low in rsi_lows:
            for stop_pct in stop_pcts:
                count += 1
                # Deep copy config and modify
                cfg_copy = copy.deepcopy(base_cfg)
                cfg_copy.signals.threshold = threshold
                cfg_copy.indicators.rsi_pullback_low = rsi_low
                cfg_copy.indicators.rsi_pullback_high = rsi_low + 10
                cfg_copy.exits.initial_stop_pct = stop_pct

                try:
                    r = run_backtest(daily_df, weekly_df, cfg_copy)
                    results_rows.append({
                        "threshold": threshold,
                        "rsi_low": rsi_low,
                        "stop_pct": stop_pct,
                        "trades": r.total_trades,
                        "win_rate": round(r.win_rate, 3),
                        "profit_factor": round(r.profit_factor, 3),
                        "max_dd": round(r.max_drawdown_pct, 3),
                        "sharpe": round(r.sharpe_ratio, 3),
                        "net_return": round(r.net_return_pct, 3),
                        "go_live": r.go_live,
                    })
                except Exception as e:
                    log.warning("Sensitivity run failed", params=f"t={threshold},rsi={rsi_low},stop={stop_pct}", error=str(e))

                if count % 10 == 0:
                    print(f"  Progress: {count}/{total}")

    df = pd.DataFrame(results_rows)
    df.to_csv(output_path, index=False)
    print(f"  → Sensitivity results saved: {output_path}")

    # Summary
    if not df.empty:
        go_count = df["go_live"].sum()
        print(f"\n  Parameter sensitivity: {go_count}/{len(df)} combinations meet GO criteria")
        if go_count / len(df) < 0.3:
            print("  ⚠  WARNING: < 30% of parameter combinations pass — strategy may be overfit")
        else:
            print("  ✓  Strategy is robust across parameter range")

    return df
