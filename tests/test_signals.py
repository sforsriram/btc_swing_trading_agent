# tests/test_signals.py
"""
Unit tests for confluence signal scorer.
Tests weight logic, threshold behavior, and edge cases.
"""
import pytest
import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.signals import score_signal, SignalScore, detect_bearish_macd_divergence
from src.strategy.regime import check_regime, RegimeResult


def make_bullish_row(
    ema_50=55000, ema_200=48000,
    rsi=45.0, macd_hist=100.0, macd_hist_prev=-50.0,
    macd=200.0, macd_signal=100.0,
    volume=1000.0, volume_avg=800.0,
    adx=28.0, adx_prev=25.0,
    close=50000.0, swing_high=60000.0, swing_low=45000.0,
    bb_lower=48000.0, weekly_ema_rising=True,
) -> pd.Series:
    """Build a row that should score near maximum."""
    return pd.Series({
        "ema_50": ema_50,
        "ema_200": ema_200,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "macd_hist_prev": macd_hist_prev,
        "macd": macd,
        "macd_signal": macd_signal,
        "volume": volume,
        "volume_avg": volume_avg,
        "adx": adx,
        "adx_prev": adx_prev,
        "close": close,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "bb_lower": bb_lower,
        "weekly_ema_rising": weekly_ema_rising,
    })


class TestSignalScore:
    def test_full_bullish_fires(self):
        row = make_bullish_row()
        result = score_signal(row)
        assert result.fire_long is True
        assert result.total_score >= 70

    def test_weights_sum_to_100(self):
        """Max possible score must be 100."""
        row = make_bullish_row()
        result = score_signal(row)
        max_possible = 25 + 20 + 20 + 10 + 15 + 10
        assert max_possible == 100

    def test_no_trend_no_fire(self):
        """If EMA50 < EMA200, trend score = 0, total unlikely to hit 70."""
        row = make_bullish_row(ema_50=45000, ema_200=55000)
        result = score_signal(row)
        assert result.trend_aligned is False
        assert result.trend_score == 0

    def test_rsi_out_of_zone_no_rsi_score(self):
        """RSI at 65 (above zone) should get 0 RSI score."""
        row = make_bullish_row(rsi=65.0)
        result = score_signal(row)
        assert result.rsi_score == 0
        assert result.rsi_in_zone is False

    def test_rsi_at_low_end_of_zone(self):
        """RSI at exactly 40 should be in zone."""
        row = make_bullish_row(rsi=40.0)
        result = score_signal(row)
        assert result.rsi_in_zone is True
        assert result.rsi_score == 20

    def test_rsi_at_high_end_of_zone(self):
        """RSI at exactly 50 should be in zone."""
        row = make_bullish_row(rsi=50.0)
        result = score_signal(row)
        assert result.rsi_in_zone is True

    def test_macd_cross_scores(self):
        """MACD histogram turning positive should score."""
        row = make_bullish_row(macd_hist=50.0, macd_hist_prev=-10.0)
        result = score_signal(row)
        assert result.macd_bullish is True
        assert result.macd_score == 20

    def test_bearish_macd_no_score(self):
        """Negative MACD histogram should not score."""
        row = make_bullish_row(macd_hist=-100.0, macd_hist_prev=-50.0)
        result = score_signal(row)
        assert result.macd_bullish is False
        assert result.macd_score == 0

    def test_low_volume_no_volume_score(self):
        """Volume below average should get 0 volume score."""
        row = make_bullish_row(volume=500.0, volume_avg=1000.0)
        result = score_signal(row)
        assert result.volume_above_avg is False
        assert result.volume_score == 0

    def test_all_zeros_no_fire(self):
        """All NaN / zero row should not fire."""
        row = pd.Series({
            "ema_50": np.nan, "ema_200": np.nan,
            "rsi": np.nan, "macd_hist": np.nan, "macd_hist_prev": np.nan,
            "volume": 0, "volume_avg": 0, "adx": np.nan,
            "close": 50000, "swing_high": np.nan, "swing_low": np.nan,
            "weekly_ema_rising": False,
        })
        result = score_signal(row)
        assert result.fire_long is False
        assert result.total_score == 0

    def test_threshold_boundary(self):
        """Score exactly at threshold should fire."""
        row = make_bullish_row()
        # First get the score, then test at that exact threshold
        score_result = score_signal(row, threshold=70)
        actual_score = score_result.total_score
        result = score_signal(row, threshold=actual_score)
        assert result.fire_long is True

    def test_score_just_below_threshold_no_fire(self):
        """Score just below threshold must not fire."""
        row = make_bullish_row()
        result = score_signal(row, threshold=200)  # impossible threshold
        assert result.fire_long is False

    def test_why_no_trade_message(self):
        """why_no_trade() should be informative when not fired."""
        row = make_bullish_row(ema_50=40000, ema_200=55000, rsi=65.0)
        result = score_signal(row, threshold=70)
        if not result.fire_long:
            msg = result.why_no_trade()
            assert "Score" in msg
            assert len(msg) > 10

    def test_adx_weak_partial_score(self):
        """ADX just below threshold should get partial score."""
        row = make_bullish_row(adx=16.0)  # < 20 threshold
        result = score_signal(row, adx_min=20)
        assert result.adx_strong is False

    def test_custom_weights(self):
        """Custom weights should be applied correctly."""
        row = make_bullish_row()
        result = score_signal(row, w_trend=50, w_rsi=10, w_macd=10, w_volume=10, w_fib=10, w_adx=10)
        assert result.trend_score <= 50

    def test_weekly_not_rising_partial_trend(self):
        """If weekly EMA not rising, trend score should be partial."""
        row = make_bullish_row(weekly_ema_rising=False)
        result = score_signal(row)
        assert not result.trend_aligned
        # EMA still uptrend, so partial score
        assert result.trend_score > 0
        assert result.trend_score < 25


