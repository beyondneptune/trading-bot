"""Readiness checklist for advancing from Phase 5 (paper observation) to
Phase 6 (paper execution with auto-fills).

The checklist ensures:
  1. Enough paper trades to measure statistical edge
  2. No loss-limit breaches in the last 90 days
  3. AI spend within budget
  4. Reconciliation log is clean
  5. Human operator has reviewed weekly strategy proposals
  6. At least one complete strategy version is in the DB

All criteria must pass. The readiness page shows current status and
blocks phase advance until the checklist is green.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Sequence

UTC = timezone.utc

REQUIRED_TRADES = 30
REQUIRED_CLEAN_RECON_DAYS = 7
MAX_AI_SPEND_USD = 50.0
MAX_LOSS_BREACH_DAYS = 90


@dataclass
class ReadinessItem:
    name: str
    passed: bool
    value: str
    requirement: str


@dataclass
class ReadinessReport:
    items: list[ReadinessItem]
    overall: bool
    checked_at: datetime

    @property
    def summary(self) -> str:
        passed = sum(1 for i in self.items if i.passed)
        return f"{passed}/{len(self.items)} criteria met"

    @property
    def blockers(self) -> list[str]:
        return [i.name for i in self.items if not i.passed]


def check_readiness(conn: sqlite3.Connection) -> ReadinessReport:
    now = datetime.now(UTC)
    items: list[ReadinessItem] = []

    # 1. Enough closed trades
    trade_count = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE closed_at IS NOT NULL"
    ).fetchone()[0]
    items.append(ReadinessItem(
        name="Enough trades",
        passed=trade_count >= REQUIRED_TRADES,
        value=str(trade_count),
        requirement=f">= {REQUIRED_TRADES}",
    ))

    # 2. Positive expectancy
    r_rows = conn.execute(
        "SELECT realised_r FROM positions WHERE closed_at IS NOT NULL AND realised_r IS NOT NULL"
    ).fetchall()
    rs = [r["realised_r"] for r in r_rows]
    expectancy = sum(rs) / len(rs) if rs else 0.0
    items.append(ReadinessItem(
        name="Positive expectancy",
        passed=expectancy > 0.0,
        value=f"{expectancy:.3f}R",
        requirement="> 0.0R",
    ))

    # 3. No loss-limit breaches in last 90 days
    cutoff = (now - timedelta(days=MAX_LOSS_BREACH_DAYS)).isoformat()
    breach_count = conn.execute(
        """SELECT COUNT(*) FROM reconciliation_log
           WHERE mismatch_type='LOSS_LIMIT_BREACH' AND recorded_at > ?""",
        (cutoff,),
    ).fetchone()[0]
    items.append(ReadinessItem(
        name="No recent loss-limit breaches",
        passed=breach_count == 0,
        value=f"{breach_count} breaches",
        requirement=f"0 in last {MAX_LOSS_BREACH_DAYS}d",
    ))

    # 4. Clean reconciliation (no RECON_MISMATCH in last 7 days)
    recon_cutoff = (now - timedelta(days=REQUIRED_CLEAN_RECON_DAYS)).isoformat()
    recon_issues = conn.execute(
        """SELECT COUNT(*) FROM reconciliation_log
           WHERE mismatch_type='RECON_MISMATCH' AND recorded_at > ?""",
        (recon_cutoff,),
    ).fetchone()[0]
    items.append(ReadinessItem(
        name="Clean reconciliation",
        passed=recon_issues == 0,
        value=f"{recon_issues} mismatches",
        requirement=f"0 in last {REQUIRED_CLEAN_RECON_DAYS}d",
    ))

    # 5. AI spend within budget
    spend_row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM ai_spend"
    ).fetchone()[0]
    ai_spend = float(spend_row)
    items.append(ReadinessItem(
        name="AI spend within budget",
        passed=ai_spend <= MAX_AI_SPEND_USD,
        value=f"${ai_spend:.2f}",
        requirement=f"<= ${MAX_AI_SPEND_USD:.0f}",
    ))

    # 6. At least one strategy version
    version_count = conn.execute(
        "SELECT COUNT(*) FROM strategy_versions"
    ).fetchone()[0]
    items.append(ReadinessItem(
        name="Strategy version exists",
        passed=version_count >= 1,
        value=str(version_count),
        requirement=">= 1",
    ))

    # 7. No pending proposals (all reviewed)
    pending_proposals = conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE decision IS NULL"
    ).fetchone()[0]
    items.append(ReadinessItem(
        name="All proposals reviewed",
        passed=pending_proposals == 0,
        value=f"{pending_proposals} pending",
        requirement="0 pending",
    ))

    overall = all(i.passed for i in items)
    return ReadinessReport(items=items, overall=overall, checked_at=now)
