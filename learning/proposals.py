"""Weekly strategy review proposals.

After each week, the system analyses performance and proposes parameter
changes (e.g. adjust stop distance, change watchlist, modify regime threshold).

Every proposal:
  1. Must cite evidence (min 30 trades for quantitative proposals)
  2. Must show a safety rule check (does this violate any hard limits?)
  3. Must be approved by the human operator before taking effect
  4. Is versioned — we can always roll back

Proposals are stored in the proposals table and presented on the Strategy page
of the dashboard.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
logger = logging.getLogger(__name__)


PROPOSAL_TYPES = (
    "stop_distance",       # widen or tighten stop
    "regime_threshold",    # change when we switch risk-off
    "watchlist",           # add or remove a symbol
    "position_size",       # change max risk per trade (within Rule 4 limits)
    "cost_gate",           # change cost-R threshold
    "setup_retirement",    # retire an underperforming setup
)


@dataclass
class ProposalDraft:
    proposal_type: str
    description: str
    evidence: str
    sample_size: int
    proposed_config: dict[str, Any]
    safety_rule_check: str


def create_proposal(
    conn: sqlite3.Connection,
    draft: ProposalDraft,
    source_report_id: int | None = None,
) -> int:
    """Write a proposal to the DB. Returns the new proposal id."""
    if draft.proposal_type not in PROPOSAL_TYPES:
        raise ValueError(
            f"Unknown proposal_type: {draft.proposal_type}. "
            f"Must be one of: {PROPOSAL_TYPES}"
        )
    if draft.sample_size < 30:
        logger.warning(
            "Proposal %s has only %d trades — at least 30 recommended",
            draft.proposal_type, draft.sample_size,
        )

    conn.execute(
        """INSERT INTO proposals
           (proposal_type, description, evidence, sample_size,
            proposed_config, safety_rule_check, source_report_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            draft.proposal_type,
            draft.description,
            draft.evidence,
            draft.sample_size,
            json.dumps(draft.proposed_config),
            draft.safety_rule_check,
            source_report_id,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    proposal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    logger.info(
        "Proposal created: %s (id=%d, sample=%d)",
        draft.proposal_type, proposal_id, draft.sample_size,
    )
    return proposal_id


def approve_proposal(
    conn: sqlite3.Connection,
    proposal_id: int,
    decided_by: str,
    version_created: str | None = None,
) -> None:
    """Mark a proposal as APPROVED and optionally tag the strategy version."""
    conn.execute(
        """UPDATE proposals
           SET decision='APPROVED', decided_at=?, decided_by=?, version_created=?
           WHERE id=?""",
        (datetime.now(UTC).isoformat(), decided_by, version_created, proposal_id),
    )
    conn.commit()


def reject_proposal(
    conn: sqlite3.Connection,
    proposal_id: int,
    decided_by: str,
    reason: str = "",
) -> None:
    """Mark a proposal as REJECTED."""
    conn.execute(
        """UPDATE proposals
           SET decision='REJECTED', decided_at=?, decided_by=?,
               evidence=evidence || '\nRejection reason: ' || ?
           WHERE id=?""",
        (datetime.now(UTC).isoformat(), decided_by, reason, proposal_id),
    )
    conn.commit()


def get_pending_proposals(conn: sqlite3.Connection) -> list[dict]:
    """Return all proposals awaiting a decision."""
    rows = conn.execute(
        "SELECT * FROM proposals WHERE decision IS NULL ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]
