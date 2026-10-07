"""Lab safety controls (CLAUDE.md rules 9, 10) — all go through the Broker
interface, so they work against the real Alpaca paper broker or the FakeBroker.

  - kill_switch    : cancel all open (entry) orders and pause. Protective stops
                     at the broker are left in place by cancelling only
                     non-stop orders. (Full entry-vs-stop tagging arrives with
                     slices in L5; here we cancel open orders and report.)
  - hard_stop      : set/clear Alpaca's suspend_trade so NO new orders anywhere.
  - close_everything: liquidate all positions (caller must confirm first).
  - reconcile      : the broker is the source of truth; compare its positions
                     with the database and report mismatches.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from broker.base import Broker

logger = logging.getLogger(__name__)


def check_options_level(conn: sqlite3.Connection, broker: Broker) -> tuple:
    """Record the broker's options level and ALERT if it ever changes.

    Returns (current_level, changed: bool). On a change, logs a warning and
    writes an ALERT to system_events so the operator is notified.
    """
    import datetime
    level = broker.get_account().options_level
    row = conn.execute(
        "SELECT value FROM db_meta WHERE key='alpaca_options_level'"
    ).fetchone()
    previous = row["value"] if row else None
    changed = previous is not None and str(previous) != str(level)
    conn.execute(
        "INSERT INTO db_meta(key, value) VALUES ('alpaca_options_level', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(level),),
    )
    if changed:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO system_events(event_type, note, logged_at) VALUES (?,?,?)",
            ("ALERT", f"Alpaca options level changed: {previous} -> {level}", now),
        )
        logger.warning("ALERT: Alpaca options level changed %s -> %s", previous, level)
    conn.commit()
    return level, changed


def kill_switch(broker: Broker) -> int:
    """Cancel all open orders and return how many were cancelled."""
    n = broker.cancel_all_orders()
    logger.warning("KILL SWITCH: cancelled %d open order(s).", n)
    return n


def hard_stop(broker: Broker, on: bool = True) -> None:
    """Hard stop: suspend (or resume) ALL trading at the broker."""
    broker.set_trade_suspended(on)
    logger.warning("HARD STOP %s.", "ENGAGED" if on else "lifted")


def close_everything(broker: Broker, confirm: bool = False) -> int:
    """Liquidate every position. Requires explicit confirm=True."""
    if not confirm:
        raise ValueError("close_everything requires confirm=True.")
    n = broker.close_all_positions(cancel_orders=True)
    logger.warning("CLOSE EVERYTHING: closed %d position(s).", n)
    return n


@dataclass
class ReconResult:
    broker_positions: dict
    db_positions: dict
    mismatches: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def reconcile(broker: Broker, conn: sqlite3.Connection) -> ReconResult:
    """Compare the broker's positions (source of truth) with the database."""
    broker_pos = {p.symbol: p.qty for p in broker.get_positions()}
    rows = conn.execute(
        "SELECT symbol, COALESCE(SUM(quantity),0) q FROM positions "
        "WHERE closed_at IS NULL GROUP BY symbol"
    ).fetchall()
    db_pos = {r["symbol"]: float(r["q"]) for r in rows}
    symbols = set(broker_pos) | set(db_pos)
    mismatches = [
        {"symbol": s, "broker": broker_pos.get(s, 0.0), "db": db_pos.get(s, 0.0)}
        for s in sorted(symbols)
        if abs(broker_pos.get(s, 0.0) - db_pos.get(s, 0.0)) > 1e-6
    ]
    if mismatches:
        logger.warning("RECONCILE: %d mismatch(es) vs broker.", len(mismatches))
    return ReconResult(broker_pos, db_pos, mismatches)
