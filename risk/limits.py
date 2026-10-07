"""Account-level loss limits and circuit breakers — CLAUDE.md rule 9.

Rule 9:
  - Daily loss limit 2%: no new buys for the rest of the day.
  - Circuit breakers:
      - equity down 5% from its weekly peak → no new buys until I reset it
      - equity down 10% from its all-time peak → no new buys until I reset it

Circuit breakers are stored in the database (equity_snapshots) and reset
only by explicit user action (on dashboard or via Telegram /resume).

Kill switch (rule 10): separate module (execution/kill.py).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from config.settings import load_risk_limits

logger = logging.getLogger(__name__)

UTC = timezone.utc


@dataclass
class LimitStatus:
    daily_loss_active: bool
    weekly_circuit_active: bool
    peak_circuit_active: bool
    new_buys_allowed: bool
    reason: str


def _equity_at_start_of_day(
    conn: sqlite3.Connection, today: date
) -> float | None:
    """Return equity at the first snapshot of today, or None."""
    row = conn.execute(
        """SELECT equity_gbp FROM equity_snapshots
           WHERE date(snapped_at) = ? ORDER BY snapped_at ASC LIMIT 1""",
        (str(today),),
    ).fetchone()
    return float(row[0]) if row else None


def _equity_weekly_peak(
    conn: sqlite3.Connection, today: date
) -> float | None:
    """Return highest equity in the trailing 7 days."""
    since = today - timedelta(days=7)
    row = conn.execute(
        """SELECT MAX(equity_gbp) FROM equity_snapshots
           WHERE date(snapped_at) >= ?""",
        (str(since),),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def _equity_all_time_peak(conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        "SELECT MAX(equity_gbp) FROM equity_snapshots"
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def check_loss_limits(
    current_equity_gbp: float,
    conn: sqlite3.Connection,
    today: date | None = None,
) -> LimitStatus:
    """Check whether any loss limit is active.

    Always uses the live equity so the check reflects real-time moves.
    """
    if today is None:
        today = datetime.now(UTC).date()

    limits = load_risk_limits()

    # Daily loss check
    day_start = _equity_at_start_of_day(conn, today)
    daily_loss_active = False
    if day_start and day_start > 0:
        daily_loss_pct = (day_start - current_equity_gbp) / day_start
        if daily_loss_pct >= limits.daily_loss_limit:
            daily_loss_active = True
            logger.warning(
                "Daily loss limit reached: %.1f%% loss today (limit %.0f%%)",
                daily_loss_pct * 100, limits.daily_loss_limit * 100,
            )

    # Weekly circuit breaker
    weekly_peak = _equity_weekly_peak(conn, today)
    weekly_circuit = False
    if weekly_peak and weekly_peak > 0:
        weekly_dd = (weekly_peak - current_equity_gbp) / weekly_peak
        if weekly_dd >= limits.weekly_drawdown_circuit:
            weekly_circuit = True
            logger.warning(
                "Weekly circuit breaker: %.1f%% drawdown from weekly peak",
                weekly_dd * 100,
            )

    # All-time peak circuit breaker
    peak = _equity_all_time_peak(conn)
    peak_circuit = False
    if peak and peak > 0:
        peak_dd = (peak - current_equity_gbp) / peak
        if peak_dd >= limits.peak_drawdown_circuit:
            peak_circuit = True
            logger.warning(
                "Peak circuit breaker: %.1f%% drawdown from all-time peak",
                peak_dd * 100,
            )

    new_buys = not (daily_loss_active or weekly_circuit or peak_circuit)

    if not new_buys:
        parts = []
        if daily_loss_active:
            parts.append("daily loss limit reached")
        if weekly_circuit:
            parts.append("weekly circuit breaker active")
        if peak_circuit:
            parts.append("peak circuit breaker active")
        reason = "No new buys: " + "; ".join(parts) + "."
    else:
        reason = "All loss limits within bounds."

    return LimitStatus(
        daily_loss_active=daily_loss_active,
        weekly_circuit_active=weekly_circuit,
        peak_circuit_active=peak_circuit,
        new_buys_allowed=new_buys,
        reason=reason,
    )


def record_equity_snapshot(
    conn: sqlite3.Connection,
    equity_gbp: float,
    cash_gbp: float,
    open_positions: int,
    portfolio_heat: float,
) -> None:
    conn.execute(
        """INSERT INTO equity_snapshots
               (equity_gbp, cash_gbp, open_positions, portfolio_heat, snapped_at)
           VALUES (?, ?, ?, ?, ?)""",
        (equity_gbp, cash_gbp, open_positions, portfolio_heat,
         datetime.now(UTC).isoformat()),
    )
    conn.commit()
