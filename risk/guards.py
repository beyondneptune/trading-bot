"""Hard-coded safety guards (CLAUDE.md rules 1 and 13).

These functions are called before any state-changing operation.
They have no side-effects other than raising or exiting — they are
intentionally simple so they are easy to audit.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


class ReadOnlyError(PermissionError):
    """Raised when order placement is attempted before Phase 6."""


def assert_paper_account(account_id: str) -> None:
    """Safety rule 1: paper accounts start with 'DU'.

    If the account is not a paper account the process exits immediately.
    No setting disables this check.
    """
    if not account_id.startswith("DU"):
        msg = (
            f"SAFETY RULE 1 VIOLATED — account '{account_id}' is not a paper "
            "account (expected 'DU…' prefix).  Refusing all operations and exiting."
        )
        logger.critical(msg)
        sys.exit(msg)


def assert_readonly_phase(phase: int) -> None:
    """Safety rule 13: IB is read-only in Phases 0-5.

    Any code path that would place, modify, or cancel an order must call
    this first.  Phase 6+ is where execution is unlocked (after the user
    turns off IB Gateway's Read-Only API setting).
    """
    if phase < 6:
        raise ReadOnlyError(
            f"Order placement is blocked in Phase {phase}.  "
            "Execution is only available from Phase 6 onwards (CLAUDE.md rule 13)."
        )
