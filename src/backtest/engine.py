# src/backtest/engine.py
"""
Minimalist BTC swing trading backtest engine.
=============================================
Design philosophy: simplicity is a feature.
- No confluence scoring, no confirmation candles, no precision proximity
- 3 entry conditions: regime + RSI pullback + EMA20 proximity
- 3 exit stages: initial stop → scale-out → trail

All indicators pre-computed before the simulation loop (no lookahead).
ATR uses Wilder smoothing (EMA with alpha=1/period).
"""
from __future__ import annotations
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from src.core.logging_setup import get_logger
from src.strategy.indicators import add_all_indicators, add_weekly_ema
from src.strategy.position_sizing import calculate_position_size
from src.strategy.stops import (
    StopStage, ExitReason,
    compute_initial_stop, evaluate_stop_machine
)

log = get_logger("backtest")

# Single configurable fee constant (0.3% per side = 0.6% round-trip)
FEE_PER_SIDE = 0.003


@dataclass
class BacktestTrade:
    trade_id: str
    entry_date: datetime
    entry_price: float
    entry_size_btc: float
    entry_size_usd: float
    entry_fees: float
    stop_at_entry: float
    atr_at_entry: float

    exit_date: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_size_btc: Optional[float] = None
    exit_fees: float = 0.0
    exit_reason: str = ""

    # Scale-out tracking
    scale_out_date: Optional[datetime] = None
    scale_out_price: Optional[float] = None
    scale_out_size_btc: float = 0.0
    scale_out_fees: float = 0.0

    # P&L (computed at close)
    gross_pnl_usd: float = 0.0
    net_pnl_usd: float = 0.0
    net_pnl_pct: float = 0.0
    r_multiple: float = 0.0
    hold_days: float = 0.0
    total_fees: float = 0.0
    stop_stage_at_exit: str = ""


@dataclass
class BacktestState:
    """Mutable state during backtest simulation."""
    equity: float
    cash: float
    position: Optional[BacktestTrade] = None
    stop_stage: StopStage = StopStage.INITIAL
    current_stop: float = 0.0
    highest_close: float = 0.0
    scale_out_executed: bool = False
    remaining_size_btc: float = 0.0
    week_start_equity: float = 0.0
    week_start_date: Optional[datetime] = None


@dataclass
class FilterAttribution:
    """Tracks how many times each filter blocked an entry."""
    ema50_below_ema200: int = 0
    weekly_ema_not_rising: int = 0
    rsi_ge_50: int = 0
    price_beyond_5pct: int = 0
    total_passed_all: int = 0
    total_considered: int = 0


@dataclass
class BacktestResults:
    """Output metrics from a completed backtest run."""
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_hold_days: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    exposure_pct: float = 0.0
    total_fees_usd: float = 0.0
    net_return_pct: float = 0.0
    r_multiples: list[float] = field(default_factory=list)
    avg_r_multiple: float = 0.0
    days_armed: int = 0
    days_flat: int = 0
    filter_attribution: FilterAttribution = field(default_factory=FilterAttribution)
    yearly_stats: list[dict] = field(default_factory=list)
    meets_profit_factor: bool = False
    meets_max_drawdown: bool = False
    go_live: bool = False
    in_sample_results: Optional["BacktestResults"] = None
    out_of_sample_results: Optional["BacktestResults"] = None
    rsi_threshold_used: float = 50.0
    proximity_pct_used: float = 5.0
    weekly_regime_used: bool = True


