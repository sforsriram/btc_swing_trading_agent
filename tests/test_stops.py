# tests/test_stops.py
"""
Unit tests for the 5-stage exit state machine.
Tests every stage transition and hard backstop.
"""
import pytest
from datetime import datetime, timedelta
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.stops import (
    StopStage, ExitReason,
    compute_initial_stop, compute_breakeven_stop, compute_trail_stop,
    evaluate_stop_machine,
)


ENTRY_PRICE = 50000.0
ATR = 1500.0
ENTRY_TIME = datetime(2024, 1, 1, 0, 0, 0)


def run_machine(
    current_price: float,
    stage: StopStage = StopStage.INITIAL,
    current_stop: float = 47500.0,
    highest_price: float = 50000.0,
    entry_time: datetime = ENTRY_TIME,
    current_time: datetime = None,
    scale_out_executed: bool = False,
    rsi: float = None,
    macd_hist: float = None,
    macd_hist_prev: float = None,
    **kwargs,
):
    if current_time is None:
        current_time = ENTRY_TIME + timedelta(days=1)
    return evaluate_stop_machine(
        current_price=current_price,
        stage=stage,
        current_stop=current_stop,
        entry_price=ENTRY_PRICE,
        atr_at_entry=ATR,
        highest_price=highest_price,
        entry_time=entry_time,
        current_time=current_time,
        scale_out_executed=scale_out_executed,
        rsi=rsi,
        macd_hist=macd_hist,
        macd_hist_prev=macd_hist_prev,
        **kwargs,
    )


class TestInitialStop:
    def test_pct_stop_calculation(self):
        stop = compute_initial_stop(50000, 1000, initial_stop_pct=0.025, initial_stop_atr_mult=1.5)
        pct_stop = 50000 * (1 - 0.025)  # 48750
        atr_stop = 50000 - 1.5 * 1000   # 48500
        assert abs(stop - min(pct_stop, atr_stop)) < 0.01

    def test_uses_more_conservative(self):
        """Should use lower stop price (more room = more conservative)."""
        stop = compute_initial_stop(50000, 5000, initial_stop_pct=0.025, initial_stop_atr_mult=1.5)
        pct_stop = 50000 * 0.975   # 48750
        atr_stop = 50000 - 7500   # 42500
        assert abs(stop - 42500) < 1.0  # ATR stop wins

    def test_stop_below_entry(self):
        stop = compute_initial_stop(50000, 1500)
        assert stop < 50000


class TestBreakevenStop:
    def test_above_entry(self):
        be = compute_breakeven_stop(50000, taker_rate=0.012)
        assert be > 50000

    def test_covers_round_trip_fees(self):
        entry = 50000
        rate = 0.012
        be = compute_breakeven_stop(entry, taker_rate=rate)
        # Break-even should cover ~2x taker fee
        expected_min = entry + entry * rate * 2
        assert be >= expected_min * 0.99


class TestTrailStop:
    def test_trail_below_highest(self):
        trail = compute_trail_stop(60000, 1500, trail_atr_mult=1.75)
        assert trail < 60000

    def test_trail_calculation(self):
        trail = compute_trail_stop(60000, 1500, trail_atr_mult=1.75)
        expected = 60000 - 1.75 * 1500  # 57375
        assert abs(trail - expected) < 0.01

    def test_higher_price_higher_trail(self):
        t1 = compute_trail_stop(55000, 1500)
        t2 = compute_trail_stop(60000, 1500)
        assert t2 > t1


class TestStage1Initial:
    def test_stop_hit_exits(self):
        result = run_machine(current_price=46000, current_stop=47000)
        assert result.should_exit_full is True
        assert result.exit_reason == ExitReason.STOP_LOSS

    def test_price_above_stop_no_exit(self):
        result = run_machine(current_price=52000, current_stop=47000)
        assert result.should_exit_full is False

    def test_transitions_to_breakeven_at_3pct(self):
        """Price at +3.1% net should trigger break-even transition."""
        # Net pnl = gross - 2*taker = 3.1% - 2.4% fees > 3%? No, let's use higher
        # With 1.2% taker * 2 = 2.4% round trip. Need gross > 5.4% for net > 3%
        price = ENTRY_PRICE * 1.056  # ~5.6% gross → ~3.2% net after fees
        result = run_machine(current_price=price, current_stop=47000)
        assert result.stage_changed is True
        assert result.new_stage in (StopStage.BREAKEVEN, StopStage.SCALED)

    def test_price_barely_at_stop(self):
        """Price exactly at stop should trigger exit."""
        stop = 47500.0
        result = run_machine(current_price=stop, current_stop=stop)
        assert result.should_exit_full is True

    def test_highest_price_updated(self):
        """highest_price should be updated when current price is higher."""
        result = run_machine(current_price=55000, highest_price=50000)
        assert result.highest_price == 55000


