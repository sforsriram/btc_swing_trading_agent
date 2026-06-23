# src/data/repository.py
"""
Database read/write helpers.
Abstracts SQLAlchemy queries from business logic.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

import pandas as pd

from src.core.db import get_candles_session, get_state_session
from src.core.models import Position, Trade, Equity, Order, Alert, StopStage, PositionSide
from src.data.candle_models import Candle
from src.core.logging_setup import get_logger

log = get_logger("repository")


# ---- Candle Repository ----

def get_candles_df(
    product_id: str,
    granularity: str,
    start_dt: datetime,
    end_dt: datetime,
) -> pd.DataFrame:
    """Load candles from cache as DataFrame. Returns empty DF if none."""
    with get_candles_session() as session:
        rows = (
            session.query(Candle)
            .filter(
                Candle.product_id == product_id,
                Candle.granularity == granularity,
                Candle.open_time >= start_dt,
                Candle.open_time <= end_dt,
            )
            .order_by(Candle.open_time)
            .all()
        )
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "open_time": r.open_time,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ])


# ---- Position Repository ----

def get_open_position() -> Optional[Position]:
    """Return the current open position, or None if flat."""
    with get_state_session() as session:
        pos = (
            session.query(Position)
            .filter(Position.side == PositionSide.LONG)
            .filter(Position.stop_stage != StopStage.CLOSED)
            .order_by(Position.created_at.desc())
            .first()
        )
        if pos:
            session.expunge(pos)
        return pos


def save_position(position: Position) -> None:
    """Insert or update a position record."""
    with get_state_session() as session:
        session.merge(position)


def close_position(trade_id: str, close_reason: str, closed_at: datetime) -> None:
    """Mark a position as closed."""
    with get_state_session() as session:
        pos = session.query(Position).filter(Position.trade_id == trade_id).first()
        if pos:
            pos.side = PositionSide.FLAT
            pos.stop_stage = StopStage.CLOSED
            pos.close_reason = close_reason
            pos.closed_at = closed_at


def update_stop_stage(
    trade_id: str,
    stop_stage: StopStage,
    current_stop: float,
    highest_price: float | None = None,
) -> None:
    """Persist stop stage transition — critical for crash recovery."""
    with get_state_session() as session:
        pos = session.query(Position).filter(Position.trade_id == trade_id).first()
        if pos:
            pos.stop_stage = stop_stage
            pos.current_stop = current_stop
            if highest_price is not None:
                pos.highest_price = highest_price
            log.info(
                "Stop stage updated",
                trade_id=trade_id,
                stage=stop_stage,
                stop=current_stop,
            )


# ---- Trade Repository ----

def save_trade(trade: Trade) -> None:
    """Persist a completed trade record."""
    with get_state_session() as session:
        session.merge(trade)


def get_all_trades() -> list[Trade]:
    """Return all completed trades, oldest first."""
    with get_state_session() as session:
        trades = session.query(Trade).order_by(Trade.entry_time).all()
        for t in trades:
            session.expunge(t)
        return trades


def get_trades_since(since_dt: datetime) -> list[Trade]:
    """Return trades closed after since_dt (for weekly drawdown calc)."""
    with get_state_session() as session:
        trades = (
            session.query(Trade)
            .filter(Trade.exit_time >= since_dt)
            .order_by(Trade.exit_time)
            .all()
        )
        for t in trades:
            session.expunge(t)
        return trades


# ---- Equity Repository ----

def save_equity_snapshot(
    snapshot_date: datetime,
    equity_usd: float,
    cash_usd: float,
    open_position_value: float = 0.0,
    cumulative_fees_usd: float = 0.0,
) -> None:
    """Upsert daily equity snapshot."""
    with get_state_session() as session:
        existing = (
            session.query(Equity)
            .filter(Equity.snapshot_date == snapshot_date)
            .first()
        )
        if existing:
            existing.equity_usd = equity_usd
            existing.cash_usd = cash_usd
            existing.open_position_value = open_position_value
            existing.cumulative_fees_usd = cumulative_fees_usd
        else:
            session.add(Equity(
                snapshot_date=snapshot_date,
                equity_usd=equity_usd,
                cash_usd=cash_usd,
                open_position_value=open_position_value,
                cumulative_fees_usd=cumulative_fees_usd,
            ))


def get_equity_since(since_dt: datetime) -> list[Equity]:
    with get_state_session() as session:
        rows = (
            session.query(Equity)
            .filter(Equity.snapshot_date >= since_dt)
            .order_by(Equity.snapshot_date)
            .all()
        )
        for r in rows:
            session.expunge(r)
        return rows


# ---- Alert Repository ----

def upsert_alert(
    alert_key: str,
    severity: str,
    message: str,
    trade_id: str | None = None,
) -> bool:
    """
    Insert alert if alert_key not already present (dedupe).
    Returns True if newly inserted, False if duplicate.
    """
    with get_state_session() as session:
        existing = session.query(Alert).filter(Alert.alert_key == alert_key).first()
        if existing:
            return False
        session.add(Alert(
            alert_key=alert_key,
            severity=severity,
            message=message,
            trade_id=trade_id,
        ))
        return True


def mark_alert_sent(alert_key: str, sent_at: datetime) -> None:
    with get_state_session() as session:
        alert = session.query(Alert).filter(Alert.alert_key == alert_key).first()
        if alert:
            alert.sent = True
            alert.sent_at = sent_at
            alert.send_attempts += 1


def get_unsent_alerts() -> list[Alert]:
    with get_state_session() as session:
        alerts = session.query(Alert).filter(Alert.sent == False).order_by(Alert.created_at).all()  # noqa: E712
        for a in alerts:
            session.expunge(a)
        return alerts