def run_backtest(
    daily_df: pd.DataFrame,
    weekly_df: pd.DataFrame,
    cfg,
    rsi_threshold: float = 50.0,
    proximity_pct: float = 5.0,
    use_weekly_regime: bool = True,
) -> BacktestResults:
    """
    Run minimalist backtest over historical data.

    Args:
        daily_df: daily OHLCV DataFrame
        weekly_df: weekly OHLCV DataFrame
        cfg: AppConfig
        rsi_threshold: RSI below this = pullback (default 50)
        proximity_pct: % proximity to EMA20 (default 5%)
        use_weekly_regime: whether to include weekly EMA21 rising check
    """
    bc = cfg.backtest
    fc = cfg.fees
    ec = cfg.exits
    rc = cfg.risk
    ic = cfg.indicators
    reg = cfg.regime
    acc = cfg.account

    if daily_df.empty or weekly_df.empty:
        log.warning("Empty input DataFrames")
        return BacktestResults()

    log.info("Starting backtest", start=bc.start_date, end=bc.end_date)

    # ---- Prepare indicators ----
    df = add_all_indicators(
        daily_df,
        rsi_period=ic.rsi_period,
        macd_fast=ic.macd_fast,
        macd_slow=ic.macd_slow,
        macd_signal=ic.macd_signal,
        bb_period=ic.bollinger_period,
        bb_std=ic.bollinger_std,
        atr_period=ic.atr_period,
        adx_period=reg.adx_period,
        ema_fast=reg.ema_fast,
        ema_slow=reg.ema_slow,
        volume_avg_period=ic.volume_avg_period,
    )

    df = add_weekly_ema(df, weekly_df, weekly_ema_period=reg.weekly_ema)

    # Filter to backtest range
    start_dt = pd.to_datetime(bc.start_date)
    end_dt = pd.to_datetime(bc.end_date)
    df = df[(df["open_time"] >= start_dt) & (df["open_time"] <= end_dt)].copy()
    df = df.reset_index(drop=True)

    if df.empty:
        log.error("No data in backtest range")
        return BacktestResults()

    log.info(f"Data: {len(df)} daily candles, {df['open_time'].min()} → {df['open_time'].max()}")

    # ---- Initialize state ----
    state = BacktestState(
        equity=bc.initial_equity,
        cash=bc.initial_equity,
        week_start_equity=bc.initial_equity,
        week_start_date=df["open_time"].iloc[0],
    )

    completed_trades: list[BacktestTrade] = []
    equity_history: list[dict] = []
    days_armed = 0
    days_flat = 0
    circuit_breaker_active = False
    filter_attr = FilterAttribution()
    pending_entry = None  # stores (entry_price, reasons) to execute at next open

    # ---- Main simulation loop ----
    for i, row in df.iterrows():
        current_date = pd.to_datetime(row["open_time"])
        current_price = float(row["close"])
        open_price = float(row["open"])

        # Weekly circuit breaker reset
        if state.week_start_date is not None:
            days_since_week_start = (current_date - state.week_start_date).days
            if days_since_week_start >= 7:
                weekly_dd = (state.equity - state.week_start_equity) / state.week_start_equity
                if rc.enable_circuit_breaker and weekly_dd <= -rc.weekly_drawdown_circuit_breaker_pct:
                    circuit_breaker_active = True
                else:
                    circuit_breaker_active = False
                state.week_start_equity = state.equity
                state.week_start_date = current_date

        # ---- Execute pending entry at OPEN (from previous bar's signal) ----
        if pending_entry is not None and state.position is None and not circuit_breaker_active:
            entry_price = open_price  # enter at next open
            atr = pending_entry["atr"]

            size_result = calculate_position_size(
                equity_usd=state.equity,
                entry_price=entry_price,
                atr=atr,
                risk_per_trade_pct=rc.risk_per_trade_pct,
                initial_stop_pct=ec.initial_stop_pct,
                initial_stop_atr_mult=ec.initial_stop_atr_mult,
                min_order_usd=acc.min_order_usd,
                base_precision=acc.base_precision,
                taker_rate=FEE_PER_SIDE,
                slippage_rate=fc.slippage_rate,
            )

            if size_result.is_viable and state.cash >= size_result.size_usd:
                entry_fee = size_result.size_btc * entry_price * FEE_PER_SIDE
                entry_cost = size_result.size_usd + entry_fee

                if state.cash >= entry_cost:
                    state.cash -= entry_cost
                    state.remaining_size_btc = size_result.size_btc
                    state.current_stop = size_result.stop_price
                    state.highest_close = entry_price
                    state.stop_stage = StopStage.INITIAL
                    state.scale_out_executed = False

                    trade = BacktestTrade(
                        trade_id=str(uuid.uuid4())[:8],
                        entry_date=current_date,
                        entry_price=entry_price,
                        entry_size_btc=size_result.size_btc,
                        entry_size_usd=size_result.size_usd,
                        entry_fees=entry_fee,
                        stop_at_entry=size_result.stop_price,
                        atr_at_entry=atr,
                    )
                    state.position = trade
                    filter_attr.total_passed_all += 1
                    days_armed += 1
                    log.info("ENTRY", date=current_date, price=entry_price,
                             size_btc=size_result.size_btc, stop=size_result.stop_price)

            pending_entry = None

        # ---- Monitor open position ----
        if state.position is not None:
            pos = state.position

            atr_current = _safe_float(row, "atr")

            stop_result = evaluate_stop_machine(
                current_price=current_price,
                stage=state.stop_stage,
                current_stop=state.current_stop,
                entry_price=pos.entry_price,
                atr_at_entry=pos.atr_at_entry,
                highest_close=state.highest_close,
                scale_out_executed=state.scale_out_executed,
                scale_out_trigger_pct=ec.scale_out_trigger_pct,
                scale_out_fraction=ec.scale_out_fraction,
                trail_atr_mult=ec.trail_atr_mult,
                atr_current=atr_current,
            )

            state.highest_close = stop_result.highest_close
            state.current_stop = stop_result.current_stop
            if stop_result.stage_changed and stop_result.new_stage:
                state.stop_stage = stop_result.new_stage

            # Execute scale-out
            if stop_result.should_scale_out and not state.scale_out_executed:
                scale_size = state.remaining_size_btc * ec.scale_out_fraction
                scale_fee = scale_size * current_price * FEE_PER_SIDE
                scale_proceed = scale_size * current_price * (1 - fc.slippage_rate) - scale_fee

                pos.scale_out_date = current_date
                pos.scale_out_price = current_price
                pos.scale_out_size_btc = scale_size
                pos.scale_out_fees = scale_fee

                state.remaining_size_btc -= scale_size
                state.cash += scale_proceed
                state.scale_out_executed = True
                log.info("Scale-out", date=current_date, price=current_price, size=scale_size)

            # Execute full exit
            if stop_result.should_exit_full:
                trade = _close_trade(
                    pos=pos,
                    exit_date=current_date,
                    exit_price=current_price,
                    remaining_btc=state.remaining_size_btc,
                    exit_reason=stop_result.exit_reason.value,
                    stop_stage=state.stop_stage.value,
                    state=state,
                )
                completed_trades.append(trade)

                state.position = None
                state.stop_stage = StopStage.INITIAL
                state.current_stop = 0.0
                state.highest_close = 0.0
                state.scale_out_executed = False
                state.remaining_size_btc = 0.0

        # ---- Entry decision (only when flat) ----
        if state.position is None and not circuit_breaker_active and pending_entry is None:
            filter_attr.total_considered += 1

            ema_50 = _safe_float(row, "ema_50")
            ema_200 = _safe_float(row, "ema_200")
            rsi = _safe_float(row, "rsi")
            ema_20 = _safe_float(row, "ema_20")
            close = _safe_float(row, "close")
            weekly_rising = _safe_bool(row, "weekly_ema_rising")
            atr = _safe_float(row, "atr")

            # REGIME: EMA50 > EMA200
            cond_regime = ema_50 is not None and ema_200 is not None and ema_50 > ema_200
            if not cond_regime:
                filter_attr.ema50_below_ema200 += 1

            # REGIME: Weekly EMA21 rising (optional)
            if cond_regime and use_weekly_regime:
                if not (weekly_rising is not None and weekly_rising):
                    cond_regime = False
                    filter_attr.weekly_ema_not_rising += 1

            # ENTRY: RSI < threshold
            cond_rsi = cond_regime and rsi is not None and rsi < rsi_threshold
            if cond_regime and not (rsi is not None and rsi < rsi_threshold):
                filter_attr.rsi_ge_50 += 1

            # ENTRY: Price within proximity_pct% of EMA20
            cond_proximity = False
            if cond_regime and cond_rsi and close is not None and ema_20 is not None and ema_20 > 0:
                pct_from_ema = abs(close - ema_20) / ema_20 * 100
                if pct_from_ema <= proximity_pct:
                    cond_proximity = True
            if cond_regime and cond_rsi and not cond_proximity:
                filter_attr.price_beyond_5pct += 1

            # Signal to enter at NEXT open
            if cond_regime and cond_rsi and cond_proximity:
                pending_entry = {
                    "atr": atr or (close * 0.02 if close else current_price * 0.02),
                }
            else:
                days_flat += 1

        # ---- Track equity ----
        open_value = (
            state.remaining_size_btc * current_price
            if state.position is not None
            else 0.0
        )
        total_equity = state.cash + open_value
        state.equity = total_equity
        equity_history.append({
            "date": current_date,
            "equity": total_equity,
            "cash": state.cash,
            "open_value": open_value,
        })

    # Close any open position at end of data
    if state.position is not None:
        last_row = df.iloc[-1]
        last_price = float(last_row["close"])
        trade = _close_trade(
            pos=state.position,
            exit_date=pd.to_datetime(last_row["open_time"]),
            exit_price=last_price,
            remaining_btc=state.remaining_size_btc,
            exit_reason="end_of_data",
            stop_stage=state.stop_stage.value,
            state=state,
        )
        completed_trades.append(trade)

    # ---- Compute metrics ----
    equity_df = pd.DataFrame(equity_history)
    results = _compute_metrics(
        trades=completed_trades,
        equity_df=equity_df,
        initial_equity=bc.initial_equity,
        days_armed=days_armed,
        days_flat=days_flat,
        success_min_pf=bc.success_min_profit_factor,
        success_max_dd=bc.success_max_drawdown_pct,
    )
    results.filter_attribution = filter_attr
    results.yearly_stats = _compute_yearly_stats(completed_trades)
    results.rsi_threshold_used = rsi_threshold
    results.proximity_pct_used = proximity_pct
    results.weekly_regime_used = use_weekly_regime

    # ---- Out-of-sample split ----
    if bc.parameter_sensitivity_check and bc.out_of_sample_split_date:
        split_dt = pd.to_datetime(bc.out_of_sample_split_date)
        in_trades = [t for t in completed_trades if t.entry_date < split_dt]
        out_trades = [t for t in completed_trades if t.entry_date >= split_dt]
        in_equity = equity_df[equity_df["date"] < split_dt]
        out_equity = equity_df[equity_df["date"] >= split_dt]

        results.in_sample_results = _compute_metrics(
            in_trades, in_equity, bc.initial_equity, 0, 0,
            bc.success_min_profit_factor, bc.success_max_drawdown_pct,
        )
        results.out_of_sample_results = _compute_metrics(
            out_trades, out_equity,
            in_equity["equity"].iloc[-1] if not in_equity.empty else bc.initial_equity,
            0, 0,
            bc.success_min_profit_factor, bc.success_max_drawdown_pct,
        )

    log.info("Backtest complete", trades=results.total_trades,
             win_rate=f"{results.win_rate:.1%}", pf=f"{results.profit_factor:.2f}")

    return results