class TestStage2Breakeven:
    def test_stop_hit_exits(self):
        be_stop = compute_breakeven_stop(ENTRY_PRICE, taker_rate=0.012)
        result = run_machine(
            current_price=ENTRY_PRICE - 100,
            stage=StopStage.BREAKEVEN,
            current_stop=be_stop,
        )
        assert result.should_exit_full is True
        assert result.exit_reason == ExitReason.BREAKEVEN_STOP

    def test_transitions_to_scale_out_at_5pct(self):
        """At +5% net, should trigger scale-out (stage may advance further in same tick)."""
        price = ENTRY_PRICE * 1.076  # enough for net > 5%
        result = run_machine(
            current_price=price,
            stage=StopStage.BREAKEVEN,
            current_stop=ENTRY_PRICE * 1.001,
            highest_price=price,
            scale_out_executed=False,
        )
        assert result.should_scale_out is True
        # Stage may be SCALED or TRAILING depending on how far price moved
        assert result.new_stage in (StopStage.SCALED, StopStage.TRAILING)

    def test_no_double_scale_out(self):
        """Should not scale out if already executed."""
        price = ENTRY_PRICE * 1.08
        result = run_machine(
            current_price=price,
            stage=StopStage.BREAKEVEN,
            current_stop=ENTRY_PRICE * 1.001,
            scale_out_executed=True,
        )
        assert result.should_scale_out is False


class TestStage4Trailing:
    def test_trail_stop_only_moves_up(self):
        """Trail stop must never decrease."""
        high_stop = ENTRY_PRICE * 1.06
        result = run_machine(
            current_price=ENTRY_PRICE * 1.04,  # price pulled back
            stage=StopStage.TRAILING,
            current_stop=high_stop,
            highest_price=ENTRY_PRICE * 1.10,
        )
        # Stop should not go below what it was
        assert result.current_stop >= high_stop

    def test_trail_stop_hit_exits(self):
        result = run_machine(
            current_price=ENTRY_PRICE * 1.02,
            stage=StopStage.TRAILING,
            current_stop=ENTRY_PRICE * 1.05,  # stop above current price
            highest_price=ENTRY_PRICE * 1.10,
        )
        assert result.should_exit_full is True
        assert result.exit_reason == ExitReason.TRAIL_STOP

    def test_ceiling_exits_at_8pct(self):
        """At +8% net, should hit ceiling and exit."""
        price = ENTRY_PRICE * 1.106  # enough for net > 8%
        result = run_machine(
            current_price=price,
            stage=StopStage.TRAILING,
            current_stop=ENTRY_PRICE * 1.04,
            highest_price=price,
        )
        assert result.should_exit_full is True
        assert result.exit_reason == ExitReason.CEILING


class TestHardBackstops:
    def test_time_stop_7_days(self):
        """After 7 days with no TP/SL, should exit."""
        current_time = ENTRY_TIME + timedelta(days=8)
        result = run_machine(
            current_price=ENTRY_PRICE * 1.02,  # mild gain, no TP/SL hit
            stage=StopStage.INITIAL,
            current_stop=ENTRY_PRICE * 0.975,
            entry_time=ENTRY_TIME,
            current_time=current_time,
        )
        assert result.should_exit_full is True
        assert result.exit_reason == ExitReason.TIME_STOP

    def test_time_stop_not_triggered_before_7_days(self):
        current_time = ENTRY_TIME + timedelta(days=5)
        result = run_machine(
            current_price=ENTRY_PRICE * 1.02,
            stage=StopStage.INITIAL,
            current_stop=ENTRY_PRICE * 0.975,
            entry_time=ENTRY_TIME,
            current_time=current_time,
        )
        assert result.exit_reason != ExitReason.TIME_STOP

    def test_momentum_exit_overbought(self):
        """RSI > 70 + MACD hist turning negative → momentum exit."""
        result = run_machine(
            current_price=ENTRY_PRICE * 1.03,
            stage=StopStage.INITIAL,
            current_stop=ENTRY_PRICE * 0.975,
            rsi=75.0,
            macd_hist=-50.0,
            macd_hist_prev=30.0,
        )
        assert result.should_exit_full is True
        assert result.exit_reason == ExitReason.MOMENTUM_EXIT

    def test_closed_position_no_action(self):
        result = run_machine(
            current_price=ENTRY_PRICE,
            stage=StopStage.CLOSED,
            current_stop=ENTRY_PRICE * 0.975,
        )
        assert result.should_exit_full is False
        assert result.should_scale_out is False


class TestConfigDriven:
    def test_custom_thresholds_respected(self):
        """Custom config thresholds should override defaults."""
        price = ENTRY_PRICE * 1.042  # just above 4% gross
        result = run_machine(
            current_price=price,
            stage=StopStage.INITIAL,
            current_stop=ENTRY_PRICE * 0.975,
            breakeven_trigger_pct=0.01,   # 1% trigger (lower than default)
            taker_rate=0.0,               # no fees for this test
        )
        # 4% net with 1% trigger → should be in BREAKEVEN or higher
        assert result.stage in (StopStage.BREAKEVEN, StopStage.SCALED, StopStage.TRAILING)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
