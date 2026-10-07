"""AI spend tracking and monthly cap enforcement — CLAUDE.md rule 17.

Costs are stored in the ai_spend table.  When the monthly cap is reached,
all API calls are blocked and the user is alerted.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

UTC = timezone.utc

# Anthropic pricing (as of late 2025 — update if pricing changes).
# Expressed in USD per million tokens.
PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.8, "output": 4.0},
    "claude-opus-4-8": {"input": 15.0, "output": 75.0},
    "default": {"input": 3.0, "output": 15.0},
}
USD_TO_GBP = 0.79  # approximate; update periodically


def tokens_to_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = PRICING.get(model, PRICING["default"])
    return (
        input_tokens / 1_000_000 * pricing["input"]
        + output_tokens / 1_000_000 * pricing["output"]
    )


def record_spend(
    conn: sqlite3.Connection,
    model: str,
    input_tokens: int,
    output_tokens: int,
    report_id: int | None = None,
) -> float:
    """Record spend and return cost in USD."""
    cost = tokens_to_usd(model, input_tokens, output_tokens)
    conn.execute(
        """INSERT INTO ai_spend (report_id, model, input_tokens, output_tokens, cost_usd, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (report_id, model, input_tokens, output_tokens, cost,
         datetime.now(UTC).isoformat()),
    )
    conn.commit()
    return cost


def monthly_spend_usd(conn: sqlite3.Connection) -> float:
    """Return total AI spend this calendar month in USD."""
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute(
        "SELECT SUM(cost_usd) FROM ai_spend WHERE recorded_at >= ?",
        (month_start.isoformat(),),
    ).fetchone()
    return float(row[0] or 0.0)


def check_budget(conn: sqlite3.Connection) -> tuple[bool, float]:
    """Return (budget_ok, spend_usd).  False means the cap is reached."""
    from config.settings import load_risk_limits
    limits = load_risk_limits()
    cap_gbp = limits.monthly_ai_budget_gbp
    cap_usd = cap_gbp / USD_TO_GBP

    spend = monthly_spend_usd(conn)
    ok = spend < cap_usd
    if not ok:
        logger.warning(
            "Monthly AI budget reached: $%.2f / $%.2f (£%.0f cap)",
            spend, cap_usd, cap_gbp,
        )
    return ok, spend
