# src/strategy/signals.py
"""
Confluence scorer: 0-100 weighted score across 6 signal components.
Fire LONG only when score >= threshold (default 70).
Enter on PULLBACKS only — never chase breakouts.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from src.core.logging_setup import get_logger
from src.strategy.indicators import compute_fibonacci_levels

log = get_logger("signals")


@dataclass
class SignalScore:
    """Full breakdown of confluence score for one candle."""
    total_score: float = 0.0
    threshold: float = 70.0
    fire_long: bool = False

    # Per-component scores (partial, proportional to weight)
    trend_score: float = 0.0        # weight 25
    rsi_score: float = 0.0          # weight 20
    macd_score: float = 0.0         # weight 20
    volume_score: float = 0.0       # weight 10
    fib_score: float = 0.0          # weight 15
    adx_score: float = 0.0          # weight 10

    # Diagnostic flags
    trend_aligned: bool = False
    rsi_in_zone: bool = False
    macd_bullish: bool = False
    volume_above_avg: bool = False
    fib_confluence: bool = False
    adx_strong: bool = False

    explanation: str = ""

    def why_no_trade(self) -> str:
        """Human-readable explanation of why a trade was NOT fired."""
        if self.fire_long:
            return "Signal fired."
        reasons = []
        if not self.trend_aligned:
            reasons.append("trend not aligned (EMA + weekly)")
        if not self.rsi_in_zone:
            reasons.append("RSI not in pullback zone (40-50)")
        if not self.macd_bullish:
            reasons.append("MACD not bullish")
        if not self.volume_above_avg:
            reasons.append("volume below 20-day avg")
        if not self.fib_confluence:
            reasons.append("not at Fibonacci level")
        if not self.adx_strong:
            reasons.append("ADX weak")
        missing = self.threshold - self.total_score
        return (
            f"Score {self.total_score:.1f}/{self.threshold} "
            f"(need +{missing:.1f} more). "
            f"Missing: {', '.join(reasons)}"
        )


def score_signal(
    row: pd.Series,
    # Weights (must sum to 100)
    w_trend: int = 25,
    w_rsi: int = 20,
    w_macd: int = 20,
    w_volume: int = 10,
    w_fib: int = 15,
    w_adx: int = 10,
    # Thresholds
    threshold: float = 70.0,
    rsi_pullback_low: float = 40.0,
    rsi_pullback_high: float = 50.0,
    adx_min: float = 20.0,
    fib_levels: list[float] = None,
    fib_tolerance_pct: float = 0.005,  # 0.5% tolerance around fib level
    # Column names
    ema_fast_col: str = "ema_50",
    ema_slow_col: str = "ema_200",
) -> SignalScore:
    """
    Compute weighted confluence score for a single daily candle.

    Signal logic:
    - TREND (25): EMA50 > EMA200 AND weekly EMA rising
    - RSI (20): RSI in 40-50 pullback zone
    - MACD (20): bullish cross OR histogram turning positive
    - VOLUME (10): volume > 20-day average
    - FIB (15): price within tolerance of key Fibonacci level
    - ADX (10): ADX > 20 and rising

    Args:
        row: pandas Series with all indicator columns
        ... weight and threshold parameters from config

    Returns:
        SignalScore with total, per-component, and fire_long flag
    """
    if fib_levels is None:
        fib_levels = [0.382, 0.5, 0.618]

    score = SignalScore(threshold=threshold)

    # ---- Component 1: Trend Alignment (weight 25) ----
    ema_fast = _safe_float(row, ema_fast_col)
    ema_slow = _safe_float(row, ema_slow_col)
    weekly_rising = _safe_bool(row, "weekly_ema_rising")

    if ema_fast is not None and ema_slow is not None:
        ema_uptrend = ema_fast > ema_slow
        score.trend_aligned = ema_uptrend and weekly_rising
        if score.trend_aligned:
            score.trend_score = w_trend
        elif ema_uptrend:
            # Partial: daily trend ok but weekly not rising
            score.trend_score = w_trend * 0.5

    # ---- Component 2: RSI Pullback Zone (weight 20) ----
    rsi = _safe_float(row, "rsi")
    if rsi is not None:
        score.rsi_in_zone = rsi_pullback_low <= rsi <= rsi_pullback_high
        if score.rsi_in_zone:
            score.rsi_score = w_rsi
        elif rsi < rsi_pullback_low + 5:
            # Near zone (within 5 points below)
            score.rsi_score = w_rsi * 0.5

    # ---- Component 3: MACD Bullish (weight 20) ----
    macd_hist = _safe_float(row, "macd_hist")
    macd_hist_prev = _safe_float(row, "macd_hist_prev")
    macd_line = _safe_float(row, "macd")
    macd_signal_val = _safe_float(row, "macd_signal")

    if macd_hist is not None and macd_hist_prev is not None:
        # Bullish cross: histogram turning positive (from negative)
        hist_turning_up = macd_hist > 0 and macd_hist_prev <= 0
        # Or histogram inflecting upward (getting less negative)
        hist_inflecting = macd_hist > macd_hist_prev and macd_hist_prev < 0

        # Cross above zero line
        cross_above_zero = (
            macd_line is not None
            and macd_signal_val is not None
            and macd_line > macd_signal_val
            and macd_hist > 0
        )

        score.macd_bullish = hist_turning_up or cross_above_zero
        if score.macd_bullish:
            score.macd_score = w_macd
        elif hist_inflecting:
            # Histogram improving but not yet positive
            score.macd_score = w_macd * 0.5

        # Note: bearish MACD divergence is an EXIT signal, NOT scored here

    # ---- Component 4: Volume Confirmation (weight 10) ----
    volume = _safe_float(row, "volume")
    volume_avg = _safe_float(row, "volume_avg")

    if volume is not None and volume_avg is not None and volume_avg > 0:
        score.volume_above_avg = volume > volume_avg
        if score.volume_above_avg:
            score.volume_score = w_volume
        elif volume > volume_avg * 0.8:
            # Within 20% of average — partial credit
            score.volume_score = w_volume * 0.5

    # ---- Component 5: Fibonacci Confluence (weight 15) ----
    close = _safe_float(row, "close")
    swing_high = _safe_float(row, "swing_high")
    swing_low = _safe_float(row, "swing_low")

    if close is not None and swing_high is not None and swing_low is not None:
        fib_prices = compute_fibonacci_levels(swing_high, swing_low, fib_levels)
        tolerance = close * fib_tolerance_pct

        for level, fib_price in fib_prices.items():
            if abs(close - fib_price) <= tolerance:
                score.fib_confluence = True
                score.fib_score = w_fib
                break

        if not score.fib_confluence:
            # Check if price is below lower BB (additional confluence signal)
            bb_lower = _safe_float(row, "bb_lower")
            if bb_lower is not None and close <= bb_lower * 1.005:
                score.fib_score = w_fib * 0.5
    else:
        # No swing high/low available — skip Fib (don't penalize)
        score.fib_score = 0

    # ---- Component 6: ADX Strength (weight 10) ----
    adx = _safe_float(row, "adx")
    adx_prev = _safe_float(row, "adx_prev") if "adx_prev" in row.index else None

    if adx is not None:
        adx_above_min = adx > adx_min
        adx_rising = (adx_prev is not None and adx > adx_prev)

        score.adx_strong = adx_above_min
        if adx_above_min and adx_rising:
            score.adx_score = w_adx
        elif adx_above_min:
            score.adx_score = w_adx * 0.75
        elif adx > adx_min * 0.8:
            # Near threshold
            score.adx_score = w_adx * 0.25

    # ---- Total Score ----
    score.total_score = (
        score.trend_score
        + score.rsi_score
        + score.macd_score
        + score.volume_score
        + score.fib_score
        + score.adx_score
    )

    score.fire_long = score.total_score >= threshold

    # Build explanation
    components = []
    if score.trend_score > 0:
        components.append(f"trend={score.trend_score:.0f}")
    if score.rsi_score > 0:
        components.append(f"rsi={score.rsi_score:.0f}")
    if score.macd_score > 0:
        components.append(f"macd={score.macd_score:.0f}")
    if score.volume_score > 0:
        components.append(f"vol={score.volume_score:.0f}")
    if score.fib_score > 0:
        components.append(f"fib={score.fib_score:.0f}")
    if score.adx_score > 0:
        components.append(f"adx={score.adx_score:.0f}")
    score.explanation = f"Score={score.total_score:.1f} [{', '.join(components)}]"

    log.debug(
        "Signal scored",
        total=score.total_score,
        fire=score.fire_long,
        explanation=score.explanation,
    )

    return score


def compute_swing_levels(df: pd.DataFrame, lookback: int = 50) -> pd.DataFrame:
    """
    Add swing_high and swing_low columns to DataFrame.
    Uses rolling max/min over lookback period.
    These are needed for Fibonacci level calculation.
    """
    df = df.copy()
    df["swing_high"] = df["high"].rolling(window=lookback, min_periods=10).max()
    df["swing_low"] = df["low"].rolling(window=lookback, min_periods=10).min()
    return df


def detect_bearish_macd_divergence(row: pd.Series) -> bool:
    """
    Detect bearish MACD divergence — used as EXIT/tighten signal, NOT entry.
    Bearish: RSI > 70 (overbought) AND MACD histogram turning negative.
    """
    rsi = _safe_float(row, "rsi")
    macd_hist = _safe_float(row, "macd_hist")
    macd_hist_prev = _safe_float(row, "macd_hist_prev")

    if rsi is None or macd_hist is None or macd_hist_prev is None:
        return False

    overbought = rsi > 70
    hist_turning_negative = macd_hist < 0 and macd_hist_prev >= 0

    return overbought and hist_turning_negative


# ---- Helpers ----

def _safe_float(row: pd.Series, col: str) -> float | None:
    """Return float value from row, or None if missing/NaN."""
    val = row.get(col)
    if val is None or pd.isna(val):
        return None
    return float(val)


def _safe_bool(row: pd.Series, col: str, default: bool = False) -> bool:
    """Return bool value from row, or default if missing/NaN."""
    val = row.get(col)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return bool(val)
