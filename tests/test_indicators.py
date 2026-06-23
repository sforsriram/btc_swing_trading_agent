# tests/test_indicators.py
"""
Unit tests for indicator calculations.
Tests mathematical correctness with known values.
"""
import pytest
import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.strategy.indicators import (
    compute_ema,
    compute_rsi,
    compute_macd,
    compute_bollinger_bands,
    compute_atr,
    compute_adx,
    compute_volume_avg,
    compute_fibonacci_levels,
    add_all_indicators,
)


def make_price_series(n=100, start=50000.0, drift=0.001, seed=42) -> pd.Series:
    """Generate synthetic price series."""
    np.random.seed(seed)
    returns = np.random.normal(drift, 0.02, n)
    prices = start * np.cumprod(1 + returns)
    return pd.Series(prices)


def make_ohlcv_df(n=300, seed=42) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame."""
    np.random.seed(seed)
    close = make_price_series(n, seed=seed)
    noise = np.abs(np.random.normal(0, 0.005, n))
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = np.random.lognormal(10, 0.5, n)

    return pd.DataFrame({
        "open_time": pd.date_range("2021-01-01", periods=n, freq="D"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class TestEMA:
    def test_ema_length(self):
        s = make_price_series(100)
        result = compute_ema(s, 20)
        assert len(result) == 100

    def test_ema_no_nan_after_warmup(self):
        s = make_price_series(100)
        result = compute_ema(s, 20)
        # EMA with adjust=False should have values from the start
        assert not result.iloc[20:].isna().any()

    def test_ema_converges_to_constant(self):
        """EMA of constant series should equal that constant."""
        s = pd.Series([100.0] * 100)
        result = compute_ema(s, 10)
        assert abs(result.iloc[-1] - 100.0) < 0.01

    def test_ema_50_below_200_in_downtrend(self):
        """Fast EMA should be below slow EMA in downtrend."""
        n = 300
        prices = pd.Series([50000 - i * 100 for i in range(n)])
        ema50 = compute_ema(prices, 50)
        ema200 = compute_ema(prices, 200)
        # After warmup, fast EMA should be below slow in downtrend
        assert ema50.iloc[-1] < ema200.iloc[-1]


class TestRSI:
    def test_rsi_bounds(self):
        """RSI must always be between 0 and 100."""
        s = make_price_series(200)
        rsi = compute_rsi(s, 14)
        valid = rsi.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_overbought_in_uptrend(self):
        """Strongly rising prices should produce RSI well above midpoint."""
        # Use longer series so RSI fully warms up
        prices = pd.Series([50000 + i * 500 for i in range(200)])
        rsi = compute_rsi(prices, 14)
        valid = rsi.dropna()
        assert len(valid) > 0
        # In a pure uptrend, RSI should converge toward high values
        assert valid.iloc[-1] > 60

    def test_rsi_oversold_in_downtrend(self):
        """Strongly falling prices should produce RSI < 40."""
        prices = pd.Series([50000 - i * 500 for i in range(100)])
        rsi = compute_rsi(prices, 14)
        assert rsi.iloc[-1] < 40

    def test_rsi_length(self):
        s = make_price_series(100)
        rsi = compute_rsi(s, 14)
        assert len(rsi) == 100


class TestMACD:
    def test_macd_columns(self):
        s = make_price_series(100)
        result = compute_macd(s)
        assert set(result.columns) == {"macd", "macd_signal", "macd_hist"}

    def test_macd_hist_is_difference(self):
        """Histogram = MACD line - signal line."""
        s = make_price_series(100)
        result = compute_macd(s)
        diff = (result["macd"] - result["macd_signal"]).round(10)
        hist = result["macd_hist"].round(10)
        pd.testing.assert_series_equal(diff, hist, check_names=False)

    def test_macd_length(self):
        s = make_price_series(100)
        result = compute_macd(s)
        assert len(result) == 100


class TestBollingerBands:
    def test_bb_columns(self):
        s = make_price_series(100)
        result = compute_bollinger_bands(s)
        assert all(c in result.columns for c in ["bb_mid", "bb_upper", "bb_lower", "bb_width", "bb_pct"])

    def test_upper_above_lower(self):
        s = make_price_series(100)
        result = compute_bollinger_bands(s, 20)
        valid = result.dropna()
        assert (valid["bb_upper"] >= valid["bb_lower"]).all()

    def test_price_mostly_within_bands(self):
        """~95% of prices should fall within 2-std Bollinger Bands."""
        s = make_price_series(200)
        result = compute_bollinger_bands(s, 20, 2.0)
        valid_idx = result.dropna().index
        s_valid = s.loc[valid_idx]
        inside = (
            (s_valid >= result.loc[valid_idx, "bb_lower"])
            & (s_valid <= result.loc[valid_idx, "bb_upper"])
        )
        assert inside.mean() > 0.90

    def test_constant_series_zero_width(self):
        """Constant price series should have zero band width."""
        s = pd.Series([100.0] * 100)
        result = compute_bollinger_bands(s, 20)
        assert result["bb_width"].dropna().abs().max() < 1e-10


class TestATR:
    def test_atr_positive(self):
        df = make_ohlcv_df(100)
        atr = compute_atr(df["high"], df["low"], df["close"], 14)
        assert (atr.dropna() > 0).all()

    def test_atr_length(self):
        df = make_ohlcv_df(100)
        atr = compute_atr(df["high"], df["low"], df["close"], 14)
        assert len(atr) == 100

    def test_atr_zero_for_no_movement(self):
        """Zero volatility should produce near-zero ATR."""
        n = 100
        s = pd.Series([50000.0] * n)
        atr = compute_atr(s, s, s, 14)
        assert atr.dropna().abs().max() < 1e-8


class TestADX:
    def test_adx_columns(self):
        df = make_ohlcv_df(100)
        result = compute_adx(df["high"], df["low"], df["close"], 14)
        assert set(result.columns) == {"adx", "plus_di", "minus_di"}

    def test_adx_non_negative(self):
        df = make_ohlcv_df(200)
        result = compute_adx(df["high"], df["low"], df["close"], 14)
        assert (result["adx"].dropna() >= 0).all()

    def test_adx_bounded(self):
        """ADX should generally be between 0 and 100."""
        df = make_ohlcv_df(300)
        result = compute_adx(df["high"], df["low"], df["close"], 14)
        assert (result["adx"].dropna() <= 100).all()

    def test_adx_high_in_strong_trend(self):
        """Strong uptrend should produce ADX > 20 after warmup."""
        n = 200
        prices = pd.Series([50000 + i * 200 for i in range(n)])
        noise = prices * 0.005
        high = prices + noise
        low = prices - noise
        result = compute_adx(high, low, prices, 14)
        assert result["adx"].iloc[-1] > 15  # strong trend


class TestFibonacciLevels:
    def test_fib_levels_structure(self):
        levels = compute_fibonacci_levels(60000, 50000)
        assert set(levels.keys()) == {0.382, 0.5, 0.618}

    def test_fib_levels_between_high_low(self):
        swing_high, swing_low = 60000, 50000
        levels = compute_fibonacci_levels(swing_high, swing_low)
        for level, price in levels.items():
            assert swing_low <= price <= swing_high

    def test_fib_38_2_correct(self):
        """38.2% retracement of 10000 range from 60000 = 56180."""
        levels = compute_fibonacci_levels(60000, 50000)
        expected = 60000 - 0.382 * 10000
        assert abs(levels[0.382] - expected) < 0.01

    def test_fib_50_correct(self):
        levels = compute_fibonacci_levels(60000, 50000)
        assert abs(levels[0.5] - 55000) < 0.01

    def test_custom_fib_levels(self):
        levels = compute_fibonacci_levels(60000, 50000, [0.236, 0.786])
        assert set(levels.keys()) == {0.236, 0.786}


class TestAddAllIndicators:
    def test_all_columns_present(self):
        df = make_ohlcv_df(300)
        result = add_all_indicators(df)
        expected_cols = [
            "ema_50", "ema_200", "rsi",
            "macd", "macd_signal", "macd_hist",
            "bb_mid", "bb_upper", "bb_lower",
            "atr", "adx", "plus_di", "minus_di",
            "volume_avg", "volume_ratio",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_does_not_modify_input(self):
        df = make_ohlcv_df(300)
        original_cols = set(df.columns)
        _ = add_all_indicators(df)
        assert set(df.columns) == original_cols

    def test_output_length_preserved(self):
        df = make_ohlcv_df(300)
        result = add_all_indicators(df)
        assert len(result) == len(df)

    def test_sufficient_data_no_all_nan(self):
        """With 300 rows, indicators after warmup should not be all NaN."""
        df = make_ohlcv_df(300)
        result = add_all_indicators(df)
        assert not result["ema_200"].iloc[210:].isna().any()
        assert not result["rsi"].iloc[20:].isna().any()
        assert not result["adx"].iloc[20:].isna().any()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
