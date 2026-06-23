# src/strategy/regime.py
"""
Regime filter: 3-condition gatekeeper.
ALL conditions must pass to allow new entries.
If regime fails → stay flat (preserving capital in chop/bear IS the strategy).
"""
from __future__ import annotations
from dataclasses import dataclass

import pandas as pd

from src.core.logging_setup import get_logger

log = get_logger("regime")


@dataclass
class RegimeResult:
    is_armed: bool              # True only if ALL conditions pass
    ema_uptrend: bool           # EMA50 > EMA200 on daily
    adx_trending: bool          # ADX > adx_min (real trend, not chop)
    weekly_rising: bool         # Weekly EMA21 is rising
    ema_fast: float | None = None
    ema_slow: float | None = None
    adx_value: float | None = None
    weekly_ema: float | None = None
    reason: str = ""

    def __str__(self) -> str:
        status = "ARMED" if self.is_armed else "FLAT"
        return (
            f"Regime={status} | "
            f"EMA50>EMA200={self.ema_uptrend}({self.ema_fast:.0f}>{self.ema_slow:.0f}) | "
            f"ADX>{self.adx_value:.1f}={self.adx_trending} | "
            f"WeeklyRising={self.weekly_rising}"
        )


def check_regime(
    row: pd.Series,
    ema_fast_col: str = "ema_50",
    ema_slow_col: str = "ema_200",
    adx_col: str = "adx",
    adx_min: float = 20.0,
    weekly_ema_rising_col: str = "weekly_ema_rising",
    require_weekly_rising: bool = True,
) -> RegimeResult:
    """
    Evaluate regime for a single daily candle row.

    Args:
        row: pandas Series (one row of daily indicator DataFrame)
        ema_fast_col: column name for fast EMA
        ema_slow_col: column name for slow EMA
        adx_col: column name for ADX
        adx_min: minimum ADX value for trend confirmation
        weekly_ema_rising_col: column for weekly EMA direction flag
        require_weekly_rising: if False, skip weekly check

    Returns:
        RegimeResult with all condition flags and is_armed status
    """
    # Guard against NaN values (insufficient history for indicators)
    ema_fast = row.get(ema_fast_col)
    ema_slow = row.get(ema_slow_col)
    adx_val = row.get(adx_col)
    weekly_rising = row.get(weekly_ema_rising_col)

    reasons = []

    # Condition 1: EMA50 > EMA200 (macro uptrend)
    if pd.isna(ema_fast) or pd.isna(ema_slow):
        ema_uptrend = False
        reasons.append("insufficient EMA history")
    else:
        ema_uptrend = float(ema_fast) > float(ema_slow)
        if not ema_uptrend:
            reasons.append(f"EMA50({ema_fast:.0f}) <= EMA200({ema_slow:.0f})")

    # Condition 2: ADX > adx_min (real trend, not chop)
    if pd.isna(adx_val):
        adx_trending = False
        reasons.append("insufficient ADX history")
    else:
        adx_trending = float(adx_val) > adx_min
        if not adx_trending:
            reasons.append(f"ADX({adx_val:.1f}) <= {adx_min}")

    # Condition 3: Weekly EMA21 rising
    if require_weekly_rising:
        if pd.isna(weekly_rising):
            wk_rising = False
            reasons.append("no weekly EMA data")
        else:
            wk_rising = bool(weekly_rising)
            if not wk_rising:
                reasons.append("weekly EMA21 not rising")
    else:
        wk_rising = True

    is_armed = ema_uptrend and adx_trending and wk_rising
    reason_str = "; ".join(reasons) if reasons else "all conditions met"

    result = RegimeResult(
        is_armed=is_armed,
        ema_uptrend=ema_uptrend,
        adx_trending=adx_trending,
        weekly_rising=wk_rising,
        ema_fast=float(ema_fast) if not pd.isna(ema_fast) else None,
        ema_slow=float(ema_slow) if not pd.isna(ema_slow) else None,
        adx_value=float(adx_val) if not pd.isna(adx_val) else None,
        weekly_ema=None,
        reason=reason_str,
    )

    log.debug(
        "Regime check",
        armed=is_armed,
        ema_uptrend=ema_uptrend,
        adx_trending=adx_trending,
        weekly_rising=wk_rising,
        reason=reason_str,
    )

    return result
