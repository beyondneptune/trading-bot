"""Round-trip cost calculator (CLAUDE.md rule 7 / SPEC section 2).

Cost components:
  - Commission: flat per side (configurable)
  - Spread: half-spread on entry, half-spread on exit
  - Slippage: per side
  - FX conversion: if instrument is in USD and account is GBP
  - UK stamp duty: 0.5% on purchases of UK shares (NOT ETFs, NOT US stocks)

All costs are expressed as a fraction of the initial risk (R) so the
cost gate "skip if cost > 0.15R" is directly computable.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import load_risk_limits
from instruments.units import InstrumentSpec, PriceUnit, to_gbp


@dataclass
class CostBreakdown:
    commission_gbp: float
    spread_gbp: float
    slippage_gbp: float
    stamp_duty_gbp: float
    fx_cost_gbp: float
    total_gbp: float
    cost_as_r: float        # total / initial_risk_gbp
    paper_haircut_gbp: float

    @property
    def total_with_haircut_gbp(self) -> float:
        return self.total_gbp + self.paper_haircut_gbp

    @property
    def cost_with_haircut_as_r(self) -> float:
        if self.cost_as_r == 0:
            return 0.0
        # Scale proportionally
        return self.cost_as_r * (self.total_with_haircut_gbp / self.total_gbp) if self.total_gbp > 0 else 0.0

    def summary(self) -> str:
        return (
            f"Commission: £{self.commission_gbp:.2f}  "
            f"Spread: £{self.spread_gbp:.2f}  "
            f"Slippage: £{self.slippage_gbp:.2f}  "
            f"Stamp duty: £{self.stamp_duty_gbp:.2f}  "
            f"FX: £{self.fx_cost_gbp:.2f}  "
            f"Total: £{self.total_gbp:.2f} ({self.cost_as_r:.3f}R)"
        )


def estimate_round_trip_cost(
    spec: InstrumentSpec,
    entry_price: float,
    quantity: float,
    initial_risk_gbp: float,
    fx_usd_gbp: float = 1.0,
    spread_pct: float | None = None,
) -> CostBreakdown:
    """Estimate the full round-trip cost of a trade.

    Args:
        spec: Instrument specification (determines stamp duty, currency).
        entry_price: Entry price in the instrument's native unit.
        quantity: Number of shares / units.
        initial_risk_gbp: £ risk at the stop (entry - stop) × quantity in GBP.
        fx_usd_gbp: Current USD→GBP rate. Only used if spec.price_unit == USD.
        spread_pct: Observed bid-ask spread as a fraction of price. If None,
                    uses the default from config.
    """
    limits = load_risk_limits()
    c = limits.costs

    if spread_pct is None:
        spread_pct = c.default_spread_estimate

    entry_gbp = to_gbp(entry_price, spec.price_unit, fx_usd_gbp)
    position_value_gbp = entry_gbp * quantity

    # Commission: flat per side × 2
    commission = c.commission_per_trade * 2

    # Spread: half-spread on entry + half-spread on exit
    spread_cost = position_value_gbp * spread_pct

    # Slippage: per side × 2
    slippage = position_value_gbp * c.slippage_per_side * 2

    # Stamp duty: only on purchase of UK shares (not ETFs, not US stocks)
    stamp_duty = (
        position_value_gbp * c.uk_stamp_duty
        if spec.stamp_duty_applies
        else 0.0
    )

    # FX conversion cost: only for USD instruments
    fx_cost = (
        position_value_gbp * 0.002  # IB charges ~0.2% on FX conversion
        if spec.price_unit == PriceUnit.USD
        else 0.0
    )

    total = commission + spread_cost + slippage + stamp_duty + fx_cost

    # Paper slippage haircut (extra per-side for paper realism)
    paper_haircut = position_value_gbp * c.paper_slippage_haircut * 2

    cost_as_r = total / initial_risk_gbp if initial_risk_gbp > 0 else float("inf")

    return CostBreakdown(
        commission_gbp=commission,
        spread_gbp=spread_cost,
        slippage_gbp=slippage,
        stamp_duty_gbp=stamp_duty,
        fx_cost_gbp=fx_cost,
        total_gbp=total,
        cost_as_r=cost_as_r,
        paper_haircut_gbp=paper_haircut,
    )
