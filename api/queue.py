"""Approval queue — idempotent, single-queue for both Telegram and dashboard.

Rules (CLAUDE.md rule 2, SPEC section 1A):
  - One approval creates at most one order set.
  - Approvals from Telegram and dashboard go through this single queue;
    double-approvals are impossible because signal_id has a UNIQUE constraint.
  - A signal cannot be approved after it has expired.
  - order_ref is a UUID generated here and checked for pre-existing open orders
    before any execution (Phase 6).
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

UTC = timezone.utc


class Decision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalError(ValueError):
    pass


def approve_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    interface: str,
    reason: Optional[str] = None,
    confidence: Optional[int] = None,
    mood: Optional[str] = None,
) -> str:
    """Approve a signal. Returns the order_ref.

    Raises ApprovalError if:
      - signal is already decided
      - signal has expired
    """
    signal = conn.execute(
        "SELECT id, expired_at, expired_reason FROM signals WHERE id=?",
        (signal_id,)
    ).fetchone()

    if signal is None:
        raise ApprovalError(f"Signal {signal_id} not found.")

    if signal["expired_at"] is not None:
        raise ApprovalError(
            f"Signal {signal_id} has expired: {signal['expired_reason']}"
        )

    existing = conn.execute(
        "SELECT decision FROM approvals WHERE signal_id=?", (signal_id,)
    ).fetchone()
    if existing is not None:
        raise ApprovalError(
            f"Signal {signal_id} already has a decision: {existing['decision']}"
        )

    order_ref = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO approvals
               (signal_id, decision, decided_at, interface, reason, confidence, mood, order_ref)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (signal_id, Decision.APPROVED.value,
         datetime.now(UTC).isoformat(),
         interface, reason, confidence, mood, order_ref),
    )
    conn.commit()
    logger.info("Signal %d APPROVED via %s (order_ref=%s)", signal_id, interface, order_ref)
    return order_ref


def reject_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    interface: str,
    reason: Optional[str] = None,
) -> None:
    """Reject a signal."""
    existing = conn.execute(
        "SELECT decision FROM approvals WHERE signal_id=?", (signal_id,)
    ).fetchone()
    if existing is not None:
        raise ApprovalError(
            f"Signal {signal_id} already has a decision: {existing['decision']}"
        )

    conn.execute(
        """INSERT INTO approvals
               (signal_id, decision, decided_at, interface, reason)
           VALUES (?, ?, ?, ?, ?)""",
        (signal_id, Decision.REJECTED.value,
         datetime.now(UTC).isoformat(), interface, reason),
    )
    conn.commit()
    logger.info("Signal %d REJECTED via %s", signal_id, interface)


def expire_signal(
    conn: sqlite3.Connection,
    signal_id: int,
    reason: str,
) -> None:
    """Mark a signal as expired so it can no longer be approved."""
    conn.execute(
        "UPDATE signals SET expired_at=?, expired_reason=? WHERE id=?",
        (datetime.now(UTC).isoformat(), reason, signal_id),
    )
    conn.commit()
    logger.info("Signal %d expired: %s", signal_id, reason)


def get_pending_signals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return signals that are valid (not expired, not decided)."""
    return conn.execute(
        """SELECT s.* FROM signals s
           LEFT JOIN approvals a ON s.id = a.signal_id
           WHERE s.expired_at IS NULL AND a.id IS NULL
           ORDER BY s.created_at DESC""",
    ).fetchall()
