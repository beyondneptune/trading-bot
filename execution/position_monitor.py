"""One monitoring pass over open positions.

For every open position: fetch the current quote and run the stop/target
check (Phase 6), which auto-closes the position if a level is hit. Also
records a heartbeat and, on request, runs reconciliation.

This is the per-tick work the orchestrator loop (run_bot.py) calls on a
timer. Kept as a single synchronous-ish pass so it is easy to test.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from execution.heartbeat import record_heartbeat
from execution.reconciliation import run_reconciliation
from execution.stops import StopCheckResult, check_stops

logger = logging.getLogger(__name__)


@dataclass
class MonitorPass:
    checked: int = 0
    closed: int = 0
    results: list[StopCheckResult] = field(default_factory=list)
    reconciliation_mismatches: int = 0


def _open_symbols(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT DISTINCT symbol, exchange FROM positions WHERE closed_at IS NULL"
    ).fetchall()
    return [(r["symbol"], r["exchange"]) for r in rows]


def monitor_positions(
    conn: sqlite3.Connection,
    feed_manager: Any,
    *,
    fx_usd_gbp: float = 0.79,
    reconcile: bool = False,
) -> MonitorPass:
    """Run one monitoring pass. Returns a summary of what happened.

    Args:
        feed_manager: anything exposing get_quote(symbol, exchange) -> Quote|None.
        reconcile: if True, also run reconciliation this pass.
    """
    record_heartbeat(conn)
    result = MonitorPass()

    for symbol, exchange in _open_symbols(conn):
        try:
            feed_manager.subscribe(symbol, exchange)
            quote = feed_manager.get_quote(symbol, exchange)
        except Exception as e:
            logger.error("Monitor: quote error for %s/%s: %s", symbol, exchange, e)
            continue
        if quote is None:
            continue

        checks = check_stops(conn, symbol, quote.mid, fx_usd_gbp=fx_usd_gbp)
        result.checked += 1
        for c in checks:
            result.results.append(c)
            if c.action != "HOLD":
                result.closed += 1
                logger.info(
                    "Monitor: %s %s @ %.4g (R=%.2f)",
                    symbol, c.action, c.close_price or 0.0, c.realised_r or 0.0,
                )

    if reconcile:
        report = run_reconciliation(conn)
        result.reconciliation_mismatches = report.mismatches
        if report.mismatches:
            logger.warning("Monitor: reconciliation found %d mismatch(es)",
                           report.mismatches)

    return result
