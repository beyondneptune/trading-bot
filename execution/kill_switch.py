"""Kill switch: immediately cancel all open orders and halt new submissions.

The kill switch:
  1. Sets a kill flag in the DB (reconciliation_log table)
  2. Cancels every PENDING_SUBMIT order
  3. Broadcasts a kill_switch event via the API WebSocket
  4. Logs the action with timestamp and operator

A kill does NOT close open positions (that would be a forced market sell —
too disruptive in paper mode). Positions remain open until their stop fires.

To resume: clear the kill flag by inserting a RESUME row.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

UTC = timezone.utc
logger = logging.getLogger(__name__)


def is_killed(conn: sqlite3.Connection) -> bool:
    """True if the kill switch has been activated and not yet resumed."""
    row = conn.execute(
        """SELECT event_type FROM system_events
           WHERE event_type IN ('KILL_SWITCH', 'KILL_RESUME')
           ORDER BY logged_at DESC LIMIT 1"""
    ).fetchone()
    if row is None:
        return False
    return row["event_type"] == "KILL_SWITCH"


def activate_kill_switch(
    conn: sqlite3.Connection,
    operator: str = "dashboard",
    reason: str = "manual",
) -> int:
    """Activate kill switch. Returns the number of orders cancelled."""
    now = datetime.now(UTC).isoformat()

    # Log the kill event
    conn.execute(
        """INSERT INTO system_events (event_type, note, logged_at)
           VALUES (?, ?, ?)""",
        ("KILL_SWITCH", f"operator={operator} reason={reason}", now),
    )

    # Cancel all pending orders
    conn.execute(
        """UPDATE orders SET status='CANCELLED', updated_at=?
           WHERE status IN ('PENDING_SUBMIT', 'SUBMITTED', 'PARTIALLY_FILLED')""",
        (now,),
    )
    cancelled = conn.execute(
        "SELECT changes()"
    ).fetchone()[0]

    conn.commit()
    logger.warning(
        "KILL SWITCH activated by %s (%s). Cancelled %d orders.",
        operator, reason, cancelled,
    )
    return cancelled


def clear_kill_switch(
    conn: sqlite3.Connection,
    operator: str = "dashboard",
) -> None:
    """Resume normal operation after a kill."""
    conn.execute(
        """INSERT INTO system_events (event_type, note, logged_at)
           VALUES (?, ?, ?)""",
        ("KILL_RESUME", f"operator={operator}", datetime.now(UTC).isoformat()),
    )
    conn.commit()
    logger.info("Kill switch cleared by %s — system resumed", operator)
