"""Shadow mode: track what would have happened if signals you REJECTED
had been taken, and vice versa.

Shadow trades are written at approval time. After 1 day and 5 days,
the system looks up the price outcome and records the hypothetical R.

This gives you feedback on your decision quality:
  - Did you correctly reject bad signals?
  - Did you miss winners you approved but then exited early?
  - Are you systematically biased (always rejecting, always approving)?

Shadow trade outcomes are stored in the shadow_trades table.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone, timedelta

UTC = timezone.utc
logger = logging.getLogger(__name__)


def record_shadow_trade(
    conn: sqlite3.Connection,
    signal_id: int,
    approval_decision: str,  # "APPROVED" | "REJECTED"
) -> None:
    """Record a shadow trade at decision time (no outcome yet)."""
    conn.execute(
        """INSERT OR IGNORE INTO shadow_trades
           (signal_id, approval_decision, tracked_at)
           VALUES (?, ?, ?)""",
        (signal_id, approval_decision, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def score_shadow_trade(
    conn: sqlite3.Connection,
    shadow_id: int,
    outcome_1h_r: float | None,
    outcome_1d_r: float | None,
    outcome_5d_r: float | None,
) -> None:
    """Update a shadow trade with its R outcome."""
    conn.execute(
        """UPDATE shadow_trades
           SET outcome_1h_r=?, outcome_1d_r=?, outcome_5d_r=?
           WHERE id=?""",
        (outcome_1h_r, outcome_1d_r, outcome_5d_r, shadow_id),
    )
    conn.commit()


def get_shadow_accuracy(conn: sqlite3.Connection) -> dict:
    """Compute decision accuracy metrics from shadow trades.

    Returns:
      approved_avg_1d_r: average 1-day R for approved signals
      rejected_avg_1d_r: average 1-day R for rejected signals
      correct_rejects: fraction of rejects with 1d_r < 0
      missed_winners: fraction of rejects with 1d_r > 1.5
    """
    rows = conn.execute(
        """SELECT approval_decision, outcome_1d_r
           FROM shadow_trades
           WHERE outcome_1d_r IS NOT NULL"""
    ).fetchall()

    approved = [r["outcome_1d_r"] for r in rows if r["approval_decision"] == "APPROVED"]
    rejected = [r["outcome_1d_r"] for r in rows if r["approval_decision"] == "REJECTED"]

    approved_avg = sum(approved) / len(approved) if approved else 0.0
    rejected_avg = sum(rejected) / len(rejected) if rejected else 0.0

    correct_rejects = (
        sum(1 for r in rejected if r < 0) / len(rejected) if rejected else 0.0
    )
    missed_winners = (
        sum(1 for r in rejected if r > 1.5) / len(rejected) if rejected else 0.0
    )

    return {
        "n_approved": len(approved),
        "n_rejected": len(rejected),
        "approved_avg_1d_r": approved_avg,
        "rejected_avg_1d_r": rejected_avg,
        "correct_rejects_pct": correct_rejects * 100,
        "missed_winners_pct": missed_winners * 100,
    }
