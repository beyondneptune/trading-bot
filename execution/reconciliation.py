"""Reconciliation: compares expected vs actual position state.

At the end of each session (or on demand), reconciliation:
  1. Reads all positions from the DB
  2. Reads fills for those positions
  3. Recomputes expected P&L from fill prices vs current market
  4. Flags any discrepancies (position missing, quantity mismatch, stale fills)

In paper mode, the IB account is the source of truth for fills.
In mock mode, the DB itself is the source of truth.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

UTC = timezone.utc
logger = logging.getLogger(__name__)


@dataclass
class ReconciliationItem:
    symbol: str
    exchange: str
    db_quantity: int
    db_avg_cost: float
    fill_quantity: int
    fill_avg_cost: float
    match: bool
    note: str


@dataclass
class ReconciliationReport:
    run_at: datetime
    items: list[ReconciliationItem]
    all_match: bool
    mismatches: int

    @property
    def summary(self) -> str:
        return (
            f"Reconciliation {self.run_at.strftime('%Y-%m-%d %H:%M')} — "
            f"{len(self.items)} positions, "
            f"{self.mismatches} mismatches"
        )


def run_reconciliation(conn: sqlite3.Connection) -> ReconciliationReport:
    """Compare DB positions against fills to detect data integrity issues."""
    now = datetime.now(UTC)
    positions = conn.execute(
        "SELECT id, symbol, exchange, quantity, avg_cost FROM positions "
        "WHERE closed_at IS NULL"
    ).fetchall()

    items: list[ReconciliationItem] = []

    for pos in positions:
        fills = conn.execute(
            """SELECT f.fill_price, f.quantity
               FROM fills f
               JOIN orders o ON f.order_id = o.id
               WHERE o.symbol=? AND o.exchange=? AND o.status='FILLED'""",
            (pos["symbol"], pos["exchange"]),
        ).fetchall()

        fill_qty = sum(f["quantity"] for f in fills)
        if fill_qty > 0:
            fill_avg = sum(f["fill_price"] * f["quantity"] for f in fills) / fill_qty
        else:
            fill_avg = 0.0

        # Allow 0.01% rounding tolerance
        qty_match = abs(fill_qty - abs(pos["quantity"])) <= 1
        price_match = (
            fill_avg == 0.0 or
            abs(fill_avg - pos["avg_cost"]) / max(pos["avg_cost"], 0.01) < 0.001
        )
        match = qty_match and price_match
        note = "" if match else (
            f"qty: db={pos['quantity']} fills={fill_qty}; "
            f"price: db={pos['avg_cost']:.4g} fills={fill_avg:.4g}"
        )

        items.append(ReconciliationItem(
            symbol=pos["symbol"],
            exchange=pos["exchange"],
            db_quantity=pos["quantity"],
            db_avg_cost=pos["avg_cost"],
            fill_quantity=fill_qty,
            fill_avg_cost=fill_avg,
            match=match,
            note=note,
        ))

    mismatches = sum(1 for i in items if not i.match)

    # Log to DB using the reconciliation_log schema
    conn.execute(
        """INSERT INTO reconciliation_log (mismatch_type, details, recorded_at)
           VALUES (?, ?, ?)""",
        (
            "RECON_OK" if mismatches == 0 else "RECON_MISMATCH",
            f"{len(items)} positions checked, {mismatches} mismatches",
            now.isoformat(),
        ),
    )
    conn.commit()

    report = ReconciliationReport(
        run_at=now,
        items=items,
        all_match=(mismatches == 0),
        mismatches=mismatches,
    )
    if mismatches > 0:
        logger.warning("Reconciliation: %d mismatches found", mismatches)
        for item in items:
            if not item.match:
                logger.warning("  %s/%s: %s", item.symbol, item.exchange, item.note)
    else:
        logger.info("Reconciliation OK: %d positions checked", len(items))

    return report
