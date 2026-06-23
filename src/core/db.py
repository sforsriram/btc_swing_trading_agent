# src/core/db.py
"""
SQLAlchemy engine + session factory for state.sqlite and candles.sqlite.
All state persists here so a crash/restart never loses an open position.
"""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _make_engine(db_path: str, echo: bool = False):
    """Create a SQLite engine with WAL mode for concurrent read safety."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=echo,
        connect_args={"check_same_thread": False},
    )

    # Enable WAL mode for better concurrent access
    @event.listens_for(engine, "connect")
    def set_wal_mode(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


# Module-level engines (initialized by init_db)
_state_engine = None
_candles_engine = None
_StateSession = None
_CandlesSession = None


def init_db(
    state_db_path: str = "data/state.sqlite",
    candles_db_path: str = "data/candles.sqlite",
    echo: bool = False,
) -> None:
    """Initialize both databases. Call once at application startup."""
    global _state_engine, _candles_engine, _StateSession, _CandlesSession

    _state_engine = _make_engine(state_db_path, echo=echo)
    _candles_engine = _make_engine(candles_db_path, echo=echo)

    _StateSession = sessionmaker(bind=_state_engine, expire_on_commit=False)
    _CandlesSession = sessionmaker(bind=_candles_engine, expire_on_commit=False)

    # Import models here to register them with Base metadata
    from src.core import models  # noqa: F401
    from src.data import candle_models  # noqa: F401

    # Create all tables
    Base.metadata.create_all(_state_engine)

    from src.data.candle_models import CandleBase
    CandleBase.metadata.create_all(_candles_engine)


@contextmanager
def get_state_session() -> Generator[Session, None, None]:
    """Context manager for state database session with auto-commit/rollback."""
    if _StateSession is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    session = _StateSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_candles_session() -> Generator[Session, None, None]:
    """Context manager for candles database session."""
    if _CandlesSession is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    session = _CandlesSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_state_engine():
    if _state_engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _state_engine


def get_candles_engine():
    if _candles_engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _candles_engine
