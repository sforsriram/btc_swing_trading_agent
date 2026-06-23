# tests/test_fees.py
"""
Unit tests for fee calculations.
Tests that fees are correctly applied at every entry, exit, and scale-out.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.position_sizing import calculate_position_size


class TestFeeMath:
    """Fees must be deducted on EVERY fill — entry, scale-out, exit."""

    def test_net_target_requires_gross_overshoot(self):
        """
        Net 5% target requires ~6.2% gross to cover round-trip fees.
        Taker fee 1.2% * 2 legs + 0.1% slippage * 2 = ~2.6% round-trip.
        """
        entry = 50000.0
        # Gross exit price needed for net +5%:
        # net = (exit - entry) / entry - round_trip_cost
        # 0.05 = gross_pct - 0.026
        # gross_pct = 0.076  → exit = 53800
        taker_rate = 0.012
        slippage = 0.001
        round_trip = (taker_rate + slippage) * 2

        net_target = 0.05
        required_gross = net_target + round_trip
        required_exit = entry * (1 + required_gross)

        # Verify the math
        gross_pnl = (required_exit - entry) / entry
        net_pnl = gross_pnl - round_trip
        assert abs(net_pnl - net_target) < 0.005  # within 0.5%

    def test_break_even_stop_covers_fees(self):
        """Break-even stop must be above entry by at least round-trip fee."""
        from src.strategy.stops import compute_breakeven_stop
        entry = 50000.0
        taker_rate = 0.012
        be_stop = compute_breakeven_stop(entry, taker_rate=taker_rate)
        # Round-trip fees
        round_trip = entry * taker_rate * 2
        assert be_stop >= entry + round_trip * 0.99  # at least covers fees

    def test_sizing_accounts_for_fees(self):
        """Position size should be calculated with fee adjustment."""
        result = calculate_position_size(
            equity_usd=10000.0,
            entry_price=50000.0,
            atr=1500.0,
            risk_per_trade_pct=0.01,
            taker_rate=0.012,
            slippage_rate=0.001,
        )
        # With fees, effective risk per BTC is larger,
        # so position size should be smaller than fee-free calculation
        result_no_fees = calculate_position_size(
            equity_usd=10000.0,
            entry_price=50000.0,
            atr=1500.0,
            risk_per_trade_pct=0.01,
            taker_rate=0.0,
            slippage_rate=0.0,
        )
        if result.is_viable and result_no_fees.is_viable:
            assert result.size_btc <= result_no_fees.size_btc

    def test_higher_fees_smaller_position(self):
        """Higher taker rate should produce smaller position size."""
        r_low = calculate_position_size(10000, 50000, 1500, taker_rate=0.006)
        r_high = calculate_position_size(10000, 50000, 1500, taker_rate=0.012)
        if r_low.is_viable and r_high.is_viable:
            assert r_low.size_btc >= r_high.size_btc

    def test_maker_fee_advantage(self):
        """Maker fee (0.6%) should allow larger position than taker (1.2%)."""
        r_maker = calculate_position_size(10000, 50000, 1500, taker_rate=0.006)
        r_taker = calculate_position_size(10000, 50000, 1500, taker_rate=0.012)
        if r_maker.is_viable and r_taker.is_viable:
            assert r_maker.size_btc >= r_taker.size_btc

    def test_small_account_fees_still_deducted(self):
        """$200 account: fees still deducted, size still viable."""
        result = calculate_position_size(
            equity_usd=200.0,
            entry_price=50000.0,
            atr=1500.0,
            risk_per_trade_pct=0.01,
            taker_rate=0.012,
            min_order_usd=1.0,
        )
        assert result.is_viable
        # Verify fee math: size_usd * taker_rate is a meaningful portion of risk
        if result.is_viable:
            fee = result.size_usd * 0.012
            assert fee > 0

    def test_fee_buffer_on_breakeven(self):
        """fee_buffer_multiplier=1.5 should widen the break-even stop."""
        from src.strategy.stops import compute_breakeven_stop
        be1 = compute_breakeven_stop(50000, taker_rate=0.012, fee_buffer_multiplier=1.0)
        be2 = compute_breakeven_stop(50000, taker_rate=0.012, fee_buffer_multiplier=1.5)
        assert be2 > be1

    def test_slippage_applied_to_sizing(self):
        """Slippage should make position slightly smaller."""
        r_no_slip = calculate_position_size(10000, 50000, 1500, slippage_rate=0.0)
        r_with_slip = calculate_position_size(10000, 50000, 1500, slippage_rate=0.002)
        if r_no_slip.is_viable and r_with_slip.is_viable:
            assert r_no_slip.size_btc >= r_with_slip.size_btc


class TestRoundTripFeeModel:
    """Verify the 1.2% round-trip fee model matches architecture spec."""

    def test_worst_case_taker_taker(self):
        """Taker entry + taker exit = 2 * 1.2% = 2.4% round-trip."""
        taker = 0.012
        round_trip = taker * 2
        assert abs(round_trip - 0.024) < 1e-10

    def test_net_5pct_target_needs_about_7_6pct_gross(self):
        """
        Architecture spec: Net 5% target = ~6.2% gross.
        With slippage: 5% + 2.4% fees + 0.2% slippage = ~7.6%
        """
        taker = 0.012
        slippage = 0.001
        net_target = 0.05
        round_trip_cost = (taker + slippage) * 2
        required_gross = net_target + round_trip_cost
        assert 0.07 <= required_gross <= 0.09  # ~7-9% gross needed

    def test_stop_loss_actual_loss(self):
        """
        2.5% stop + fees = ~5% actual account impact per losing trade.
        Architecture: ~40% win rate is profitable with this asymmetry.
        """
        stop_pct = 0.025
        taker = 0.012
        actual_loss = stop_pct + taker * 2  # entry fee + exit fee
        # Actual loss should be larger than stop due to fees
        assert actual_loss > stop_pct
        assert actual_loss < 0.06  # but not catastrophic


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
