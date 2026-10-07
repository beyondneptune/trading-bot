"""Historical bar management: fetch via IB, cache in SQLite, mock fallback.

Fetch strategy:
  1. Read what's already in the local cache.
  2. Request only the missing tail from IB (never re-fetch what we have).
  3. IB has pacing limits: 60 requests per 10 minutes; we stay well under by
     batching and sleeping between symbols.
  4. In mock_mode, skip IB entirely and return synthetic bars.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.settings import load_settings
from data.database import get_connection
from data.mock_data import generate_daily_bars
from data.models import Bar, DataQuality
from instruments.units import InstrumentSpec, PriceUnit

logger = logging.getLogger(__name__)

UTC = timezone.utc
_IB_PACING_SLEEP = 0.6  # seconds between IB historical requests (safety margin)


# ---------------------------------------------------------------------------
# Cache read / write
# ---------------------------------------------------------------------------

def _load_cached_bars(
    conn: sqlite3.Connection,
    symbol: str,
    exchange: str,
    bar_type: str,
    since: Optional[datetime] = None,
) -> list[Bar]:
    sql = """
        SELECT ts, open, high, low, close, volume, price_unit
        FROM bars
        WHERE symbol=? AND exchange=? AND bar_type=?
    """
    params: list = [symbol, exchange, bar_type]
    if since is not None:
        sql += " AND ts >= ?"
        params.append(since.isoformat())
    sql += " ORDER BY ts ASC"

    rows = conn.execute(sql, params).fetchall()
    bars = []
    for r in rows:
        bars.append(
            Bar(
                symbol=symbol,
                exchange=exchange,
                bar_type=bar_type,
                ts=datetime.fromisoformat(r["ts"]),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                price_unit=PriceUnit(r["price_unit"]),
                quality=DataQuality.CACHED,
            )
        )
    return bars


def _save_bars(conn: sqlite3.Connection, bars: list[Bar]) -> int:
    """Insert bars, ignoring duplicates. Returns count inserted."""
    sql = """
        INSERT OR IGNORE INTO bars
            (symbol, exchange, bar_type, ts, open, high, low, close, volume, price_unit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    count = 0
    for b in bars:
        conn.execute(
            sql,
            (
                b.symbol, b.exchange, b.bar_type,
                b.ts.isoformat(),
                b.open, b.high, b.low, b.close, b.volume,
                b.price_unit.value,
            ),
        )
        count += 1
    conn.commit()
    return count


def _latest_cached_ts(
    conn: sqlite3.Connection, symbol: str, exchange: str, bar_type: str
) -> Optional[datetime]:
    row = conn.execute(
        "SELECT MAX(ts) FROM bars WHERE symbol=? AND exchange=? AND bar_type=?",
        (symbol, exchange, bar_type),
    ).fetchone()
    val = row[0] if row else None
    if val:
        return datetime.fromisoformat(val)
    return None


# ---------------------------------------------------------------------------
# IB fetch (real connection)
# ---------------------------------------------------------------------------

async def _fetch_from_ib(
    ib_conn,
    spec: InstrumentSpec,
    bar_size: str,
    duration: str,
    end_dt: Optional[datetime] = None,
) -> list[Bar]:
    """Fetch bars from IB Gateway. Returns empty list on any error."""
    try:
        from ib_async import Stock, Contract  # type: ignore[import]

        if spec.exchange in ("LSE",):
            contract = Stock(spec.symbol, "LSE", spec.currency)
        else:
            contract = Stock(spec.symbol, spec.exchange, spec.currency)

        end_str = "" if end_dt is None else end_dt.strftime("%Y%m%d %H:%M:%S")
        raw_bars = await ib_conn.ib.reqHistoricalDataAsync(
            contract,
            endDateTime=end_str,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
        )
        bars = []
        for rb in raw_bars:
            ts = rb.date
            if not isinstance(ts, datetime):
                ts = datetime.strptime(str(ts), "%Y%m%d").replace(tzinfo=UTC)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            bars.append(
                Bar(
                    symbol=spec.symbol,
                    exchange=spec.exchange,
                    bar_type="1d",
                    ts=ts,
                    open=rb.open,
                    high=rb.high,
                    low=rb.low,
                    close=rb.close,
                    volume=float(rb.volume),
                    price_unit=spec.price_unit,
                    quality=DataQuality.LIVE,
                )
            )
        logger.info("Fetched %d bars from IB for %s", len(bars), spec.symbol)
        return bars
    except Exception as exc:
        logger.warning("IB historical fetch failed for %s: %s", spec.symbol, exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_daily_bars(
    spec: InstrumentSpec,
    db_path: str,
    years: int = 3,
    ib_conn=None,
) -> list[Bar]:
    """Return (and cache) daily bars for `spec`, covering `years` of history.

    In mock_mode or when IB fails, returns synthetic bars.
    """
    settings = load_settings()
    conn = get_connection(db_path)

    try:
        if settings.mock_mode:
            cached = _load_cached_bars(conn, spec.symbol, spec.exchange, "1d")
            if not cached:
                mock_bars = generate_daily_bars(spec, n_days=252 * years)
                _save_bars(conn, mock_bars)
                cached = mock_bars
            return cached

        # Real mode: find gap and fetch only what's missing
        latest = _latest_cached_ts(conn, spec.symbol, spec.exchange, "1d")
        cutoff = datetime.now(UTC) - timedelta(days=years * 365)

        if latest is None or latest < cutoff:
            duration = f"{years} Y"
            bars = await _fetch_from_ib(ib_conn, spec, "1 day", duration)
            if bars:
                _save_bars(conn, bars)
            else:
                # Rule 11: no synthetic fallback outside mock mode.
                raise NoDataError(
                    f"No IB bars returned for {spec.symbol}; refusing to "
                    "fabricate synthetic data outside mock mode (rule 11)."
                )
        elif (datetime.now(UTC) - latest).days > 1:
            # incremental update
            days_missing = (datetime.now(UTC) - latest).days + 1
            duration = f"{days_missing} D"
            await asyncio.sleep(_IB_PACING_SLEEP)
            bars = await _fetch_from_ib(ib_conn, spec, "1 day", duration)
            if bars:
                _save_bars(conn, bars)

        return _load_cached_bars(conn, spec.symbol, spec.exchange, "1d")
    finally:
        conn.close()


class NoDataError(RuntimeError):
    """Required real data is unavailable (CLAUDE.md rule 11: bad/missing data
    means no trade — raise, never fabricate)."""


def get_daily_bars_sync(
    spec: InstrumentSpec,
    db_path: str,
    years: int = 3,
) -> list[Bar]:
    """Synchronous wrapper — uses cached data only (no live fetch).

    Outside mock mode, missing data RAISES (never falls back to synthetic
    data) per CLAUDE.md rule 11.
    """
    settings = load_settings()
    conn = get_connection(db_path)
    try:
        cached = _load_cached_bars(conn, spec.symbol, spec.exchange, "1d")
        if cached:
            return cached
        if settings.mock_mode:
            mock_bars = generate_daily_bars(spec, n_days=252 * years)
            _save_bars(conn, mock_bars)
            return mock_bars
        raise NoDataError(
            f"No cached daily bars for {spec.symbol}/{spec.exchange}; "
            "synthetic data is forbidden outside mock mode (rule 11)."
        )
    finally:
        conn.close()
