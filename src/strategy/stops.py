# src/strategy/stops.py
"""
Minimalist Exit State Machine
=============================
3 stages only:

STAGE 1 — INITIAL STOP: max(2.0 x ATR14, entry_price x 0.035)
  Uses the wider of 2×ATR or 3.5% to protect against BTC wick noise.
  If price hits stop → exit full position.

STAGE 2 — SCALE-OUT: at entry + 6.5%, sell 50% of position
  Move stop to breakeven (entry_price) for the remaining 50% runner.

STAGE 3 — TRAIL STOP: highest_close_since_entry - (2.25 x ATR14)
  Trail uses highest CLOSE (not high) to avoid wick noise.
  ATR is recalculated daily (not fixed at entry).
  Trail only moves up, never down.
  No hard take-profit, no time stop.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from src.core.logging_setup import get_logger

log = get_logger("stops")


class ExitReason(str, Enum):
    NONE = "none"
    STOP_LOSS = "stop_loss"
    SCALE_OUT = "scale_out"
    TRAIL_STOP = "trail_stop"
    CIRCUIT_BREAKER = "circuit_breaker"
    MANUAL_KILL = "manual_kill"
    END_OF_DATA = "end_of_data"


class StopStage(str, Enum):
    INITIAL = "INITIAL"
    SCALED = "SCALED"
    TRAILING = "TRAILING"
    CLOSED = "CLOSED"


@dataclass
class StopUpdate:
    """Result of evaluating the stop machine for one daily bar."""
    stage: StopStage
    current_stop: float
    highest_close: float
    should_exit_full: bool = False
    should_scale_out: bool = False
    exit_reason: ExitReason = ExitReason.NONE
    stage_changed: bool = False
    new_stage: StopStage | None = None
    explanation: str = ""


def compute_initial_stop(
    entry_price: float,
    atr: float,
    initial_stop_atr_mult: float = 2.0,
    initial_stop_pct: float = 0.035,
) -> float:
    """
    Stage 1 initial stop loss.
    Uses the WIDER of 2×ATR or 3.5% of entry price.
    This protects against BTC wick noise while keeping stops tight enough.
    """
    stop_atr = entry_price - (initial_stop_atr_mult * atr)
    stop_pct = entry_price * (1 - initial_stop_pct)
    return min(stop_atr, stop_pct)  # lower price = wider stop


def compute_trail_stop(
    highest_close: float,
    atr_current: float,
    trail_atr_mult: float = 2.25,
) -> float:
    """
    Stage 3 trailing stop.
    Trail = highest_close_since_entry - (trail_atr_mult x ATR14)
    ATR is recalculated daily (not fixed at entry).
    Uses highest CLOSE (not high) to avoid wick noise.
    """
    return highest_close - (trail_atr_mult * atr_current)


def evaluate_stop_machine(
    current_price: float,
    stage: StopStage,
    current_stop: float,
    entry_price: float,
    atr_at_entry: float,
    highest_close: float,
    scale_out_executed: bool,
    scale_out_trigger_pct: float = 0.065,
    scale_out_fraction: float = 0.50,
    trail_atr_mult: float = 2.25,
    atr_current: float | None = None,
) -> StopUpdate:
    """
    Evaluate the exit state machine for one daily bar.

    Args:
        current_price: current bar close price
        stage: current stop stage
        current_stop: current stop price
        entry_price: trade entry price
        atr_at_entry: ATR value at time of entry (for initial stop reference)
        highest_close: highest close price since entry
        scale_out_executed: whether 50% scale-out has already happened
        scale_out_trigger_pct: % gain to trigger scale-out (0.065 = +6.5%)
        scale_out_fraction: fraction to sell at scale-out (0.50 = 50%)
        trail_atr_mult: ATR multiplier for trailing stop (2.25)
        atr_current: current ATR value (recalculated daily, used for trail)
    """
    if stage == StopStage.CLOSED:
        return StopUpdate(
            stage=stage, current_stop=current_stop, highest_close=highest_close,
            explanation="Position already closed"
        )

    new_highest_close = max(highest_close, current_price)
    pnl_pct = (current_price - entry_price) / entry_price

    result = StopUpdate(
        stage=stage,
        current_stop=current_stop,
        highest_close=new_highest_close,
    )

    # ---- Stage 1: Initial Stop ----
    if stage == StopStage.INITIAL:
        if current_price <= current_stop:
            result.should_exit_full = True
            result.exit_reason = ExitReason.STOP_LOSS
            result.new_stage = StopStage.CLOSED
            result.stage_changed = True
            result.explanation = f"Stop loss: price={current_price:.2f} <= stop={current_stop:.2f}"
            return result

        # Check for scale-out trigger (+6.5%)
        if not scale_out_executed and pnl_pct >= scale_out_trigger_pct:
            result.should_scale_out = True
            result.stage = StopStage.SCALED
            result.stage_changed = True
            result.new_stage = StopStage.SCALED
            result.current_stop = entry_price  # move to breakeven
            result.explanation = f"Scale-out at +{pnl_pct:.2%}. Stop moved to breakeven."
            log.info("Scale-out triggered", pnl=pnl_pct)
            return result

        result.explanation = f"Stage 1: price={current_price:.2f} stop={current_stop:.2f} pnl={pnl_pct:.2%}"
        return result

    # ---- Stage 2/3: After scale-out (trailing the remainder) ----
    if stage in (StopStage.SCALED, StopStage.TRAILING):
        # Check if breakeven/trail stop hit
        if current_price <= current_stop:
            result.should_exit_full = True
            result.exit_reason = ExitReason.TRAIL_STOP
            result.new_stage = StopStage.CLOSED
            result.stage_changed = True
            result.explanation = f"Trail stop: price={current_price:.2f} <= stop={current_stop:.2f}"
            return result

        # Update trailing stop (never moves down)
        atr_for_trail = atr_current if atr_current is not None else atr_at_entry
        trail_stop = compute_trail_stop(new_highest_close, atr_for_trail, trail_atr_mult)
        new_stop = max(trail_stop, current_stop)

        if new_stop > current_stop:
            result.current_stop = new_stop
            if result.stage != StopStage.TRAILING:
                result.stage = StopStage.TRAILING
                result.new_stage = StopStage.TRAILING
                result.stage_changed = True

        result.explanation = (
            f"Stage 3: trail={result.current_stop:.2f} "
            f"price={current_price:.2f} highest_close={new_highest_close:.2f}"
        )
        return result

    result.explanation = f"Unknown stage: {stage}"
    return result