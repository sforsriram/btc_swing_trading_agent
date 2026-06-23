# src/data/candle_models.py
"""
ORM model for candles.sqlite (separate DB from state).
Stores OHLCV data fetched from Coinbase, cached incrementally.
"""
from __future__ import annotations
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CandleBase(DeclarativeBase):
    pass


class Candle(CandleBase):
    """
    OHLCV candle from Coinbase Advanced Trade.
    Unique on (product_id, granularity, open_time) — safe to re-insert.
    """
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("product_id", "granularity", "open_time", name="uq_candle"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    granularity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # ONE_DAY, FOUR_HOUR, etc.
    open_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    close_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return (
            f"<Candle {self.product_id} {self.granularity} "
            f"{self.open_time} C={self.close}>"
        )
