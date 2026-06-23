# src/core/models.py
"""
ORM models for state.sqlite:
  Position, Order, Trade, Equity, Alert
These survive restarts — the source of truth for what the agent owns.
"""
from __future__ import annotations
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, Integer, String, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


# ---- Enumerations ----

class StopStage(str, PyEnum):
    INITIAL = "INITIAL"
    BREAKEVEN = "BREAKEVEN"
    SCALED = "SCALED"        # scale-out executed, trailing the remainder
    TRAILING = "TRAILING"    # pure trail, no more scale-out
    CLOSED = "CLOSED"


class PositionSide(str, PyEnum):
    LONG = "LONG"
    FLAT = "FLAT"


class OrderStatus(str, PyEnum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class OrderSide(str, PyEnum):
    BUY = "BUY"
    SELL = "SELL"


class AlertSeverity(str, PyEnum):
    INFO = "INFO"
    TRADE = "TRADE"
    CRITICAL = "CRITICAL"


# ---- Models ----

class Position(Base):
    """
    Represents the current open position (max 1 at a time).
    Stop stage is persisted on every transition for crash recovery.
    """
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    side: Mapped[str] = mapped_column(Enum(PositionSide), default=PositionSide.FLAT)

    # Entry details
    entry_price: Mapped[float | None] = mapped_column(Float)
    entry_size_btc: Mapped[float | None] = mapped_column(Float)  # full size at entry
    entry_size_usd: Mapped[float | None] = mapped_column(Float)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime)
    entry_score: Mapped[float | None] = mapped_column(Float)
    entry_regime: Mapped[str | None] = mapped_column(String(32))

    # Current stop state machine
    stop_stage: Mapped[str] = mapped_column(Enum(StopStage), default=StopStage.INITIAL)
    current_stop: Mapped[float | None] = mapped_column(Float)
    current_size_btc: Mapped[float | None] = mapped_column(Float)  # remaining after scale-out
    highest_price: Mapped[float | None] = mapped_column(Float)     # for trailing stop calc
    atr_at_entry: Mapped[float | None] = mapped_column(Float)

    # Scale-out tracking
    scale_out_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    scale_out_price: Mapped[float | None] = mapped_column(Float)
    scale_out_time: Mapped[datetime | None] = mapped_column(DateTime)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    close_reason: Mapped[str | None] = mapped_column(String(64))

    def __repr__(self) -> str:
        return (
            f"<Position trade_id={self.trade_id} side={self.side} "
            f"stage={self.stop_stage} stop={self.current_stop}>"
        )


class Order(Base):
    """Individual exchange order record for idempotency and reconciliation."""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    trade_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)

    side: Mapped[str] = mapped_column(Enum(OrderSide))
    order_type: Mapped[str] = mapped_column(String(32))   # market | limit | stop_limit
    status: Mapped[str] = mapped_column(Enum(OrderStatus), default=OrderStatus.PENDING)
    reason: Mapped[str | None] = mapped_column(String(64))  # entry | stop | scale_out | trail_exit | ceiling | time_stop

    requested_size_btc: Mapped[float | None] = mapped_column(Float)
    filled_size_btc: Mapped[float | None] = mapped_column(Float)
    limit_price: Mapped[float | None] = mapped_column(Float)
    fill_price: Mapped[float | None] = mapped_column(Float)
    fees_paid_usd: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    filled_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw_response: Mapped[str | None] = mapped_column(Text)  # JSON string of API response

    def __repr__(self) -> str:
        return (
            f"<Order order_id={self.order_id} side={self.side} "
            f"status={self.status} fill={self.fill_price}>"
        )


class Trade(Base):
    """Completed round-trip trade record (entry → exit). Immutable once closed."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    entry_size_btc: Mapped[float] = mapped_column(Float)
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    exit_time: Mapped[datetime] = mapped_column(DateTime)

    gross_pnl_usd: Mapped[float] = mapped_column(Float)
    net_pnl_usd: Mapped[float] = mapped_column(Float)       # after fees
    net_pnl_pct: Mapped[float] = mapped_column(Float)
    fees_paid_usd: Mapped[float] = mapped_column(Float)
    r_multiple: Mapped[float | None] = mapped_column(Float) # net_pnl / initial_risk

    exit_reason: Mapped[str] = mapped_column(String(64))    # stop_loss | scale_out | trail | ceiling | time_stop
    stop_stage_at_exit: Mapped[str] = mapped_column(String(32))
    entry_score: Mapped[float | None] = mapped_column(Float)
    entry_regime: Mapped[str | None] = mapped_column(String(32))
    hold_days: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return (
            f"<Trade trade_id={self.trade_id} net_pnl={self.net_pnl_pct:.2%} "
            f"exit={self.exit_reason}>"
        )


class Equity(Base):
    """Daily equity snapshot for drawdown tracking and circuit breaker."""
    __tablename__ = "equity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, unique=True, nullable=False)
    equity_usd: Mapped[float] = mapped_column(Float)
    open_position_value: Mapped[float] = mapped_column(Float, default=0.0)
    cash_usd: Mapped[float] = mapped_column(Float)
    cumulative_fees_usd: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Alert(Base):
    """Persisted alert row for dashboard feed and Telegram dedupe."""
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(Enum(AlertSeverity), default=AlertSeverity.INFO)
    message: Mapped[str] = mapped_column(Text)
    trade_id: Mapped[str | None] = mapped_column(String(64))
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
