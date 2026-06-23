# src/core/logging_setup.py
"""
Structured JSON logging via structlog.
- logs/agent.log  : rotating JSON (every decision/order/fill/error)
- logs/trades.log : human-readable trade ledger
"""
from __future__ import annotations
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import structlog


def setup_logging(
    level: str = "INFO",
    json_log_path: str = "logs/agent.log",
    trade_ledger_path: str = "logs/trades.log",
    rotate_max_bytes: int = 10_485_760,
    rotate_backups: int = 5,
) -> None:
    """Configure structlog + stdlib rotating handlers. Call once at startup."""

    # Ensure log directories exist
    Path(json_log_path).parent.mkdir(parents=True, exist_ok=True)
    Path(trade_ledger_path).parent.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    # ---- stdlib handlers ----
    # 1. JSON rotating file handler
    json_handler = logging.handlers.RotatingFileHandler(
        json_log_path,
        maxBytes=rotate_max_bytes,
        backupCount=rotate_backups,
        encoding="utf-8",
    )
    json_handler.setLevel(log_level)

    # 2. Console handler (human readable for dev)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # 3. Trade ledger (append-only plain text)
    trade_handler = logging.handlers.RotatingFileHandler(
        trade_ledger_path,
        maxBytes=rotate_max_bytes,
        backupCount=rotate_backups,
        encoding="utf-8",
    )
    trade_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=log_level,
        handlers=[json_handler, console_handler],
        format="%(message)s",
    )

    # ---- structlog configuration ----
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Store trade handler reference on a named logger
    trade_logger = logging.getLogger("trades")
    trade_logger.addHandler(trade_handler)
    trade_logger.propagate = False


def get_logger(name: str = "agent") -> structlog.stdlib.BoundLogger:
    """Get a named structlog logger."""
    return structlog.get_logger(name)


def log_trade(
    event: str,
    trade_id: str,
    order_id: str | None = None,
    score: float | None = None,
    regime: str | None = None,
    price: float | None = None,
    size: float | None = None,
    stop: float | None = None,
    stop_stage: str | None = None,
    pnl_pct: float | None = None,
    **kwargs,
) -> None:
    """Write a structured entry to the human-readable trade ledger."""
    trade_log = logging.getLogger("trades")
    entry = {
        "event": event,
        "trade_id": trade_id,
        "order_id": order_id,
        "score": score,
        "regime": regime,
        "price": price,
        "size": size,
        "stop": stop,
        "stop_stage": stop_stage,
        "pnl_pct": pnl_pct,
        **kwargs,
    }
    # Filter None values for readability
    entry = {k: v for k, v in entry.items() if v is not None}
    trade_log.info(str(entry))
