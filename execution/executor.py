"""Bridge an approved signal to a paper bracket order.

This is the glue between the approval queue (Phase 4a) and order execution
(Phase 6). When the user approves a signal, THIS module runs the final
real-time safety gates and, only if they all pass, submits the bracket order.

Final gates (checked at execution time, not just signal time):
  - Kill switch must not be active (rule 13)
  - Loss limits / circuit breakers must allow new buys (rule 9)
  - Phase must be ≥ 6 (rule 8/13 — also enforced inside submit_bracket_order)

If a gate blocks, the approval stays recorded (for audit) but no order is
placed, and we return the reason. Submission is idempotent on order_ref.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional

from config.settings import Settings, load_settings
from execution.kill_switch import is_killed
from execution.orders import OrderSpec, PhaseLockError, submit_bracket_order
from risk.limits import check_loss_limits

logger = logging.getLogger(__name__)

_DEFAULT_EQUITY_GBP = 10_000.0


@dataclass
class ExecutionResult:
    executed: bool
    status: str           # FILLED | PENDING_SUBMIT | BLOCKED | ERROR
    order_ref: Optional[str]
    detail: str


def _current_equity(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT equity_gbp FROM equity_snapshots "
        "ORDER BY snapped_at DESC LIMIT 1"
    ).fetchone()
    return float(row[0]) if row else _DEFAULT_EQUITY_GBP


async def execute_approved_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    settings: Optional[Settings] = None,
    ib_conn: object = None,
) -> ExecutionResult:
    """Run final safety gates and submit the bracket order for an approved
    signal. Call this AFTER the approval has been recorded.

    In mock mode the order is simulated. In live mode (ib_conn connected) a
    real bracket order is routed to the IB paper account.
    """
    settings = settings or load_settings()

    sig = conn.execute(
        "SELECT * FROM signals WHERE id=?", (signal_id,)
    ).fetchone()
    if sig is None:
        return ExecutionResult(False, "ERROR", None, f"Signal {signal_id} not found.")

    if sig["action"] != "BUY":
        return ExecutionResult(
            False, "BLOCKED", None,
            f"Signal {signal_id} action is {sig['action']} — not executable.",
        )
    if sig["size"] is None or sig["entry_price"] is None or sig["stop_price"] is None:
        return ExecutionResult(
            False, "BLOCKED", None,
            f"Signal {signal_id} is missing order levels — not executable.",
        )

    appr = conn.execute(
        "SELECT id, order_ref FROM approvals "
        "WHERE signal_id=? AND decision='APPROVED'",
        (signal_id,),
    ).fetchone()
    if appr is None:
        return ExecutionResult(
            False, "ERROR", None,
            f"No approval found for signal {signal_id} — approve it first.",
        )

    # --- Real-time safety gates ---
    if is_killed(conn):
        logger.warning("Execution blocked by kill switch (signal %d)", signal_id)
        return ExecutionResult(False, "BLOCKED", appr["order_ref"],
                               "Kill switch is active — no new orders.")

    limits = check_loss_limits(_current_equity(conn), conn)
    if not limits.new_buys_allowed:
        logger.warning("Execution blocked by loss limits (signal %d): %s",
                       signal_id, limits.reason)
        return ExecutionResult(False, "BLOCKED", appr["order_ref"], limits.reason)

    # Resolve the trading currency from the instrument spec.
    from instruments.units import find_instrument, load_watchlist
    spec = find_instrument(load_watchlist(), sig["symbol"], sig["exchange"])
    currency = spec.currency if spec is not None else "GBP"

    order_spec = OrderSpec(
        approval_id=appr["id"],
        order_ref=appr["order_ref"],
        symbol=sig["symbol"],
        exchange=sig["exchange"],
        action="BUY",
        quantity=int(sig["size"]),
        limit_price=float(sig["entry_price"]),
        stop_price=float(sig["stop_price"]),
        target_price=float(sig["target_price"]),
        currency=currency,
    )

    try:
        if settings.mock_mode:
            record = submit_bracket_order(
                conn, order_spec, phase=settings.phase, mock_mode=True,
            )
        else:
            # Live: route a real bracket order to the IB paper account.
            from execution.live_orders import submit_bracket_order_live
            if ib_conn is None or not getattr(ib_conn, "is_connected", False):
                return ExecutionResult(
                    False, "BLOCKED", appr["order_ref"],
                    "Live mode but no connected IB connection available.",
                )
            record = await submit_bracket_order_live(
                conn, order_spec, ib_conn, phase=settings.phase,
            )
    except PhaseLockError as e:
        logger.warning("Execution blocked by phase lock (signal %d): %s", signal_id, e)
        return ExecutionResult(False, "BLOCKED", appr["order_ref"], str(e))

    executed = record.status in ("FILLED", "PENDING_SUBMIT", "SUBMITTED")
    detail = (
        f"Order {record.order_ref} {record.status} "
        f"({'mock fill' if settings.mock_mode else 'sent to IB paper'})."
    )
    logger.info("Signal %d executed: %s", signal_id, detail)
    return ExecutionResult(executed, record.status, record.order_ref, detail)