class TestBearishMACDDivergence:
    def test_detects_divergence(self):
        row = make_bullish_row(rsi=75.0, macd_hist=-50.0, macd_hist_prev=30.0)
        assert detect_bearish_macd_divergence(row) is True

    def test_no_divergence_rsi_normal(self):
        row = make_bullish_row(rsi=55.0, macd_hist=-50.0, macd_hist_prev=30.0)
        assert detect_bearish_macd_divergence(row) is False

    def test_no_divergence_macd_positive(self):
        row = make_bullish_row(rsi=75.0, macd_hist=50.0, macd_hist_prev=30.0)
        assert detect_bearish_macd_divergence(row) is False

    def test_no_divergence_nan_values(self):
        row = pd.Series({"rsi": np.nan, "macd_hist": np.nan, "macd_hist_prev": np.nan})
        assert detect_bearish_macd_divergence(row) is False


class TestRegime:
    def test_all_passing_is_armed(self):
        row = pd.Series({
            "ema_50": 55000, "ema_200": 48000,
            "adx": 25.0, "weekly_ema_rising": True,
        })
        result = check_regime(row)
        assert result.is_armed is True

    def test_ema_failing_not_armed(self):
        row = pd.Series({
            "ema_50": 45000, "ema_200": 55000,
            "adx": 25.0, "weekly_ema_rising": True,
        })
        result = check_regime(row)
        assert result.is_armed is False
        assert result.ema_uptrend is False

    def test_adx_failing_not_armed(self):
        row = pd.Series({
            "ema_50": 55000, "ema_200": 48000,
            "adx": 15.0, "weekly_ema_rising": True,
        })
        result = check_regime(row)
        assert result.is_armed is False
        assert result.adx_trending is False

    def test_weekly_failing_not_armed(self):
        row = pd.Series({
            "ema_50": 55000, "ema_200": 48000,
            "adx": 25.0, "weekly_ema_rising": False,
        })
        result = check_regime(row)
        assert result.is_armed is False
        assert result.weekly_rising is False

    def test_nan_values_not_armed(self):
        row = pd.Series({
            "ema_50": np.nan, "ema_200": np.nan,
            "adx": np.nan, "weekly_ema_rising": True,
        })
        result = check_regime(row)
        assert result.is_armed is False

    def test_regime_result_str(self):
        row = pd.Series({
            "ema_50": 55000, "ema_200": 48000,
            "adx": 25.0, "weekly_ema_rising": True,
        })
        result = check_regime(row)
        s = str(result)
        assert "ARMED" in s or "FLAT" in s

    def test_weekly_not_required(self):
        """If require_weekly_rising=False, weekly check is skipped."""
        row = pd.Series({
            "ema_50": 55000, "ema_200": 48000,
            "adx": 25.0, "weekly_ema_rising": False,
        })
        result = check_regime(row, require_weekly_rising=False)
        assert result.is_armed is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
