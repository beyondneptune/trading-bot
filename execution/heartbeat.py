"""Heartbeat watchdog for the execution system.

The heartbeat writes a timestamp to the DB every N seconds.
If the heartbeat timestamp goes stale (> 2× interval), an alert is raised.

This detects:
  - Process crash
  - Event loop freeze
  - Loss of IB connection (in live mode)

In mock mode, the heartbeat is simulated and always healthy.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

UTC = timezone.utc
logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30
STALE_THRESHOLD_SECONDS = HEARTBEAT_INTERVAL_SECONDS * 2


def record_heartbeat(conn: sqlite3.Connection) -> None:
    """Write a heartbeat row (upsert on the single HEARTBEAT row)."""
    conn.execute(
        """INSERT OR REPLACE INTO system_events
           (id, event_type, note, logged_at)
           VALUES (
             (SELECT id FROM system_events WHERE event_type='HEARTBEAT' LIMIT 1),
             'HEARTBEAT', 'alive', ?
           )""",
        (datetime.now(UTC).isoformat(),),
    )
    conn.commit()


def is_heartbeat_healthy(conn: sqlite3.Connection) -> bool:
    """True if last heartbeat was within STALE_THRESHOLD_SECONDS."""
    row = conn.execute(
        "SELECT logged_at FROM system_events "
        "WHERE event_type='HEARTBEAT' ORDER BY logged_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return False
    try:
        last = datetime.fromisoformat(row["logged_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - last).total_seconds()
        return age < STALE_THRESHOLD_SECONDS
    except (ValueError, TypeError):
        return False


async def run_heartbeat(conn: sqlite3.Connection) -> None:
    """Async heartbeat loop. Runs until cancelled."""
    logger.info("Heartbeat started (interval=%ds)", HEARTBEAT_INTERVAL_SECONDS)
    while True:
        try:
            record_heartbeat(conn)
        except Exception as e:
            logger.error("Heartbeat write failed: %s", e)
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break
