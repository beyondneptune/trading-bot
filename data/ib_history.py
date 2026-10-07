"""Fetch historical daily bars from IB and store them in the bars table.

The detectors (breakout, dip-to-support) and the signal builder (ATR stop)
need recent daily bars. A fresh paper account has no bars locally, so this
pulls them from IB. Read-only — no orders. ib_async is imported lazily.

Prices are normalised to the instrument's native unit (GBX = pence for UK
shares) so they line up with quotes and the rest of the pipeline.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from data.database import get_connection
from data.models import PriceUnit
from instruments.units import InstrumentSpec, load_watchlist

logger = logging.getLogger(__name__)
UTC = timezone.utc


def _native_factor(price_unit: PriceUnit, pm: int) -> float:
    """IB historical bars for LSE come in PENCE. Multiply by this to get the
    instrument's native unit: GBX is already pence (×1); GBP is pounds (÷pm);
    USD is unchanged (×1)."""
    if price_unit == PriceUnit.GBP:
        return 1.0 / pm
    return 1.0


async def fetch_and_store_daily_bars(
    ib_conn: Any,
    specs: list[InstrumentSpec] | None = None,
    db_path: str = "trading_bot.db",
    duration: str = "1 Y",
    bar_size: str = "1 day",
) -> dict[str, int]:
    """Fetch daily bars for each spec and upsert into the bars table.

    Returns {symbol: n_bars_stored}.
    """
    from ib_async import Stock

    if ib_conn is None or not ib_conn.is_connected:
        raise RuntimeError("Historical fetch requires a connected IB connection.")
    ib = ib_conn._ib
    specs = specs if specs is not None else load_watchlist()
    conn = get_connection(db_path)
    out: dict[str, int] = {}

    for spec in specs:
        contract = Stock(spec.symbol, "SMART", spec.currency, primaryExchange=spec.exchange)
        details = await ib.reqContractDetailsAsync(contract)
        if not details:
            logger.warning("History: no contract for %s", spec.symbol)
            out[spec.symbol] = 0
            continue
        contract = details[0].contract
        pm = details[0].priceMagnifier or 1
        factor = _native_factor(spec.price_unit, pm)

        try:
            bars = await ib.reqHistoricalDataAsync(
                contract, endDateTime="", durationStr=duration,
                barSizeSetting=bar_size, whatToShow="TRADES",
                useRTH=True, formatDate=1,
            )
        except Exception as e:
            logger.error("History: fetch failed for %s: %s", spec.symbol, e)
            out[spec.symbol] = 0
            continue

        n = 0
        for b in bars:
            # IB daily bars give a date (or naive datetime); the Bar model
            # requires a timezone-aware ts, so normalise to midnight UTC.
            from datetime import date as _date, time as _time
            d = b.date
            if isinstance(d, datetime):
                dt = d if d.tzinfo else d.replace(tzinfo=UTC)
            elif isinstance(d, _date):
                dt = datetime.combine(d, _time(0, 0), tzinfo=UTC)
            else:  # string
                dt = datetime.fromisoformat(str(d)).replace(tzinfo=UTC)
            ts_str = dt.isoformat()
            conn.execute(
                """INSERT OR IGNORE INTO bars
                   (symbol, exchange, bar_type, ts, open, high, low, close,
                    volume, price_unit)
                   VALUES (?, ?, '1d', ?, ?, ?, ?, ?, ?, ?)""",
                (spec.symbol, spec.exchange, ts_str,
                 b.open * factor, b.high * factor, b.low * factor, b.close * factor,
                 float(b.volume or 0), spec.price_unit.value),
            )
            n += 1
        conn.commit()
        out[spec.symbol] = n
        logger.info("History: stored %d daily bars for %s", n, spec.symbol)

    conn.close()
    return out