def _close_trade(
    pos: BacktestTrade,
    exit_date: datetime,
    exit_price: float,
    remaining_btc: float,
    exit_reason: str,
    stop_stage: str,
    state: BacktestState,
) -> BacktestTrade:
    """Finalize a trade — compute all P&L metrics."""
    exit_fee = remaining_btc * exit_price * FEE_PER_SIDE
    exit_proceed = remaining_btc * exit_price - exit_fee

    total_fees = pos.entry_fees + pos.scale_out_fees + exit_fee

    scale_proceed = (
        pos.scale_out_size_btc * pos.scale_out_price - pos.scale_out_fees
        if pos.scale_out_size_btc > 0 and pos.scale_out_price
        else 0
    )
    total_receive = scale_proceed + exit_proceed
    net_pnl = total_receive - (pos.entry_size_usd + pos.entry_fees)
    net_pnl_pct = net_pnl / pos.entry_size_usd

    initial_risk = pos.entry_size_usd - (pos.stop_at_entry * pos.entry_size_btc)
    r_multiple = net_pnl / initial_risk if initial_risk > 0 else 0

    hold_days = (exit_date - pos.entry_date).total_seconds() / 86400

    pos.exit_date = exit_date
    pos.exit_price = exit_price
    pos.exit_size_btc = remaining_btc
    pos.exit_fees = exit_fee
    pos.exit_reason = exit_reason
    pos.stop_stage_at_exit = stop_stage
    pos.net_pnl_usd = net_pnl
    pos.net_pnl_pct = net_pnl_pct
    pos.r_multiple = r_multiple
    pos.hold_days = hold_days
    pos.total_fees = total_fees

    state.cash += exit_proceed
    return pos


