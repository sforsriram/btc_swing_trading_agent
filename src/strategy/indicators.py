# src/strategy/indicators.py
"""
Technical indicator calculations.
All functions take a DataFrame with OHLCV columns and return a new DataFrame
with indicator columns appended. No side effects.

Required input columns: open, high, low, close, volume, open_time
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing (EMA of gains/losses).
    Returns values 0–100.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)  # positive losses

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.where(avg_loss > 0, other=np.nan)
    rsi = 100 - (100 / (1 + rs))
    # When avg_loss == 0 (pure uptrend), RSI = 100
    rsi = rsi.where(avg_loss > 0, other=100.0)
    return rsi


def compute_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    MACD line, signal line, and histogram.
    Returns DataFrame with columns: macd, macd_signal, macd_hist
    """
    ema_fast = compute_ema(series, fast)
    ema_slow = compute_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "macd": macd_line,
        "macd_signal": signal_line,
        "macd_hist": histogram,
    }, index=series.index)


def compute_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """
    Bollinger Bands: middle (SMA), upper, lower bands.
    Returns DataFrame with columns: bb_mid, bb_upper, bb_lower, bb_width, bb_pct
    """
    mid = compute_sma(series, period)
    std = series.rolling(window=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    width = (upper - lower) / mid
    pct = (series - lower) / (upper - lower)

    return pd.DataFrame({
        "bb_mid": mid,
        "bb_upper": upper,
        "bb_lower": lower,
        "bb_width": width,
        "bb_pct": pct,
    }, index=series.index)


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range using Wilder's smoothing.
    True range = max of: (H-L), |H-Cprev|, |L-Cprev|
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    return atr


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.DataFrame:
    """
    Average Directional Index (ADX) with +DI and -DI.
    Returns DataFrame with columns: adx, plus_di, minus_di
    ADX > 20 = real trend; ADX < 20 = choppy.
    """
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # Directional Movement
    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder smoothing
    atr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_dm_smooth = plus_dm.ewm(alpha=1 / period, adjust=False).mean()
    minus_dm_smooth = minus_dm.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * plus_dm_smooth / atr_smooth.replace(0, np.nan)
    minus_di = 100 * minus_dm_smooth / atr_smooth.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()

    return pd.DataFrame({
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
    }, index=high.index)


def compute_volume_avg(volume: pd.Series, period: int = 20) -> pd.Series:
    """Rolling average volume."""
    return volume.rolling(window=period).mean()


def compute_fibonacci_levels(
    swing_high: float,
    swing_low: float,
    fib_levels: list[float] = None,
) -> dict[float, float]:
    """
    Compute Fibonacci retracement price levels from a swing high/low.

    Args:
        swing_high: recent swing high price
        swing_low: recent swing low price
        fib_levels: retracement levels, default [0.382, 0.5, 0.618]

    Returns:
        Dict mapping fib_level → price
    """
    if fib_levels is None:
        fib_levels = [0.382, 0.5, 0.618]
    rng = swing_high - swing_low
    return {level: swing_high - level * rng for level in fib_levels}


def add_all_indicators(
    df: pd.DataFrame,
    rsi_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_period: int = 20,
    bb_std: float = 2.0,
    atr_period: int = 14,
    adx_period: int = 14,
    ema_fast: int = 50,
    ema_slow: int = 200,
    weekly_ema: int = 21,
    volume_avg_period: int = 20,
) -> pd.DataFrame:
    """
    Add all strategy indicators to a OHLCV DataFrame.
    Input df must have columns: open, high, low, close, volume

    Returns a new DataFrame with all indicator columns added.
    Does NOT modify the input DataFrame.
    """
    df = df.copy()

    # EMAs for regime filter and entry conditions
    df[f"ema_{ema_fast}"] = compute_ema(df["close"], ema_fast)
    df[f"ema_{ema_slow}"] = compute_ema(df["close"], ema_slow)
    df["ema_20"] = compute_ema(df["close"], 20)

    # 10-bar swing low for proximity entry condition
    df["swing_low_10"] = df["low"].rolling(window=10, min_periods=1).min()

    # RSI
    df["rsi"] = compute_rsi(df["close"], rsi_period)

    # MACD
    macd_df = compute_macd(df["close"], macd_fast, macd_slow, macd_signal)
    df = pd.concat([df, macd_df], axis=1)

    # Bollinger Bands
    bb_df = compute_bollinger_bands(df["close"], bb_period, bb_std)
    df = pd.concat([df, bb_df], axis=1)

    # ATR
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], atr_period)

    # ADX
    adx_df = compute_adx(df["high"], df["low"], df["close"], adx_period)
    df = pd.concat([df, adx_df], axis=1)

    # Volume average
    df["volume_avg"] = compute_volume_avg(df["volume"], volume_avg_period)
    df["volume_ratio"] = df["volume"] / df["volume_avg"].replace(0, np.nan)

    # Previous candle values (for cross detection)
    df["macd_hist_prev"] = df["macd_hist"].shift(1)
    df["rsi_prev"] = df["rsi"].shift(1)

    return df


def add_weekly_ema(
    daily_df: pd.DataFrame,
    weekly_df: pd.DataFrame,
    weekly_ema_period: int = 21,
) -> pd.DataFrame:
    """
    Merge weekly EMA21 into daily DataFrame.
    Used for higher-timeframe trend alignment.

    Args:
        daily_df: daily OHLCV with open_time column
        weekly_df: weekly OHLCV with open_time column

    Returns:
        daily_df with weekly_ema and weekly_ema_rising columns added
    """
    daily_df = daily_df.copy()
    weekly_df = weekly_df.copy()

    weekly_df["weekly_ema21"] = compute_ema(weekly_df["close"], weekly_ema_period)
    weekly_df["weekly_ema21_prev"] = weekly_df["weekly_ema21"].shift(1)
    weekly_df["weekly_ema_rising"] = weekly_df["weekly_ema21"] > weekly_df["weekly_ema21_prev"]

    # Forward-fill weekly values into daily (each daily date gets the most recent weekly value)
    daily_df["open_time"] = pd.to_datetime(daily_df["open_time"])
    weekly_df["open_time"] = pd.to_datetime(weekly_df["open_time"])

    weekly_subset = weekly_df[["open_time", "weekly_ema21", "weekly_ema_rising"]].copy()
    weekly_subset = weekly_subset.sort_values("open_time")

    daily_df = daily_df.sort_values("open_time")
    daily_df = pd.merge_asof(
        daily_df,
        weekly_subset,
        on="open_time",
        direction="backward",
    )

    return daily_df
