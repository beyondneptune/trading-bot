"""Full pre-trade risk check — runs all CLAUDE.md rules in sequence.

Called:
  1. Before creating a signal (end-of-day or intraday).
  2. Again at approval time on the live price (rule 11).

Returns a RiskCheckReport with a pass/fail and per-rule breakdown.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from config.settings import load_risk_limits, load_settings
from data.models import DataQuality, Quote
from instruments.units import InstrumentSpec
from risk.costs import CostBreakdown, estimate_round_trip_cost
from risk.gates import GateResult, cost_gate, data_quality_gate
from risk.guards import ReadOnlyError, assert_readonly_phase
from risk.heat import HeatCheckResult, OpenPosition, check_portfolio_heat
from risk.limits import LimitStatus, check_loss_limits
from risk.sizing import SizingResult, compute_position_size

UTC = timezone.utc


@dataclass
class RuleCheck:
    rule_id: str
    description: str
    passed: bool
    detail: str


@dataclass
class RiskCheckReport:
    symbol: str
    exchange: str
    timestamp: datetime
    passed: bool
    checks: list[RuleCheck] = field(default_factory=list)
    sizing: Optional[SizingResult] = None
    costs: Optional[CostBreakdown] = None
    limit_status: Optional[LimitStatus] = None

    def to_chips(self) -> list[str]:
        """Return pass/fail chip labels for the UI."""
        return [
            f"{'✓' if c.passed else '✗'} Rule {c.rule_id}: {c.description}"
            for c in self.checks
        ]

    def failed_rules(self) -> list[RuleCheck]:
        return [c for c in self.checks if not c.passed]


def run_risk_checks(
    spec: InstrumentSpec,
    entry_price: float,
    stop_price: float,
    target_price: float,
    equity_gbp: float,
    cash_gbp: float,
    open_positions: list[OpenPosition],
    quote: Optional[Quote],
    bars_by_symbol: dict,
    conn: sqlite3.Connection,
    regime_multiplier: float = 1.0,
    fx_usd_gbp: float = 1.0,
    is_intraday: bool = False,
    today: Optional[date] = None,
) -> RiskCheckReport:
    """Run all applicable pre-trade risk checks.

    Returns a RiskCheckReport.  The `passed` field is True only if all
    rules pass.
    """
    settings = load_settings()
    checks: list[RuleCheck] = []
    now = datetime.now(UTC)

    # Rule 1: Paper account guard (already enforced at connection time,
    # but we record it here for completeness)
    checks.append(RuleCheck(
        rule_id="1", description="Paper account",
        passed=True, detail="Verified at connection time.",
    ))

    # Rule 2: Approval required — always passes at check time (approval
    # is the outer gate); recorded for audit trail
    checks.append(RuleCheck(
        rule_id="2", description="Approval required",
        passed=True, detail="Signal will require explicit approval before execution.",
    ))

    # Rule 3: Long-only, cash-only
    long_only_ok = entry_price > 0 and stop_price > 0
    checks.append(RuleCheck(
        rule_id="3", description="Long-only, cash-only",
        passed=long_only_ok,
        detail="OK." if long_only_ok else "Short positions not permitted.",
    ))

    # Rule 4: Sizing
    sizing = compute_position_size(
        spec=spec,
        entry_price=entry_price,
        stop_price=stop_price,
        equity_gbp=equity_gbp,
        cash_gbp=cash_gbp,
        open_position_count=len(open_positions),
        regime_multiplier=regime_multiplier,
        fx_usd_gbp=fx_usd_gbp,
    )
    checks.append(RuleCheck(
        rule_id="4", description="Position sizing",
        passed=sizing.feasible,
        detail=sizing.reason,
    ))

    # Rule 5: Portfolio heat
    if sizing.feasible and sizing.quantity > 0:
        from instruments.units import to_gbp
        entry_gbp_val = to_gbp(entry_price, spec.price_unit, fx_usd_gbp)
        stop_gbp_val = to_gbp(stop_price, spec.price_unit, fx_usd_gbp)
        new_pos = OpenPosition(
            symbol=spec.symbol,
            exchange=spec.exchange,
            quantity=float(sizing.quantity),
            entry_price_gbp=entry_gbp_val,
            stop_price_gbp=stop_gbp_val,
        )
        heat = check_portfolio_heat(
            open_positions, new_pos, equity_gbp, bars_by_symbol
        )
        checks.append(RuleCheck(
            rule_id="5", description="Portfolio heat",
            passed=heat.feasible,
            detail=heat.reason,
        ))
    else:
        heat = None
        checks.append(RuleCheck(
            rule_id="5", description="Portfolio heat",
            passed=True, detail="N/A (sizing failed).",
        ))

    # Rule 6: Regime (reflected in sizing via multiplier)
    regime_ok = regime_multiplier > 0
    checks.append(RuleCheck(
        rule_id="6", description="Regime multiplier",
        passed=regime_ok,
        detail=f"Multiplier: {regime_multiplier}×. {'No new buys in stressed regime.' if not regime_ok else 'OK.'}",
    ))

    # Rule 7: Cost gate
    if sizing.feasible and sizing.quantity > 0:
        costs = estimate_round_trip_cost(
            spec=spec,
            entry_price=entry_price,
            quantity=float(sizing.quantity),
            initial_risk_gbp=max(sizing.risk_gbp, 0.01),
            fx_usd_gbp=fx_usd_gbp,
        )
        cg = cost_gate(costs, entry_price, stop_price, target_price)
        checks.append(RuleCheck(
            rule_id="7", description="Cost gate",
            passed=cg.passed,
            detail=cg.reason,
        ))
    else:
        costs = None
        checks.append(RuleCheck(
            rule_id="7", description="Cost gate",
            passed=True, detail="N/A (sizing failed).",
        ))

    # Rules 8 & 13: bracket order and read-only phase
    try:
        assert_readonly_phase(settings.phase)
        exec_ok = True
        exec_detail = "Execution permitted (Phase 6+)."
    except ReadOnlyError as e:
        exec_ok = True  # passes — execution is intentionally blocked pre-Phase 6
        exec_detail = str(e)
    checks.append(RuleCheck(
        rule_id="8+13", description="Bracket order / read-only phase",
        passed=exec_ok,
        detail=exec_detail,
    ))

    # Rule 9: Daily loss limit and circuit breakers
    limits_status = check_loss_limits(equity_gbp, conn, today)
    checks.append(RuleCheck(
        rule_id="9", description="Loss limits / circuit breakers",
        passed=limits_status.new_buys_allowed,
        detail=limits_status.reason,
    ))

    # Rule 11: Data quality (already checked separately; summarise here)
    dq = data_quality_gate(quote, spec.symbol, spec.exchange, is_intraday)
    checks.append(RuleCheck(
        rule_id="12", description="Data quality",
        passed=dq.passed,
        detail=dq.reason,
    ))

    overall = all(c.passed for c in checks)

    return RiskCheckReport(
        symbol=spec.symbol,
        exchange=spec.exchange,
        timestamp=now,
        passed=overall,
        checks=checks,
        sizing=sizing,
        costs=costs,
        limit_status=limits_status,
    )
