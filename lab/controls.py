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
    """Cancel open ENTRY orders only; protective stops are left in place
    (CLAUDE.md rule 9). Returns how many entries were cancelled."""
    n = broker.cancel_entry_orders()
    logger.warning("KILL SWITCH: cancelled %d entry order(s); stops left in place.", n)
    return n


def close_position(broker: Broker, symbol: str) -> bool:
    """Close a long position the SAFE way (rule 6): cancel its protective stop
    FIRST, then sell at market — so shares can never be sold twice. Returns
    True if a position was sold."""
    for o in broker.get_orders(open_only=True):
        if o.symbol == symbol and o.is_protective_stop:
            broker.cancel_order(o.id)
            logger.info("Cancelled protective stop %s for %s before selling.", o.id, symbol)
    pos = next((p for p in broker.get_positions() if p.symbol == symbol), None)
    if pos is None or pos.qty == 0:
        return False
    broker.submit_order(symbol=symbol, qty=abs(pos.qty), side="sell",
                        order_type="market", tif="day")
    logger.info("Sold %s %s.", abs(pos.qty), symbol)
    return True


def cancel_unfilled_entries(broker: Broker) -> int:
    """Close-of-day task: cancel any still-open GTC entry (buy) orders, so a
    stale entry can't fill overnight. Same as the kill switch's entry cancel."""
    return broker.cancel_entry_orders()


def ensure_stops(broker: Broker, conn: sqlite3.Connection,
                 stop_pct: float = 0.03) -> list:
    """Daily check (rule 6): every long position must have a live protective
    stop. GTC stops expire after ~90 days, so if one is missing, re-place it
    (GTC) and ALERT. Returns the symbols whose stops were re-placed."""
    import datetime
    stopped = {o.symbol for o in broker.get_orders(open_only=True)
               if o.is_protective_stop}
    replaced = []
    for p in broker.get_positions():
        if p.qty > 0 and p.symbol not in stopped:
            stop_price = round(p.avg_entry_price * (1 - stop_pct), 2)
            broker.submit_order(symbol=p.symbol, qty=abs(p.qty), side="sell",
                                order_type="stop", stop_price=stop_price, tif="gtc")
            replaced.append(p.symbol)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO system_events(event_type, note, logged_at) VALUES (?,?,?)",
                ("ALERT", f"Re-placed missing protective stop for {p.symbol} @ {stop_price}", now),
            )
            logger.warning("ALERT: re-placed missing stop for %s @ %s", p.symbol, stop_price)
    if replaced:
        conn.commit()
    return replaced


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
    """Compare the broker's positions (source of truth) with what the database
    learned from the trade-update stream: net filled qty per symbol (buys minus
    sells, using each order's cumulative filled_qty)."""
    broker_pos = {p.symbol: p.qty for p in broker.get_positions()}
    rows = conn.execute(
        """SELECT symbol, side, MAX(COALESCE(filled_qty, 0)) AS filled
           FROM trade_updates
           WHERE order_id IS NOT NULL AND symbol IS NOT NULL
           GROUP BY order_id, symbol, side"""
    ).fetchall()
    db_pos: dict = {}
    for r in rows:
        signed = float(r["filled"]) * (1.0 if r["side"] == "buy" else -1.0)
        db_pos[r["symbol"]] = db_pos.get(r["symbol"], 0.0) + signed
    db_pos = {s: q for s, q in db_pos.items() if abs(q) > 1e-9}
    symbols = set(broker_pos) | set(db_pos)
    mismatches = [
        {"symbol": s, "broker": broker_pos.get(s, 0.0), "db": db_pos.get(s, 0.0)}
        for s in sorted(symbols)
        if abs(broker_pos.get(s, 0.0) - db_pos.get(s, 0.0)) > 1e-6
    ]
    if mismatches:
        logger.warning("RECONCILE: %d mismatch(es) vs broker.", len(mismatches))
    return ReconResult(broker_pos, db_pos, mismatches)
