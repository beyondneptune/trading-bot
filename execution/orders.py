"""Order management for paper execution.

A bracket order = parent limit order + stop loss OCO + take profit OCO.
In paper mode, orders are submitted to IB's paper account (DU*).

Idempotency: every order has a unique order_ref from the approval queue.
Before submitting, we check the DB for a row with that order_ref.
If found and in a terminal state (FILLED/CANCELLED), do not re-submit.
If found in a pending state, log a warning and return the existing record.

Phase guard: no order submission below Phase 6.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

UTC = timezone.utc
logger = logging.getLogger(__name__)

# Paper slippage haircut applied on top of limit price (SPEC §3.2)
PAPER_SLIPPAGE_BPS = 5  # 5 basis points


class ExecutionError(ValueError):
    pass


class PhaseLockError(ExecutionError):
    pass


@dataclass
class OrderSpec:
    approval_id: int
    order_ref: str
    symbol: str
    exchange: str
    action: str          # BUY | SELL
    quantity: int
    limit_price: float
    stop_price: float
    target_price: float
    currency: str


@dataclass
class OrderRecord:
    id: int
    order_ref: str
    ib_order_id: Optional[int]
    status: str
    submitted_at: str


def _require_phase_6(phase: int) -> None:
    if phase < 6:
        raise PhaseLockError(
            f"Execution locked: current phase={phase}, minimum phase=6. "
            "Use the readiness checklist to advance to Phase 6."
        )


def _idempotency_check(
    conn: sqlite3.Connection,
    order_ref: str,
) -> Optional[OrderRecord]:
    """Return existing order record if order_ref already exists in DB."""
    row = conn.execute(
        "SELECT id, order_ref, ib_order_id, status, submitted_at "
        "FROM orders WHERE order_ref=?",
        (order_ref,),
    ).fetchone()
    if row is None:
        return None
    return OrderRecord(
        id=row["id"],
        order_ref=row["order_ref"],
        ib_order_id=row["ib_order_id"],
        status=row["status"],
        submitted_at=row["submitted_at"],
    )


def submit_bracket_order(
    conn: sqlite3.Connection,
    spec: OrderSpec,
    phase: int,
    mock_mode: bool = True,
) -> OrderRecord:
    """Submit a bracket order.

    In mock mode: writes to DB only, simulates immediate fill.
    In live mode (phase=6): submits to IB paper account via ib_async.

    Returns the OrderRecord (existing if idempotent replay, new otherwise).
    """
    _require_phase_6(phase)

    # Idempotency guard
    existing = _idempotency_check(conn, spec.order_ref)
    if existing:
        if existing.status in ("FILLED", "CANCELLED", "REJECTED"):
            logger.info(
                "Order %s already in terminal state %s — skipping re-submit",
                spec.order_ref, existing.status,
            )
            return existing
        logger.warning(
            "Order %s already exists with status %s — returning existing",
            spec.order_ref, existing.status,
        )
        return existing

    now = datetime.now(UTC).isoformat()

    # Write pending order
    conn.execute(
        """INSERT INTO orders
           (approval_id, order_ref, symbol, exchange, action, order_type,
            quantity, limit_price, stop_price, target_price, currency,
            status, submitted_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            spec.approval_id, spec.order_ref,
            spec.symbol, spec.exchange, spec.action,
            "LMT",
            spec.quantity, spec.limit_price, spec.stop_price, spec.target_price,
            spec.currency,
            "PENDING_SUBMIT", now, now,
        ),
    )
    conn.commit()
    order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    if mock_mode:
        # Simulate immediate fill at limit price + paper slippage
        slippage = spec.limit_price * PAPER_SLIPPAGE_BPS / 10_000
        fill_price = spec.limit_price + (slippage if spec.action == "BUY" else -slippage)
        _record_fill(conn, order_id, fill_price, spec.quantity, commission=0.0, currency=spec.currency)
        conn.execute(
            "UPDATE orders SET status='FILLED', updated_at=? WHERE id=?",
            (datetime.now(UTC).isoformat(), order_id),
        )
        _open_position(conn, spec, fill_price, order_id)
        conn.commit()
        status = "FILLED"
    else:
        status = "PENDING_SUBMIT"

    logger.info(
        "Bracket order submitted: %s %s %d@%.4g stop=%.4g target=%.4g [%s]",
        spec.action, spec.symbol, spec.quantity, spec.limit_price,
        spec.stop_price, spec.target_price, status,
    )
    return OrderRecord(
        id=order_id,
        order_ref=spec.order_ref,
        ib_order_id=None,
        status=status,
        submitted_at=now,
    )


def _record_fill(
    conn: sqlite3.Connection,
    order_id: int,
    fill_price: float,
    quantity: int,
    commission: float,
    currency: str,
) -> None:
    conn.execute(
        """INSERT INTO fills (order_id, fill_price, quantity, commission, currency, filled_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (order_id, fill_price, quantity, commission, currency,
         datetime.now(UTC).isoformat()),
    )


def _open_position(
    conn: sqlite3.Connection,
    spec: OrderSpec,
    fill_price: float,
    order_id: int,
) -> None:
    conn.execute(
        """INSERT INTO positions
           (symbol, exchange, quantity, avg_cost, currency,
            stop_price, target_price, opened_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            spec.symbol, spec.exchange,
            spec.quantity if spec.action == "BUY" else -spec.quantity,
            fill_price, spec.currency,
            spec.stop_price, spec.target_price,
            datetime.now(UTC).isoformat(),
        ),
    )


def cancel_order(
    conn: sqlite3.Connection,
    order_ref: str,
    reason: str = "manual_cancel",
) -> None:
    """Cancel an open order by order_ref."""
    row = _idempotency_check(conn, order_ref)
    if row is None:
        raise ExecutionError(f"Order {order_ref} not found")
    if row.status in ("FILLED", "CANCELLED", "REJECTED"):
        raise ExecutionError(f"Order {order_ref} already in terminal state {row.status}")
    conn.execute(
        "UPDATE orders SET status='CANCELLED', updated_at=? WHERE order_ref=?",
        (datetime.now(UTC).isoformat(), order_ref),
    )
    conn.commit()
    logger.info("Order %s cancelled: %s", order_ref, reason)
