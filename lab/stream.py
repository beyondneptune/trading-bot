"""Trade-update stream → database. The broker is the source of truth; every
fill/ack/cancel it streams is recorded in the trade_updates table.

handle_trade_update() is pure and testable (works for a dict or an alpaca
TradeUpdate object). run_stream() wires the live broker stream to it.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


def _get(obj, *names, default=None):
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _s(x):
    return None if x is None else str(x)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _raw(update) -> str:
    try:
        if isinstance(update, dict):
            return json.dumps(update, default=str)
        if hasattr(update, "model_dump_json"):
            return update.model_dump_json()
        return json.dumps(str(update))
    except Exception:
        return str(update)


def handle_trade_update(conn: sqlite3.Connection, update) -> None:
    """Record one trade update (dict or alpaca TradeUpdate) in the database."""
    event = str(_get(update, "event", default=""))
    order = _get(update, "order", default={})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO trade_updates
           (event, order_id, client_order_id, symbol, qty, filled_qty,
            filled_avg_price, status, side, received_at, raw)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event,
            _s(_get(order, "id")),
            _s(_get(order, "client_order_id")),
            _s(_get(order, "symbol")),
            _num(_get(order, "qty")),
            _num(_get(order, "filled_qty")),
            _num(_get(order, "filled_avg_price")),
            _s(_get(order, "status")),
            _s(_get(order, "side")),
            now,
            _raw(update),
        ),
    )
    conn.commit()
    logger.info("trade update recorded: event=%s symbol=%s",
                event, _get(order, "symbol"))


def run_stream(broker, db_path: str) -> None:
    """Connect the live broker trade-update stream to the database. Blocks."""
    from data.database import get_connection
    conn = get_connection(db_path)

    def on_update(update):
        try:
            handle_trade_update(conn, update)
        except Exception as e:
            logger.error("Failed to record trade update: %s", e)

    stream = broker.stream_trade_updates(on_update)
    logger.info("Trade-update stream starting…")
    stream.run()   # blocks on the websocket
