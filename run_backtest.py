# run_backtest.py
"""
Phase 0 Entry Point — Data Pipeline + Backtest
================================================
Usage:
    python run_backtest.py                    # full backtest with live Coinbase data
    python run_backtest.py --offline          # use cached SQLite data only (no API calls)
    python run_backtest.py --yfinance         # use yfinance data (free, 2015-present)
    python run_backtest.py --thirds           # test thirds-based exit variant
    python run_backtest.py --sensitivity      # run parameter sensitivity sweep
    python run_backtest.py --start 2015-01-01 --end 2026-06-01

Requires:
    - .env file with COINBASE_API_KEY and COINBASE_API_SECRET
    - OR --offline flag if data already cached in data/candles.sqlite
    - OR --yfinance flag (no API keys needed)
"""
from __future__ import annotations
import argparse
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import get_config, get_coinbase_credentials
from src.core.logging_setup import setup_logging, get_logger
from src.core.db import init_db


def parse_args():
    parser = argparse.ArgumentParser(description="BTC Swing Agent — Phase 0 Backtest")
    parser.add_argument("--offline", action="store_true",
                        help="Use cached data only, no API calls")
    parser.add_argument("--yfinance", action="store_true",
                        help="Use yfinance to fetch BTC-USD data (free, 2015-present)")
    parser.add_argument("--thirds", action="store_true",
                        help="Test thirds-based exit variant (1/3 at +5%%, 1/3 at +8%%, trail 1/3)")
    parser.add_argument("--sensitivity", action="store_true",
                        help="Run parameter sensitivity sweep after main backtest")
    parser.add_argument("--start", type=str, default=None,
                        help="Override backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None,
                        help="Override backtest end date (YYYY-MM-DD)")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to config.yaml (default: config/config.yaml)")
    return parser.parse_args()


