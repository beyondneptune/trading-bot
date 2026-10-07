"""Live IB bracket-order routing (async).

Imported only when actually placing a live order, and ib_async is imported
lazily inside the function — so the mock-mode code path and the test suite
(which may run on Python 3.9 without ib_async) are never affected.

Handles the LSE pricing quirks proven out in ops/place_first_order.py:
  - Orders are priced in PENCE (same unit as market data).
  - The tick-size rule (from the contract's market rule) is in POUNDS, so
    tick_pence = pound_tick × priceMagnifier (100 for GBX shares).
  - Our stored prices are in the instrument's native unit (GBX pence or GBP
    pounds); we convert to the IB order unit and back for the DB.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from data.models import PriceUnit
from execution.orders import (
    ExecutionError,
    OrderRecord,
    OrderSpec,
    _idempotency_check,
    _open_position,
    _record_fill,
    _require_phase_6,
)
from instruments.units import find_instrument, load_watchlist

logger = logging.getLogger(__name__)

FILL_WAIT_SECONDS = 20


def _round_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return round(price, 4)
    return round(round(price / tick) * tick, 6)


async def _tick_for_price(ib: Any, details: Any, pounds: float) -> float:
    """Pound-denominated tick that applies at `pounds`, via the market rule."""
    for rid in (details.marketRuleIds or "").split(","):
        rid = rid.strip()
        if not rid:
            continue
        try:
            increments = await ib.reqMarketRuleAsync(int(rid))
        except Exception:
            continue
        applicable = None
        for pi in sorted(increments, key=lambda x: x.lowEdge):
            if pounds >= pi.lowEdge:
                applicable = pi.increment
        if applicable and applicable > 0:
            return float(applicable)
    return 0.01


def _ib_factor(price_unit: PriceUnit, pm: int) -> float:
    """Multiply a native-unit price by this to get the IB order price (pence
    for GBX/GBP; GBX is already pence so factor 1, GBP pounds×pm→pence)."""
    return pm if price_unit == PriceUnit.GBP else 1.0


async def submit_bracket_order_live(
    conn: sqlite3.Connection,
    spec: OrderSpec,
    ib_conn: Any,
    phase: int,
) -> OrderRecord:
    """Place a REAL bracket order on the IB paper account and record it.

    Idempotent on spec.order_ref. Returns an OrderRecord.
    """
    from datetime import datetime, timezone

    from config.settings import load_settings

    # L0: the IB trading path is switched off unless explicitly selected.
    if load_settings().trading_backend != "ib":
        raise ExecutionError(
            "IB trading is disabled (trading_backend != 'ib'). The IB path is "
            "kept but switched off; the lab trades via Alpaca (from L1)."
        )

    from ib_async import Stock

    utc = timezone.utc
    _require_phase_6(phase)

    existing = _idempotency_check(conn, spec.order_ref)
    if existing:
        logger.info("Order %s already exists (%s) — not re-submitting",
                    spec.order_ref, existing.status)
        return existing

    if ib_conn is None or not ib_conn.is_connected:
        raise RuntimeError("Live order requires a connected IB connection.")
    ib = ib_conn._ib

    inst = find_instrument(load_watchlist(), spec.symbol, spec.exchange)
    price_unit = inst.price_unit if inst else PriceUnit.GBX

    contract = Stock(spec.symbol, "SMART", spec.currency, primaryExchange=spec.exchange)
    details_list = await ib.reqContractDetailsAsync(contract)
    if not details_list:
        raise RuntimeError(f"No IB contract for {spec.symbol}/{spec.exchange}.")
    details = details_list[0]
    contract = details.contract
    pm = details.priceMagnifier or 1
    factor = _ib_factor(price_unit, pm)

    # Native price -> IB order price, with the correct pence tick.
    pounds = (spec.limit_price * factor) / pm
    pound_tick = await _tick_for_price(ib, details, pounds)
    tick = pound_tick * pm
    ib_limit = _round_tick(spec.limit_price * factor, tick)
    ib_stop = _round_tick(spec.stop_price * factor, tick)
    ib_target = _round_tick(spec.target_price * factor, tick)

    logger.info(
        "Live bracket %s: qty=%d limit=%s stop=%s target=%s (IB pence, tick=%s)",
        spec.symbol, spec.quantity, ib_limit, ib_stop, ib_target, tick,
    )

    bracket = ib.bracketOrder(
        spec.action, spec.quantity,
        limitPrice=ib_limit, takeProfitPrice=ib_target, stopLossPrice=ib_stop,
    )
    trades = [ib.placeOrder(contract, o) for o in bracket]
    parent = trades[0]

    # Write the order row immediately (SUBMITTED), then wait for the fill.
    now = datetime.now(utc).isoformat()
    conn.execute(
        """INSERT INTO orders
           (approval_id, order_ref, ib_order_id, symbol, exchange, action,
            order_type, quantity, limit_price, stop_price, target_price,
            currency, status, submitted_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'LMT', ?, ?, ?, ?, ?, 'SUBMITTED', ?, ?)""",
        (spec.approval_id, spec.order_ref, parent.order.orderId,
         spec.symbol, spec.exchange, spec.action, spec.quantity,
         spec.limit_price, spec.stop_price, spec.target_price, spec.currency,
         now, now),
    )
    conn.commit()
    order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    import asyncio
    for _ in range(FILL_WAIT_SECONDS):
        await asyncio.sleep(1.0)
        if parent.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
            break

    st = parent.orderStatus.status
    avg_ib = parent.orderStatus.avgFillPrice or 0.0

    if st == "Filled" and avg_ib > 0:
        native_fill = avg_ib / factor      # back to the instrument's native unit
        conn.execute(
            "UPDATE orders SET status='FILLED', updated_at=? WHERE id=?",
            (datetime.now(utc).isoformat(), order_id),
        )
        _record_fill(conn, order_id, float(native_fill), spec.quantity,
                     commission=0.0, currency=spec.currency)
        _open_position(conn, spec, float(native_fill), order_id)
        conn.commit()
        status = "FILLED"
        logger.info("Live order %s FILLED @ %.4g (native)", spec.order_ref, native_fill)
    else:
        conn.execute(
            "UPDATE orders SET status=?, updated_at=? WHERE id=?",
            (st.upper() if st else "SUBMITTED", datetime.now(utc).isoformat(), order_id),
        )
        conn.commit()
        status = st or "SUBMITTED"
        logger.warning("Live order %s not filled (status=%s)", spec.order_ref, status)

    return OrderRecord(id=order_id, order_ref=spec.order_ref,
                       ib_order_id=parent.order.orderId, status=status, submitted_at=now)