def _compute_metrics(
    trades: list[BacktestTrade],
    equity_df: pd.DataFrame,
    initial_equity: float,
    days_armed: int,
    days_flat: int,
    success_min_pf: float,
    success_max_dd: float,
) -> BacktestResults:
    """Compute all performance metrics."""
    results = BacktestResults(
        trades=trades, equity_curve=equity_df,
        days_armed=days_armed, days_flat=days_flat,
    )

    if not trades:
        return results

    results.total_trades = len(trades)
    winners = [t for t in trades if t.net_pnl_pct > 0]
    losers = [t for t in trades if t.net_pnl_pct <= 0]

    results.winning_trades = len(winners)
    results.losing_trades = len(losers)
    results.win_rate = len(winners) / len(trades) if trades else 0

    gross_wins = sum(t.net_pnl_usd for t in winners)
    gross_losses = abs(sum(t.net_pnl_usd for t in losers))
    results.profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    results.avg_win_pct = np.mean([t.net_pnl_pct for t in winners]) if winners else 0
    results.avg_loss_pct = np.mean([t.net_pnl_pct for t in losers]) if losers else 0
    results.avg_hold_days = np.mean([t.hold_days for t in trades])
    results.total_fees_usd = sum(t.total_fees for t in trades)
    results.r_multiples = [t.r_multiple for t in trades]
    results.avg_r_multiple = np.mean(results.r_multiples) if results.r_multiples else 0

    if not equity_df.empty:
        final_equity = equity_df["equity"].iloc[-1]
        results.net_return_pct = (final_equity - initial_equity) / initial_equity
        eq = equity_df["equity"]
        roll_max = eq.cummax()
        drawdown = (eq - roll_max) / roll_max
        results.max_drawdown_pct = abs(drawdown.min())

    if not equity_df.empty and len(equity_df) > 1:
        daily_returns = equity_df["equity"].pct_change().dropna()
        if daily_returns.std() > 0:
            results.sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

    total_days = days_armed + days_flat
    if total_days > 0:
        days_in_trade = sum(t.hold_days for t in trades)
        results.exposure_pct = days_in_trade / total_days

    results.meets_profit_factor = results.profit_factor >= success_min_pf
    results.meets_max_drawdown = results.max_drawdown_pct <= success_max_dd
    results.go_live = results.meets_profit_factor and results.meets_max_drawdown

    return results


def _compute_yearly_stats(trades: list[BacktestTrade]) -> list[dict]:
    """Year-by-year breakdown."""
    if not trades:
        return []
    yearly = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "net_pnl_usd": 0.0})
    for t in trades:
        year = t.entry_date.year
        yearly[year]["trades"] += 1
        if t.net_pnl_pct > 0:
            yearly[year]["wins"] += 1
        else:
            yearly[year]["losses"] += 1
        yearly[year]["net_pnl_usd"] += t.net_pnl_usd

    result = []
    for year in sorted(yearly.keys()):
        yr = yearly[year]
        result.append({
            "year": year,
            "trades": yr["trades"],
            "win_rate": yr["wins"] / yr["trades"] if yr["trades"] > 0 else 0.0,
            "net_pnl_usd": round(yr["net_pnl_usd"], 2),
        })
    return result


def _safe_float(row: pd.Series, col: str) -> float | None:
    val = row.get(col)
    if val is None or pd.isna(val):
        return None
    return float(val)


def _safe_bool(row: pd.Series, col: str, default: bool = False) -> bool:
    val = row.get(col)
    if val is None or pd.isna(val):
        return default
    return bool(val)