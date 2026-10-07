"""Pre-trade gates — rules 7 and 12.

Rule 7 — Cost gate:
  Skip any trade whose estimated round-trip cost > 0.15R, or whose
  planned reward-to-risk is below 2:1.

Rule 12 — Data quality gate:
  No trade on stale, delayed, missing, or implausible prices.
  Delayed data disables intraday alerts for that instrument.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.settings import load_risk_limits
from data.models import DataQuality, Quote
from risk.costs import CostBreakdown

UTC = timezone.utc
STALE_THRESHOLD = timedelta(seconds=30)


@dataclass
class GateResult:
    passed: bool
    reason: str


def cost_gate(
    cost: CostBreakdown,
    entry_price: float,
    stop_price: float,
    target_price: float,
) -> GateResult:
    """Rule 7: reject if cost > 0.15R or R:R < 2:1."""
    limits = load_risk_limits()
    max_cost_r = limits.max_cost_per_r
    min_rr = limits.min_reward_risk

    if cost.cost_as_r > max_cost_r:
        return GateResult(
            passed=False,
            reason=(
                f"Cost gate: estimated cost {cost.cost_as_r:.3f}R exceeds "
                f"maximum {max_cost_r}R."
            ),
        )

    risk = entry_price - stop_price
    reward = target_price - entry_price
    if risk <= 0:
        return GateResult(passed=False, reason="Stop must be below entry.")
    rr = reward / risk
    # Rule 8 is "R:R >= 2:1"; use a small tolerance so a target built for
    # exactly 2.0 isn't rejected by floating-point error (e.g. 1.9999999).
    if rr < min_rr - 1e-9:
        return GateResult(
            passed=False,
            reason=(
                f"Cost gate: reward:risk {rr:.2f}:1 is below the minimum {min_rr:.0f}:1."
            ),
        )

    return GateResult(passed=True, reason="Cost gate passed.")


def data_quality_gate(
    quote: Optional[Quote],
    symbol: str,
    exchange: str,
    is_intraday: bool = True,
) -> GateResult:
    """Rule 12: reject if data is stale, delayed, or missing.

    Delayed data disables intraday alerts; end-of-day ideas can still use it.
    """
    if quote is None:
        return GateResult(
            passed=False,
            reason=f"Data quality gate: no quote available for {symbol}/{exchange}.",
        )

    now = datetime.now(UTC)
    ts = quote.ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    if (now - ts) > STALE_THRESHOLD:
        return GateResult(
            passed=False,
            reason=(
                f"Data quality gate: quote for {symbol} is stale "
                f"({(now - ts).seconds}s old, limit {STALE_THRESHOLD.seconds}s)."
            ),
        )

    if is_intraday and quote.quality == DataQuality.DELAYED:
        return GateResult(
            passed=False,
            reason=(
                f"Data quality gate: {symbol} has delayed data — "
                "intraday alerts disabled (rule 12)."
            ),
        )

    if is_intraday and quote.quality == DataQuality.MOCK:
        return GateResult(
            passed=False,
            reason=f"Data quality gate: {symbol} is on mock data — intraday disabled.",
        )

    # Plausibility: price must be positive and non-zero
    if quote.last <= 0 or quote.bid <= 0 or quote.ask <= 0:
        return GateResult(
            passed=False,
            reason=f"Data quality gate: implausible price for {symbol} (≤0).",
        )

    # Ask must be ≥ bid
    if quote.ask < quote.bid:
        return GateResult(
            passed=False,
            reason=f"Data quality gate: ask < bid for {symbol}.",
        )

    return GateResult(passed=True, reason="Data quality gate passed.")
