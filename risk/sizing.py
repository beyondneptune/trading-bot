"""Position sizing — CLAUDE.md rule 4.

Sizing is always computed by code, never by the AI.

Rules (all applied in order; the binding constraint is reported):
  1. 1% equity risk limit: size = (equity × 0.01) / (entry - stop) × regime_mult
  2. 15% position cap: position value ≤ 15% of equity
  3. 8 open positions maximum
  4. Cash minimum: cash after entry ≥ 10% of equity

The alert card must always state which rule limited the size.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from config.settings import load_risk_limits
from instruments.units import InstrumentSpec, PriceUnit, to_gbp


class SizingConstraint(str, Enum):
    RISK_RULE = "1% risk rule"
    POSITION_CAP = "15% position cap"
    CASH_MIN = "10% cash minimum"
    MAX_POSITIONS = "8-position limit"
    REGIME = "regime multiplier"


@dataclass
class SizingResult:
    quantity: int
    position_value_gbp: float
    risk_gbp: float
    risk_pct: float                 # risk_gbp / equity
    position_pct: float             # position_value_gbp / equity
    binding_constraint: SizingConstraint
    regime_multiplier: float
    feasible: bool                  # False if quantity == 0
    reason: str


def compute_position_size(
    spec: InstrumentSpec,
    entry_price: float,
    stop_price: float,
    equity_gbp: float,
    cash_gbp: float,
    open_position_count: int,
    regime_multiplier: float = 1.0,
    fx_usd_gbp: float = 1.0,
) -> SizingResult:
    """Compute the position size subject to all Rule 4 constraints.

    Args:
        spec: Instrument spec (for price unit normalisation).
        entry_price: Intended entry price in native unit.
        stop_price: Stop-loss price in native unit.
        equity_gbp: Total account equity in GBP.
        cash_gbp: Available cash (settled, not unsettled) in GBP.
        open_position_count: Current number of open positions.
        regime_multiplier: 0.0, 0.5, or 1.0 from the regime check.
        fx_usd_gbp: USD→GBP rate (only used for USD instruments).
    """
    limits = load_risk_limits()

    entry_gbp = to_gbp(entry_price, spec.price_unit, fx_usd_gbp)
    stop_gbp = to_gbp(stop_price, spec.price_unit, fx_usd_gbp)
    risk_per_share_gbp = entry_gbp - stop_gbp

    # Safety checks
    if open_position_count >= limits.max_positions:
        return SizingResult(
            quantity=0,
            position_value_gbp=0.0,
            risk_gbp=0.0,
            risk_pct=0.0,
            position_pct=0.0,
            binding_constraint=SizingConstraint.MAX_POSITIONS,
            regime_multiplier=regime_multiplier,
            feasible=False,
            reason=f"Already at maximum {limits.max_positions} open positions.",
        )

    if regime_multiplier == 0.0:
        return SizingResult(
            quantity=0,
            position_value_gbp=0.0,
            risk_gbp=0.0,
            risk_pct=0.0,
            position_pct=0.0,
            binding_constraint=SizingConstraint.REGIME,
            regime_multiplier=0.0,
            feasible=False,
            reason="Stressed regime: no new buys.",
        )

    if risk_per_share_gbp <= 0:
        return SizingResult(
            quantity=0,
            position_value_gbp=0.0,
            risk_gbp=0.0,
            risk_pct=0.0,
            position_pct=0.0,
            binding_constraint=SizingConstraint.RISK_RULE,
            regime_multiplier=regime_multiplier,
            feasible=False,
            reason=f"Stop ({stop_gbp:.4f} GBP) must be below entry ({entry_gbp:.4f} GBP).",
        )

    # 1. Risk-based quantity
    max_risk_gbp = equity_gbp * limits.max_risk_per_trade * regime_multiplier
    qty_by_risk = int(max_risk_gbp / risk_per_share_gbp)
    binding = SizingConstraint.RISK_RULE

    # 2. Apply 15% position cap
    max_value_gbp = equity_gbp * limits.max_position_size
    qty_by_cap = int(max_value_gbp / entry_gbp)
    if qty_by_cap < qty_by_risk:
        qty_by_risk = qty_by_cap
        binding = SizingConstraint.POSITION_CAP

    # 3. Cash minimum: must keep ≥ 10% of equity in cash after purchase
    min_cash_after = equity_gbp * limits.min_cash_ratio
    available_cash = cash_gbp - min_cash_after
    qty_by_cash = int(available_cash / entry_gbp) if available_cash > 0 else 0
    if qty_by_cash < qty_by_risk:
        qty_by_risk = qty_by_cash
        binding = SizingConstraint.CASH_MIN

    quantity = max(0, qty_by_risk)
    position_value = quantity * entry_gbp
    risk_gbp = quantity * risk_per_share_gbp

    if quantity == 0:
        return SizingResult(
            quantity=0,
            position_value_gbp=0.0,
            risk_gbp=0.0,
            risk_pct=0.0,
            position_pct=0.0,
            binding_constraint=binding,
            regime_multiplier=regime_multiplier,
            feasible=False,
            reason="No shares can be purchased within current constraints.",
        )

    constraint_explanations = {
        SizingConstraint.RISK_RULE: (
            f"Sized by 1% risk rule × {regime_multiplier}× regime multiplier."
        ),
        SizingConstraint.POSITION_CAP: (
            f"15% position cap (1% risk rule alone would allow "
            f"{int(max_risk_gbp / risk_per_share_gbp)} shares)."
        ),
        SizingConstraint.CASH_MIN: (
            f"10% cash minimum (must keep ≥ £{min_cash_after:,.0f} in cash)."
        ),
    }

    return SizingResult(
        quantity=quantity,
        position_value_gbp=position_value,
        risk_gbp=risk_gbp,
        risk_pct=risk_gbp / equity_gbp if equity_gbp > 0 else 0.0,
        position_pct=position_value / equity_gbp if equity_gbp > 0 else 0.0,
        binding_constraint=binding,
        regime_multiplier=regime_multiplier,
        feasible=True,
        reason=constraint_explanations[binding],
    )
