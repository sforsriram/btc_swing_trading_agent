# src/data/collector.py
"""
Paginated OHLCV data collector.
- Fetches up to 300 candles/call from Coinbase
- Paginates via start/end timestamps to assemble multi-year history
- Detects gaps in fetched data
- Caches incrementally to candles.sqlite (fetch once, update incrementally)
"""
from __future__ import annotations
import time
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.core.logging_setup import get_logger
from src.data.candle_models import Candle
from src.core.db import get_candles_session

if TYPE_CHECKING:
    from src.data.coinbase_client import CoinbaseClient

log = get_logger("collector")

# Granularity → seconds per candle
GRANULARITY_SECONDS = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 300,
    "FIFTEEN_MINUTE": 900,
    "THIRTY_MINUTE": 1800,
    "ONE_HOUR": 3600,
    "TWO_HOUR": 7200,
    "SIX_HOUR": 21600,
    "ONE_DAY": 86400,
    "FOUR_HOUR": 14400,
    "ONE_WEEK": 604800,
}

MAX_CANDLES_PER_REQUEST = 300


def _parse_candle_time(ts) -> datetime:
    """Parse candle timestamp (int, float, or numeric string) to UTC datetime."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
    # Handle numeric strings like "1609459200"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        pass
    return pd.to_datetime(ts, utc=True).to_pydatetime().replace(tzinfo=None)


def collect_candles(
    client: "CoinbaseClient",
    product_id: str,
    granularity: str,
    start_dt: datetime,
    end_dt: datetime,
    candles_per_request: int = MAX_CANDLES_PER_REQUEST,
    rate_limit_sleep: float = 0.5,
    gap_detection: bool = True,
) -> pd.DataFrame:
    """
    Fetch and cache OHLCV candles from Coinbase, paginating as needed.

    Strategy:
    1. Check SQLite cache for existing candles in range
    2. Identify missing date ranges
    3. Fetch missing ranges from API with pagination
    4. Upsert into SQLite cache
    5. Return full DataFrame from cache

    Args:
        client: CoinbaseClient instance
        product_id: e.g. "BTC-USD"
        granularity: e.g. "ONE_DAY"
        start_dt: inclusive start datetime (UTC, naive)
        end_dt: inclusive end datetime (UTC, naive)
        candles_per_request: max candles per API call (Coinbase limit ~300)
        rate_limit_sleep: seconds between paginated calls
        gap_detection: if True, warn on gaps in returned data

    Returns:
        DataFrame with columns: open_time, open, high, low, close, volume
        Sorted ascending by open_time.
    """
    if granularity not in GRANULARITY_SECONDS:
        raise ValueError(f"Unknown granularity: {granularity}. Valid: {list(GRANULARITY_SECONDS)}")

    seconds_per_candle = GRANULARITY_SECONDS[granularity]
    window_seconds = candles_per_request * seconds_per_candle

    log.info(
        "Starting candle collection",
        product_id=product_id,
        granularity=granularity,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
    )

    # ---- Step 1: Check what we already have in cache ----
    cached_df = _load_from_cache(product_id, granularity, start_dt, end_dt)
    if not cached_df.empty:
        cached_start = cached_df["open_time"].min()
        cached_end = cached_df["open_time"].max()
        log.info(
            "Cache hit",
            cached_rows=len(cached_df),
            cached_start=str(cached_start),
            cached_end=str(cached_end),
        )
    else:
        cached_start = None
        cached_end = None

    # ---- Step 2: Identify missing ranges ----
    fetch_ranges = _compute_missing_ranges(
        start_dt, end_dt, cached_start, cached_end, seconds_per_candle
    )

    # ---- Step 3: Fetch missing data from API ----
    all_new_candles: list[dict] = []

    for fetch_start, fetch_end in fetch_ranges:
        log.info(
            "Fetching range from API",
            fetch_start=fetch_start.isoformat(),
            fetch_end=fetch_end.isoformat(),
        )
        page_candles = _fetch_paginated(
            client=client,
            product_id=product_id,
            granularity=granularity,
            start_dt=fetch_start,
            end_dt=fetch_end,
            window_seconds=window_seconds,
            rate_limit_sleep=rate_limit_sleep,
        )
        all_new_candles.extend(page_candles)

    # ---- Step 4: Upsert into cache ----
    if all_new_candles:
        _upsert_candles(all_new_candles, product_id, granularity)
        log.info("Upserted candles to cache", count=len(all_new_candles))

    # ---- Step 5: Load full range from cache ----
    result_df = _load_from_cache(product_id, granularity, start_dt, end_dt)
    result_df = result_df.sort_values("open_time").reset_index(drop=True)

    # ---- Gap detection ----
    if gap_detection and not result_df.empty:
        _check_gaps(result_df, seconds_per_candle, product_id, granularity)

    log.info(
        "Collection complete",
        total_candles=len(result_df),
        date_range=f"{result_df['open_time'].min()} → {result_df['open_time'].max()}" if not result_df.empty else "empty",
    )

    return result_df


def _fetch_paginated(
    client: "CoinbaseClient",
    product_id: str,
    granularity: str,
    start_dt: datetime,
    end_dt: datetime,
    window_seconds: int,
    rate_limit_sleep: float,
) -> list[dict]:
    """Paginate through a date range, fetching up to 300 candles per call."""
    all_candles = []
    page_start = start_dt
    call_count = 0

    while page_start < end_dt:
        page_end = min(
            page_start + timedelta(seconds=window_seconds),
            end_dt,
        )

        start_ts = int(page_start.replace(tzinfo=timezone.utc).timestamp())
        end_ts = int(page_end.replace(tzinfo=timezone.utc).timestamp())

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                raw_candles = client.get_candles(
                    product_id=product_id,
                    granularity=granularity,
                    start=start_ts,
                    end=end_ts,
                    rate_limit_sleep=rate_limit_sleep,
                )
                all_candles.extend(raw_candles)
                call_count += 1
                log.debug(
                    "Fetched page",
                    call=call_count,
                    page_start=page_start.isoformat(),
                    page_end=page_end.isoformat(),
                    count=len(raw_candles),
                )
                break
            except Exception as e:
                if attempt == max_attempts - 1:
                    log.error("Failed to fetch candle page after retries", error=str(e))
                    raise
                wait = rate_limit_sleep * (2 ** attempt)
                log.warning("Candle fetch failed, retrying", attempt=attempt + 1, wait=wait, error=str(e))
                time.sleep(wait)

        page_start = page_end

    return all_candles


def _compute_missing_ranges(
    start_dt: datetime,
    end_dt: datetime,
    cached_start: datetime | None,
    cached_end: datetime | None,
    seconds_per_candle: int,
) -> list[tuple[datetime, datetime]]:
    """Determine which date ranges need to be fetched from the API."""
    if cached_start is None:
        # Nothing cached — fetch everything
        return [(start_dt, end_dt)]

    ranges = []
    tolerance = timedelta(seconds=seconds_per_candle)

    # Gap before cached range
    if start_dt < cached_start - tolerance:
        ranges.append((start_dt, cached_start))

    # Gap after cached range (incremental update)
    if end_dt > cached_end + tolerance:
        ranges.append((cached_end, end_dt))

    return ranges


def _load_from_cache(
    product_id: str,
    granularity: str,
    start_dt: datetime,
    end_dt: datetime,
) -> pd.DataFrame:
    """Load candles from SQLite cache for the given range."""
    try:
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

            data = [
                {
                    "open_time": r.open_time,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                }
                for r in rows
            ]
            return pd.DataFrame(data)
    except Exception as e:
        log.error("Cache load failed", error=str(e))
        return pd.DataFrame()


def _upsert_candles(
    raw_candles: list,
    product_id: str,
    granularity: str,
) -> None:
    """Upsert raw candle dicts into SQLite, ignoring duplicates."""
    records = []
    for c in raw_candles:
        try:
            # Handle both dict and object responses from SDK
            if hasattr(c, "__dict__"):
                c = vars(c)

            start_val = c.get("start") or c.get("open_time")
            records.append({
                "product_id": product_id,
                "granularity": granularity,
                "open_time": _parse_candle_time(start_val),
                "close_time": _parse_candle_time(
                    int(start_val) + GRANULARITY_SECONDS[granularity]
                    if isinstance(start_val, (int, float, str)) and str(start_val).isdigit()
                    else start_val
                ),
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": float(c.get("volume", 0)),
            })
        except Exception as e:
            log.warning("Skipping malformed candle", error=str(e), candle=str(c)[:100])

    if not records:
        return

    with get_candles_session() as session:
        stmt = sqlite_insert(Candle).values(records)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["product_id", "granularity", "open_time"]
        )
        session.execute(stmt)


def _check_gaps(
    df: pd.DataFrame,
    seconds_per_candle: int,
    product_id: str,
    granularity: str,
) -> None:
    """Warn if there are unexpected gaps in the candle series."""
    times = pd.to_datetime(df["open_time"])
    diffs = times.diff().dropna()
    expected = pd.Timedelta(seconds=seconds_per_candle)
    tolerance = pd.Timedelta(seconds=seconds_per_candle * 1.5)

    gaps = diffs[diffs > tolerance]
    if not gaps.empty:
        log.warning(
            "Gaps detected in candle data",
            product_id=product_id,
            granularity=granularity,
            gap_count=len(gaps),
            largest_gap=str(gaps.max()),
            gap_locations=[str(times.iloc[i]) for i in gaps.index[:5]],
        )
    else:
        log.debug("No gaps detected in candle data", rows=len(df))
