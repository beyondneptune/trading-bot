"""R-multiple statistics for completed trades.

R = realised P&L / initial risk (1R = the amount you risked on entry).
Positive R > 1 means you made more than you risked.
Negative R means a loss.

These stats tell you:
  - Expectancy: average R per trade (positive = edge)
  - Win rate: how often you profit
  - Profit factor: sum of wins / sum of losses
  - Max consecutive losses (drawdown risk)

The null model (see null_model.py) tests whether your stats could be
explained by chance — i.e., would a coin-flip strategy do as well?
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Sequence


@dataclass
class RStats:
    n: int
    expectancy: float          # mean R per trade
    win_rate: float            # fraction of trades with R > 0
    profit_factor: float       # sum(winners) / |sum(losers)|
    avg_winner: float          # mean R of winning trades
    avg_loser: float           # mean R of losing trades (negative)
    max_consec_losses: int
    total_r: float

    @property
    def is_viable(self) -> bool:
        """Minimum criteria for a live-trading candidate system."""
        return (
            self.n >= 30
            and self.expectancy > 0.0
            and self.profit_factor > 1.5
            and self.win_rate > 0.35
        )


def compute_r_stats(r_multiples: Sequence[float]) -> RStats:
    """Compute R statistics from a list of R-multiples."""
    if not r_multiples:
        return RStats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)

    rs = list(r_multiples)
    n = len(rs)
    total = sum(rs)
    expectancy = total / n

    winners = [r for r in rs if r > 0]
    losers = [r for r in rs if r < 0]

    win_rate = len(winners) / n
    avg_winner = sum(winners) / len(winners) if winners else 0.0
    avg_loser = sum(losers) / len(losers) if losers else 0.0

    sum_winners = sum(winners)
    sum_losers = abs(sum(losers)) if losers else 0.0
    profit_factor = sum_winners / sum_losers if sum_losers > 0 else float("inf")

    # Max consecutive losses
    max_consec = 0
    current = 0
    for r in rs:
        if r < 0:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0

    return RStats(
        n=n,
        expectancy=expectancy,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_winner=avg_winner,
        avg_loser=avg_loser,
        max_consec_losses=max_consec,
        total_r=total,
    )


def load_r_multiples(
    conn: sqlite3.Connection,
    setup_name: str | None = None,
    min_trades: int = 0,
) -> list[float]:
    """Load realised R-multiples from closed positions."""
    if setup_name:
        rows = conn.execute(
            """SELECT p.realised_r
               FROM positions p
               JOIN signals s ON p.open_signal_id = s.id
               WHERE p.closed_at IS NOT NULL
               AND p.realised_r IS NOT NULL
               AND s.signal_type = ?
               ORDER BY p.closed_at""",
            (setup_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT realised_r FROM positions
               WHERE closed_at IS NOT NULL AND realised_r IS NOT NULL
               ORDER BY closed_at""",
        ).fetchall()

    return [r["realised_r"] for r in rows]


def r_stats_from_db(
    conn: sqlite3.Connection,
    setup_name: str | None = None,
) -> RStats:
    rs = load_r_multiples(conn, setup_name)
    return compute_r_stats(rs)
