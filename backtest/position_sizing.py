# src/strategy/position_sizing.py
"""
Risk-based position sizing.
Size = (Equity * risk_pct) / (Entry - StopPrice)
Dollar loss is constant vs volatility — works at $200 or $200,000.
Respects Coinbase BTC-USD min order and quantity precision.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

from src.core.logging_setup import get_logger

log = get_logger("position_sizing")


@dataclass
class SizeResult:
    size_btc: float          # BTC quantity to buy
    size_usd: float          # USD cost at entry price
    risk_usd: float          # max dollar loss if stop hit
    risk_pct: float          # risk as % of equity
    stop_price: float        # calculated stop loss price
    is_viable: bool          # True if meets min order size
    reason: str = ""         # why not viable, if applicable


def calculate_position_size(
    equity_usd: float,
    entry_price: float,
    atr: float,
    risk_per_trade_pct: float = 0.01,
    initial_stop_pct: float = 0.025,
    initial_stop_atr_mult: float = 1.5,
    min_order_usd: float = 1.0,
    base_precision: int = 8,
    quote_precision: int = 2,
    taker_rate: float = 0.012,
    slippage_rate: float = 0.001,
) -> SizeResult:
    """
    Calculate risk-based BTC position size.

    Stop is set to whichever is MORE structurally sound:
    - Percentage-based: entry * (1 - initial_stop_pct)
    - ATR-based: entry - (initial_stop_atr_mult * atr)

    Uses the LARGER of the two stops (further from entry = more conservative).

    Args:
        equity_usd: current total equity in USD
        entry_price: expected entry price (current close or limit)
        atr: current ATR(14) value
        risk_per_trade_pct: fraction of equity to risk (default 0.01 = 1%)
        initial_stop_pct: percentage stop below entry (default 0.025 = 2.5%)
        initial_stop_atr_mult: ATR multiplier for stop (default 1.5)
        min_order_usd: Coinbase minimum order in USD (default $1)
        base_precision: BTC decimal places (default 8)
        quote_precision: USD decimal places (default 2)
        taker_rate: fee rate for sizing (conservative — assume taker)
        slippage_rate: modeled slippage

    Returns:
        SizeResult with all sizing details
    """
    if entry_price <= 0 or equity_usd <= 0:
        return SizeResult(
            size_btc=0, size_usd=0, risk_usd=0, risk_pct=0,
            stop_price=0, is_viable=False, reason="invalid price or equity"
        )

    # Calculate stop price — use structurally sound (larger distance)
    stop_pct_price = entry_price * (1 - initial_stop_pct)
    stop_atr_price = entry_price - (initial_stop_atr_mult * atr)

    # Use lower price (more conservative — larger stop, less size)
    stop_price = min(stop_pct_price, stop_atr_price)

    if stop_price <= 0 or stop_price >= entry_price:
        return SizeResult(
            size_btc=0, size_usd=0, risk_usd=0, risk_pct=0,
            stop_price=stop_price, is_viable=False,
            reason=f"invalid stop price {stop_price:.2f} vs entry {entry_price:.2f}"
        )

    # Risk in USD
    risk_usd = equity_usd * risk_per_trade_pct

    # Risk per BTC (entry → stop distance, net of fees on both legs)
    # Gross trigger prices set higher to absorb fees
    effective_cost_per_btc = entry_price * (1 + taker_rate + slippage_rate)
    effective_stop_per_btc = stop_price * (1 - taker_rate - slippage_rate)
    risk_per_btc = effective_cost_per_btc - effective_stop_per_btc

    if risk_per_btc <= 0:
        return SizeResult(
            size_btc=0, size_usd=0, risk_usd=0, risk_pct=0,
            stop_price=stop_price, is_viable=False,
            reason=f"risk_per_btc={risk_per_btc:.4f} <= 0 after fees"
        )

    # Raw BTC size
    raw_size_btc = risk_usd / risk_per_btc

    # Round DOWN to base_precision (never round up — would exceed risk budget)
    factor = 10 ** base_precision
    size_btc = math.floor(raw_size_btc * factor) / factor

    # Calculate USD cost
    size_usd = size_btc * entry_price

    # Viability check
    if size_usd < min_order_usd:
        return SizeResult(
            size_btc=size_btc,
            size_usd=size_usd,
            risk_usd=risk_usd,
            risk_pct=risk_per_trade_pct,
            stop_price=stop_price,
            is_viable=False,
            reason=(
                f"Order size ${size_usd:.4f} below minimum ${min_order_usd}. "
                f"Need equity > ${min_order_usd * (risk_per_btc / risk_usd):.2f}"
            )
        )

    # Cap at 95% of equity (never go all-in)
    max_usd = equity_usd * 0.95
    if size_usd > max_usd:
        size_btc = math.floor((max_usd / entry_price) * factor) / factor
        size_usd = size_btc * entry_price

    actual_risk_usd = size_btc * risk_per_btc
    actual_risk_pct = actual_risk_usd / equity_usd

    log.info(
        "Position sized",
        equity=equity_usd,
        entry=entry_price,
        stop=stop_price,
        size_btc=size_btc,
        size_usd=size_usd,
        risk_usd=actual_risk_usd,
        risk_pct=f"{actual_risk_pct:.2%}",
        stop_pct_used=f"{(entry_price - stop_price) / entry_price:.2%}",
    )

    return SizeResult(
        size_btc=size_btc,
        size_usd=round(size_usd, quote_precision),
        risk_usd=round(actual_risk_usd, quote_precision),
        risk_pct=actual_risk_pct,
        stop_price=round(stop_price, quote_precision),
        is_viable=True,
    )


def round_btc(size: float, base_precision: int = 8) -> float:
    """Round BTC size down to base_precision decimal places."""
    factor = 10 ** base_precision
    return math.floor(size * factor) / factor


def round_usd(amount: float, quote_precision: int = 2) -> float:
    """Round USD to quote_precision decimal places."""
    return round(amount, quote_precision)