def main():
    args = parse_args()

    # ---- Load config ----
    cfg = get_config(args.config)

    # ---- Setup logging ----
    setup_logging(
        level=cfg.logging.level,
        json_log_path=cfg.logging.json_log_path,
        trade_ledger_path=cfg.logging.trade_ledger_path,
        rotate_max_bytes=cfg.logging.rotate_max_bytes,
        rotate_backups=cfg.logging.rotate_backups,
    )
    log = get_logger("run_backtest")

    # Apply CLI overrides
    if args.start:
        cfg.backtest.start_date = args.start
    if args.end:
        cfg.backtest.end_date = args.end
    if args.thirds:
        cfg.exits.enable_thirds_variant = True
        cfg.exits.scale_out_fraction = 0.333
        log.info("Thirds-based exit variant enabled")

    print("\n" + "=" * 65)
    print("  BTC SWING AGENT — PHASE 0: DATA PIPELINE + BACKTEST")
    print("=" * 65)
    print(f"  Config    : {args.config or 'config/config.yaml'}")
    print(f"  Date range: {cfg.backtest.start_date} → {cfg.backtest.end_date}")
    print(f"  Equity    : ${cfg.backtest.initial_equity:.0f}")
    print(f"  Exit mode : {'Thirds (1/3+1/3+trail)' if cfg.exits.enable_thirds_variant else 'Two-stage (50% + trail)'}")
    print(f"  Data mode : {'yfinance' if args.yfinance else 'Offline SQLite' if args.offline else 'Coinbase API'}")
    print("=" * 65 + "\n")

    # ---- Init databases ----
    log.info("Initializing databases")
    init_db(
        state_db_path="data/state.sqlite",
        candles_db_path=cfg.data.cache_db_path,
    )

    # ---- Fetch / load data ----
    start_dt = datetime.strptime(cfg.backtest.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(cfg.backtest.end_date, "%Y-%m-%d")

    print("Step 1/4: Loading candle data...")

    if args.yfinance:
        daily_df, weekly_df = _load_yfinance_data(cfg, start_dt, end_dt, log)
    else:
        daily_df, weekly_df = _load_data(cfg, start_dt, end_dt, offline=args.offline, log=log)

    if daily_df.empty:
        print("\n❌ ERROR: No daily candle data available.")
        print("   If using --offline, ensure data/candles.sqlite has cached data.")
        print("   If using --yfinance, check internet connection.")
        print("   Otherwise check your COINBASE_API_KEY in .env\n")
        sys.exit(1)

    print(f"  ✓ Daily candles  : {len(daily_df):,} rows ({daily_df['open_time'].min().date()} → {daily_df['open_time'].max().date()})")
    print(f"  ✓ Weekly candles : {len(weekly_df):,} rows")

    # ---- Run main backtest ----
    print("\nStep 2/4: Running backtest...")
    from src.backtest.engine import run_backtest
    from src.backtest.metrics import print_report, print_trade_table, save_equity_plot, save_r_distribution

    results = run_backtest(daily_df, weekly_df, cfg)

    # ---- Print results ----
    print("\nStep 3/4: Generating report...")
    variant = "THIRDS VARIANT" if cfg.exits.enable_thirds_variant else "TWO-STAGE"
    print_report(results, title=f"BACKTEST REPORT — {variant}")
    print_trade_table(results)

    # Save plots
    suffix = "_thirds" if cfg.exits.enable_thirds_variant else ""
    save_equity_plot(results, output_path=f"logs/equity_curve{suffix}.png")
    save_r_distribution(results, output_path=f"logs/r_distribution{suffix}.png")

    # ---- Parameter sensitivity ----
    if args.sensitivity:
        print("\nStep 4/4: Running parameter sensitivity sweep...")
        from src.backtest.metrics import run_parameter_sensitivity
        run_parameter_sensitivity(
            daily_df, weekly_df, cfg,
            output_path=f"logs/sensitivity{suffix}.csv"
        )
    else:
        print("\nStep 4/4: Sensitivity sweep skipped (use --sensitivity to enable)")

    # ---- Final verdict ----
    print("\n" + "=" * 65)
    if results.go_live:
        print("  ✅  GO — Phase 0 PASSED. Proceed to Phase 1 Paper Trading.")
    else:
        reasons = []
        if not results.meets_profit_factor:
            reasons.append(f"PF={results.profit_factor:.2f} < 1.3")
        if not results.meets_max_drawdown:
            reasons.append(f"MaxDD={results.max_drawdown_pct:.1%} > 25%")
        print(f"  ❌  NO-GO — {', '.join(reasons)}")
        print("     Tune config.yaml thresholds and re-run before Phase 1.")
    print("=" * 65 + "\n")

    return 0 if results.go_live else 1


def _load_yfinance_data(cfg, start_dt, end_dt, log):
    """Load BTC-USD data from yfinance (free, no API key needed)."""
    import pandas as pd
    import yfinance as yf

    log.info("Yfinance mode: downloading BTC-USD data")

    # Download daily data with extra warmup
    warmup_start = start_dt - pd.Timedelta(days=400)

    print("  Downloading daily BTC-USD from yfinance...")
    raw = yf.download("BTC-USD", start=warmup_start, end=end_dt, interval="1d")

    if raw.empty:
        print("  ⚠  yfinance returned empty data")
        return pd.DataFrame(), pd.DataFrame()

    # Flatten MultiIndex columns (yfinance returns ('Close', 'BTC-USD'))
    raw.columns = [col[0] for col in raw.columns]

    # Build daily_df with expected column names
    daily_df = pd.DataFrame({
        "open_time": pd.to_datetime(raw.index),
        "open": raw["Open"].values,
        "high": raw["High"].values,
        "low": raw["Low"].values,
        "close": raw["Close"].values,
        "volume": raw["Volume"].values,
    })

    # Sort by time
    daily_df = daily_df.sort_values("open_time").reset_index(drop=True)

    # Filter to requested backtest range
    daily_df = daily_df[daily_df["open_time"] >= pd.Timestamp(start_dt)].copy()
    daily_df = daily_df.reset_index(drop=True)

    # Build weekly DataFrame from daily (resample)
    weekly_df = daily_df.copy()
    weekly_df = weekly_df.set_index("open_time")
    weekly_agg = weekly_df.resample("W").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna().reset_index()
    weekly_df = weekly_df.reset_index()

    # Extra warmup for weekly indicators
    weekly_warmup_start = start_dt - pd.Timedelta(days=400)
    weekly_df = weekly_agg[weekly_agg["open_time"] >= pd.Timestamp(weekly_warmup_start)].copy()
    weekly_df = weekly_df.reset_index(drop=True)

    print(f"  yfinance data: {len(daily_df)} daily rows, {len(weekly_df)} weekly rows")
    print(f"  First daily date: {daily_df['open_time'].min()}")
    print(f"  Data start check: {'✅ >= 2015-06-01' if daily_df['open_time'].min() <= pd.Timestamp('2015-06-01') else '⚠️  starts later than 2015-06-01'}")

    return daily_df, weekly_df


def _load_data(cfg, start_dt, end_dt, offline: bool, log):
    """Load daily and weekly candle data from cache or API."""
    import pandas as pd

    if offline:
        log.info("Offline mode: loading from SQLite cache only")
        from src.data.repository import get_candles_df
        daily_df = get_candles_df(
            cfg.general.product_id, "ONE_DAY", start_dt, end_dt
        )
        weekly_df = get_candles_df(
            cfg.general.product_id, "ONE_WEEK",
            start_dt - pd.Timedelta(days=365),  # extra for weekly EMA warmup
            end_dt,
        )
        return daily_df, weekly_df

    # Live mode: paginate from Coinbase API
    log.info("Fetching data from Coinbase API")

    try:
        api_key, api_secret = get_coinbase_credentials()
    except EnvironmentError as e:
        log.error("Missing Coinbase credentials", error=str(e))
        print(f"\n⚠  {e}")
        print("   Add COINBASE_API_KEY and COINBASE_API_SECRET to your .env file")
        print("   OR run with --offline to use cached data")
        print("   OR run with --yfinance to use free data\n")
        import pandas as pd
        return pd.DataFrame(), pd.DataFrame()

    from src.data.coinbase_client import CoinbaseClient
    from src.data.collector import collect_candles
    import pandas as pd

    client = CoinbaseClient(api_key=api_key, api_secret=api_secret)

    # Health check
    print("  Checking Coinbase API connectivity...")
    if not client.health_check():
        print("  ⚠  Coinbase API health check failed. Check credentials.")
        log.warning("Coinbase health check failed — attempting data fetch anyway")

    # Extra warmup period for indicators
    warmup_start = start_dt - pd.Timedelta(days=400)
    weekly_start = start_dt - pd.Timedelta(days=400)

    print(f"  Fetching daily candles (this may take 4-6 API calls)...")
    daily_df = collect_candles(
        client=client,
        product_id=cfg.general.product_id,
        granularity="ONE_DAY",
        start_dt=warmup_start,
        end_dt=end_dt,
        candles_per_request=cfg.data.candles_per_request,
        rate_limit_sleep=cfg.data.rate_limit_sleep_seconds,
        gap_detection=cfg.data.gap_detection,
    )

    print(f"  Fetching weekly candles...")
    weekly_df = collect_candles(
        client=client,
        product_id=cfg.general.product_id,
        granularity="ONE_WEEK",
        start_dt=weekly_start,
        end_dt=end_dt,
        candles_per_request=cfg.data.candles_per_request,
        rate_limit_sleep=cfg.data.rate_limit_sleep_seconds,
        gap_detection=cfg.data.gap_detection,
    )

    # Filter to requested backtest range (after loading with warmup)
    if not daily_df.empty:
        daily_df = daily_df[daily_df["open_time"] >= start_dt].copy()
    if not weekly_df.empty:
        weekly_df = weekly_df[weekly_df["open_time"] >= weekly_start].copy()

    return daily_df, weekly_df


if __name__ == "__main__":
    sys.exit(main())