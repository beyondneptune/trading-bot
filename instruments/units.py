"""Price unit normalisation.

LSE single stocks are quoted in pence (GBX).  Most LSE-listed ETFs are
quoted in GBP or USD.  US stocks and ETFs are in USD.

Rule: all internal P&L, risk and cost calculations use GBP (pounds).
      Store raw prices alongside their unit so we can always display
      the correct value and detect unit bugs.

Classic bug to avoid: treating a 100p (1 GBP) share as if it were
100 GBP — a 100× error.  Every price that crosses a module boundary
must carry its PriceUnit.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Sequence

import yaml
from pydantic import BaseModel, field_validator


class PriceUnit(str, Enum):
    GBX = "GBX"  # pence (LSE single stocks)
    GBP = "GBP"  # pounds sterling
    USD = "USD"  # US dollars


def to_gbp(price: float, unit: PriceUnit, fx_usd_gbp: float = 1.0) -> float:
    """Convert a price to GBP.

    Args:
        price: raw price in the instrument's native unit
        unit: the PriceUnit of the raw price
        fx_usd_gbp: USD→GBP rate (e.g. 0.79).  Required when unit=USD.
    """
    if unit == PriceUnit.GBX:
        return price / 100.0
    if unit == PriceUnit.USD:
        return price * fx_usd_gbp
    return price  # already GBP


def to_display_price(price_gbp: float, unit: PriceUnit) -> float:
    """Convert a GBP price back to the instrument's display unit."""
    if unit == PriceUnit.GBX:
        return price_gbp * 100.0
    return price_gbp


class InstrumentSpec(BaseModel):
    symbol: str
    exchange: str
    currency: str       # GBP | USD (settlement currency)
    price_unit: PriceUnit
    sector: str = "unknown"
    min_tick: float = 0.01
    price_magnifier: int = 1

    @field_validator("symbol")
    @classmethod
    def symbol_upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("exchange")
    @classmethod
    def exchange_upper(cls, v: str) -> str:
        return v.upper()

    @property
    def is_uk_share(self) -> bool:
        """True for LSE-listed shares quoted in pence (stamp duty applies)."""
        return self.exchange == "LSE" and self.price_unit == PriceUnit.GBX

    @property
    def stamp_duty_applies(self) -> bool:
        return self.is_uk_share


def load_watchlist(path: str | Path | None = None) -> list[InstrumentSpec]:
    """Load and validate every instrument from the watchlist YAML."""
    if path is None:
        path = Path(__file__).parent.parent / "config" / "watchlist.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)

    specs: list[InstrumentSpec] = []
    for group in data.values():
        for item in group:
            specs.append(InstrumentSpec(**item))
    return specs


def find_instrument(
    specs: Sequence[InstrumentSpec], symbol: str, exchange: str
) -> InstrumentSpec | None:
    symbol = symbol.upper()
    exchange = exchange.upper()
    for s in specs:
        if s.symbol == symbol and s.exchange == exchange:
            return s
    return None
