"""The propose-only learning loop — periodic Phase-7 pass over closed trades.

Once a cycle (see run_bot.LEARN_EVERY_N_TICKS) the bot reviews its own track
record and, where the evidence is strong enough, writes a *proposal* for the
human operator to approve on the Strategy page. It ties together the existing
Phase-7 pieces:

  - learning/r_stats.py   → expectancy, win rate, profit factor per setup
  - learning/null_model.py → is the edge distinguishable from chance?
  - learning/proposals.py  → persist a reviewable, versioned proposal

Three hard rules, by design:

  1. PROPOSE-ONLY. This loop NEVER changes a parameter, retires a setup, or
     touches live config. It only writes rows to the ``proposals`` table. A
     human must approve a proposal before it takes effect. (This is the
     "propose-only self-improvement" the operator authorised — distinct from
     full-auto *execution*, which has its own kill-switch-gated path.)

  2. EVIDENCE-GATED. No proposal is made for a setup with fewer than
     ``min_trades`` (default 30) closed trades — the same floor the null model
     and proposals module require. Below that we report "insufficient data"
     and do nothing.

  3. IDEMPOTENT. The pass runs on every learning tick. It must not spam the
     table with duplicates, so before creating a proposal it checks whether an
     equivalent one is already pending.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from learning.null_model import MIN_TRADES_FOR_TEST, run_permutation_test, NullModelResult
from learning.proposals import ProposalDraft, create_proposal, get_pending_proposals
from learning.r_stats import RStats, load_r_multiples, compute_r_stats

UTC = timezone.utc
logger = logging.getLogger(__name__)


@dataclass
class LearningPass:
    checked_at: datetime
    n_closed: int
    overall: RStats
    null_model: NullModelResult
    per_setup: dict[str, RStats]
    proposals_created: list[int] = field(default_factory=list)
    note: str = ""

    @property
    def summary(self) -> str:
        return (
            f"learning pass: {self.n_closed} closed trades, "
            f"overall {self.overall.expectancy:+.2f}R, {self.null_model.verdict}, "
            f"{len(self.proposals_created)} new proposal(s)"
        )


def _distinct_setups(conn: sqlite3.Connection) -> list[str]:
    """Setups (signal_type) that have at least one closed, R-scored position."""
    rows = conn.execute(
        """SELECT DISTINCT s.signal_type
           FROM positions p
           JOIN signals s ON p.open_signal_id = s.id
           WHERE p.closed_at IS NOT NULL AND p.realised_r IS NOT NULL
           ORDER BY s.signal_type"""
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _pending_retired_setups(conn: sqlite3.Connection) -> set[str]:
    """Setups that already have a pending (undecided) retirement proposal, so we
    don't create a second one on the next learning tick."""
    retired: set[str] = set()
    for p in get_pending_proposals(conn):
        if p.get("proposal_type") != "setup_retirement":
            continue
        try:
            cfg = json.loads(p.get("proposed_config") or "{}")
        except (ValueError, TypeError):
            continue
        setup = cfg.get("setup")
        if setup:
            retired.add(setup)
    return retired


def run_learning_pass(
    conn: sqlite3.Connection,
    *,
    min_trades: int = MIN_TRADES_FOR_TEST,
    p_threshold: float = 0.05,
    n_simulations: int = 10_000,
    now: datetime | None = None,
) -> LearningPass:
    """Review closed trades and emit propose-only proposals where warranted.

    Current proposal: retire a setup that, over >= ``min_trades`` closed trades,
    shows no positive edge (expectancy <= 0 or profit factor < 1.0). Retirement
    only removes a setup from scanning, so it can never breach a hard risk limit;
    it still needs human approval to take effect.
    """
    now = now or datetime.now(UTC)

    overall_rs = load_r_multiples(conn)
    overall = compute_r_stats(overall_rs)
    null = run_permutation_test(
        overall_rs, n_simulations=n_simulations, p_threshold=p_threshold
    )

    per_setup: dict[str, RStats] = {}
    created: list[int] = []

    if overall.n < min_trades:
        note = (
            f"insufficient data: {overall.n}/{min_trades} closed trades — "
            f"no proposals until the sample is large enough to measure edge"
        )
        logger.info("Learning pass: %s", note)
        return LearningPass(
            checked_at=now, n_closed=overall.n, overall=overall,
            null_model=null, per_setup=per_setup, note=note,
        )

    already_pending = _pending_retired_setups(conn)

    for setup in _distinct_setups(conn):
        rs = load_r_multiples(conn, setup_name=setup)
        stats = compute_r_stats(rs)
        per_setup[setup] = stats

        if stats.n < min_trades:
            continue
        if stats.expectancy > 0.0 and stats.profit_factor >= 1.0:
            continue  # setup is pulling its weight — leave it alone
        if setup in already_pending:
            continue  # operator already has this proposal to review

        setup_null = run_permutation_test(
            rs, n_simulations=n_simulations, p_threshold=p_threshold
        )
        evidence = (
            f"{stats.n} closed trades: expectancy {stats.expectancy:+.2f}R, "
            f"profit factor {stats.profit_factor:.2f}, win rate {stats.win_rate:.0%}, "
            f"max consecutive losses {stats.max_consec_losses}. "
            f"Null model: {setup_null.verdict}."
        )
        draft = ProposalDraft(
            proposal_type="setup_retirement",
            description=(
                f"Retire setup '{setup}': {stats.n} trades show no positive edge "
                f"(expectancy {stats.expectancy:+.2f}R, profit factor "
                f"{stats.profit_factor:.2f})."
            ),
            evidence=evidence,
            sample_size=stats.n,
            proposed_config={"setup": setup, "action": "retire"},
            safety_rule_check=(
                "Retiring a setup only removes it from scanning (fewer trades); "
                "it cannot breach any hard risk limit. Requires human approval "
                "before taking effect."
            ),
        )
        pid = create_proposal(conn, draft)
        created.append(pid)
        logger.info(
            "Learning pass proposed retiring setup '%s' (proposal id=%d)", setup, pid
        )

    note = (
        f"reviewed {overall.n} closed trades across {len(per_setup)} setup(s); "
        f"{len(created)} proposal(s) created"
    )
    result = LearningPass(
        checked_at=now, n_closed=overall.n, overall=overall,
        null_model=null, per_setup=per_setup, proposals_created=created, note=note,
    )
    logger.info("Learning pass: %s", result.summary)
    return result
