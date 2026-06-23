# tests/test_sizing.py
"""
Unit tests for position sizing.
Tests risk math, precision rounding, and edge cases.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.position_sizing import calculate_position_size, round_btc, round_usd


class TestPositionSizing:
    """Risk = 1% of equity / (entry - stop) distance."""

    def test_basic_sizing(self):
        result = calculate_position_size(
            equity_usd=10000.0,
            entry_price=50000.0,
            atr=1500.0,
            risk_per_trade_pct=0.01,
        )
        assert result.is_viable
        assert result.size_btc > 0
        assert result.size_usd > 0

    def test_risk_amount_correct(self):
        """Dollar risk should be ~1% of equity."""
        equity = 10000.0
        result = calculate_position_size(
            equity_usd=equity,
            entry_price=50000.0,
            atr=1500.0,
            risk_per_trade_pct=0.01,
        )
        assert result.is_viable
        # Risk should be close to 1% of equity (with fee adjustment it will be slightly less)
        assert abs(result.risk_usd - equity * 0.01) / (equity * 0.01) < 0.20

    def test_small_account_200(self):
        """Should work with $200 account (Sriram's test rig size)."""
        result = calculate_position_size(
            equity_usd=200.0,
            entry_price=50000.0,
            atr=1500.0,
            risk_per_trade_pct=0.01,
            min_order_usd=1.0,
        )
        # $200 * 1% risk = $2 risk, still viable above $1 min
        assert result.is_viable
        assert result.size_usd >= 1.0

    def test_btc_precision_8_decimal(self):
        """BTC size must have at most 8 decimal places."""
        result = calculate_position_size(
            equity_usd=10000.0,
            entry_price=50000.0,
            atr=1500.0,
        )
        if result.is_viable:
            # Check no more than 8 decimal places
            size_str = f"{result.size_btc:.8f}"
            assert result.size_btc == float(size_str)

    def test_stop_price_below_entry(self):
        """Stop price must always be below entry price."""
        result = calculate_position_size(
            equity_usd=10000.0,
            entry_price=50000.0,
            atr=1500.0,
        )
        if result.is_viable:
            assert result.stop_price < 50000.0

    def test_stop_uses_larger_distance(self):
        """Should use whichever stop is further from entry (more conservative)."""
        entry = 50000.0
        atr = 5000.0  # large ATR → ATR stop is further
        result = calculate_position_size(
            equity_usd=10000.0,
            entry_price=entry,
            atr=atr,
            initial_stop_pct=0.025,   # 2.5% = 1250
            initial_stop_atr_mult=1.5,  # 1.5 * 5000 = 7500 → further
        )
        if result.is_viable:
            pct_stop = entry * (1 - 0.025)    # 48750
            atr_stop = entry - 1.5 * atr       # 42500
            expected_stop = min(pct_stop, atr_stop)  # 42500
            assert abs(result.stop_price - expected_stop) < 1.0

    def test_invalid_entry_price(self):
        result = calculate_position_size(
            equity_usd=10000.0,
            entry_price=0.0,
            atr=1500.0,
        )
        assert result.is_viable is False

    def test_zero_equity(self):
        result = calculate_position_size(
            equity_usd=0.0,
            entry_price=50000.0,
            atr=1500.0,
        )
        assert result.is_viable is False

    def test_below_min_order_not_viable(self):
        """Very tiny equity with high min order should be not viable."""
        # $5 equity at 1% risk = $0.05 expected risk — size will be tiny
        # Use a very high min_order_usd to force failure
        result = calculate_position_size(
            equity_usd=5.0,
            entry_price=50000.0,
            atr=1500.0,
            risk_per_trade_pct=0.01,
            min_order_usd=10.0,  # min $10 — too high for $5 equity
        )
        assert result.is_viable is False
        assert "minimum" in result.reason.lower()

    def test_size_does_not_exceed_95_pct_equity(self):
        """Position should never exceed 95% of equity."""
        result = calculate_position_size(
            equity_usd=10000.0,
            entry_price=50000.0,
            atr=0.1,  # tiny ATR → very tight stop → large position
            risk_per_trade_pct=0.50,  # extreme risk (to force cap)
        )
        if result.is_viable:
            assert result.size_usd <= 10000 * 0.95

    def test_higher_equity_proportional_size(self):
        """Doubling equity should roughly double position size."""
        r1 = calculate_position_size(10000, 50000, 1500, 0.01)
        r2 = calculate_position_size(20000, 50000, 1500, 0.01)
        if r1.is_viable and r2.is_viable:
            ratio = r2.size_btc / r1.size_btc
            assert 1.8 < ratio < 2.2  # ~2x with fee rounding


class TestRoundBTC:
    def test_rounds_down(self):
        """Should always round DOWN to preserve risk budget."""
        result = round_btc(0.123456789, 8)
        assert result == 0.12345678  # truncated, not rounded

    def test_exact_value_unchanged(self):
        assert round_btc(0.12345678, 8) == 0.12345678

    def test_large_value(self):
        assert round_btc(1.999999999, 8) == 1.99999999


class TestRoundUSD:
    def test_rounds_to_2_decimals(self):
        assert round_usd(100.1234) == 100.12

    def test_standard_value(self):
        assert round_usd(50000.005) == 50000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
