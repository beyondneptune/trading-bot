"""Stop management: monitors open positions and closes them when stop is hit.

In mock mode: checks current price against stop/target and records the close.
In live mode (Phase 6): would submit market orders via IB.

The stop manager is called by the monitor scan loop and the heartbeat.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

UTC = timezone.utc
logger = logging.getLogger(__name__)


@dataclass
class StopCheckResult:
    position_id: int
    symbol: str
    action: str       # "STOP_HIT" | "TARGET_HIT" | "HOLD"
    close_price: Optional[float] = None
    realised_pnl_gbp: Optional[float] = None
    realised_r: Optional[float] = None


def check_stops(
    conn: sqlite3.Connection,
    symbol: str,
    current_price: float,
    fx_usd_gbp: float = 0.79,
) -> list[StopCheckResult]:
    """Check all open positions for this symbol against stop/target.
    Returns a list of results (may be empty)."""
    positions = conn.execute(
        """SELECT id, quantity, avg_cost, currency, stop_price, target_price
           FROM positions
           WHERE symbol=? AND closed_at IS NULL""",
        (symbol,),
    ).fetchall()

    results = []
    for pos in positions:
        result = _evaluate_position(pos, current_price, fx_usd_gbp)
        result.symbol = symbol
        if result.action != "HOLD":
            _close_position(conn, pos, result)
        results.append(result)
    return results


def _evaluate_position(pos, current_price: float, fx_usd_gbp: float) -> StopCheckResult:
    qty = pos["quantity"]
    avg_cost = pos["avg_cost"]
    stop = pos["stop_price"]
    target = pos["target_price"]
    is_long = qty > 0

    action = "HOLD"
    close_price = None

    if stop is not None:
        if is_long and current_price <= stop:
            action = "STOP_HIT"
            close_price = stop
        elif not is_long and current_price >= stop:
            action = "STOP_HIT"
            close_price = stop

    if target is not None and action == "HOLD":
        if is_long and current_price >= target:
            action = "TARGET_HIT"
            close_price = target
        elif not is_long and current_price <= target:
            action = "TARGET_HIT"
            close_price = target

    realised_pnl_gbp = None
    realised_r = None

    if close_price is not None:
        price_diff = (close_price - avg_cost) * (1 if is_long else -1)
        realised_pnl_gbp = price_diff * abs(qty)
        # Normalise to GBX if needed (simple check: large price → pence)
        if avg_cost > 500 and pos["currency"] == "GBP":
            realised_pnl_gbp /= 100.0
        if stop and avg_cost:
            risk_per_share = abs(avg_cost - stop)
            if risk_per_share > 0:
                r_multiple = price_diff / risk_per_share
                realised_r = r_multiple

    return StopCheckResult(
        position_id=pos["id"],
        symbol="",  # set by caller
        action=action,
        close_price=close_price,
        realised_pnl_gbp=realised_pnl_gbp,
        realised_r=realised_r,
    )


def _close_position(conn: sqlite3.Connection, pos, result: StopCheckResult) -> None:
    conn.execute(
        """UPDATE positions
           SET closed_at=?, close_price=?, realised_pnl_gbp=?, realised_r=?
           WHERE id=?""",
        (
            datetime.now(UTC).isoformat(),
            result.close_price,
            result.realised_pnl_gbp,
            result.realised_r,
            pos["id"],
        ),
    )
    conn.commit()
    logger.info(
        "Position %d closed: %s @ %.4g, R=%.2f",
        pos["id"], result.action, result.close_price or 0,
        result.realised_r or 0,
    )
