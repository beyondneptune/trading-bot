"""Baseline rules strategy.

Rule set (SPEC section 9):
  - Universe: instruments above their 200-day SMA
  - Rank by combined 6-month + 12-month price momentum (equal weight)
  - Hold top-N positions (configurable; default 5)
  - Stop: 2×ATR(14) below entry
  - Rebalance: weekly (Friday close)
  - Size: equal-weight among held positions, respecting cash minimum

This runs as a virtual portfolio in parallel with the AI strategy.
Results are stored in the database and shown on the performance page.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from data.models import Bar
from instruments.units import InstrumentSpec, PriceUnit, to_gbp

UTC = timezone.utc


@dataclass
class BaselinePosition:
    symbol: str
    exchange: str
    entry_date: date
    entry_price_gbp: float
    stop_price_gbp: float
    quantity: float
    cost_gbp: float


@dataclass
class BaselineSnapshot:
    as_of: date
    equity_gbp: float
    positions: list[BaselinePosition]
    cash_gbp: float
    trade_log: list[dict] = field(default_factory=list)


def _atr(bars: list[Bar], period: int = 14) -> float:
    """Average True Range over the last `period` bars."""
    if len(bars) < period + 1:
        return bars[-1].close * 0.02 if bars else 0.0
    trs = []
    for i in range(-period, 0):
        b = bars[i]
        prev_close = bars[i - 1].close
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs)


def _sma(bars: list[Bar], period: int) -> Optional[float]:
    if len(bars) < period:
        return None
    return sum(b.close for b in bars[-period:]) / period


def _momentum(bars: list[Bar], months: int) -> Optional[float]:
    """Price momentum: return over the last `months` calendar months."""
    trading_days = months * 21
    if len(bars) < trading_days + 1:
        return None
    return bars[-1].close / bars[-trading_days].close - 1.0


def _to_gbp_price(price: float, spec: InstrumentSpec, fx: float = 1.0) -> float:
    return to_gbp(price, spec.price_unit, fx_usd_gbp=fx)


def _commission(cost_gbp: float, is_uk_share: bool = False) -> float:
    """Estimate round-trip commission + stamp duty."""
    from config.settings import load_risk_limits
    limits = load_risk_limits()
    c = limits.costs
    stamp = cost_gbp * c.uk_stamp_duty if is_uk_share else 0.0
    return c.commission_per_trade * 2 + stamp + cost_gbp * (c.slippage_per_side * 2)


class BaselineStrategy:
    """Tracks a virtual portfolio running the baseline rules strategy."""

    def __init__(
        self,
        starting_equity_gbp: float = 12_500.0,
        top_n: int = 5,
        fx_usd_gbp: float = 0.79,
    ) -> None:
        self.equity = starting_equity_gbp
        self.top_n = top_n
        self.fx = fx_usd_gbp
        self.cash = starting_equity_gbp
        self.positions: dict[str, BaselinePosition] = {}
        self.snapshots: list[BaselineSnapshot] = []
        self.trade_log: list[dict] = []

    def _rank_universe(
        self,
        bars_by_symbol: dict[str, list[Bar]],
        specs: dict[str, InstrumentSpec],
    ) -> list[tuple[str, float]]:
        """Score each symbol by 6m+12m momentum. Return sorted best-first."""
        scores = []
        for symbol, bars in bars_by_symbol.items():
            sma = _sma(bars, 200)
            if sma is None or bars[-1].close <= sma:
                continue
            m6 = _momentum(bars, 6)
            m12 = _momentum(bars, 12)
            if m6 is None or m12 is None:
                continue
            scores.append((symbol, (m6 + m12) / 2))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def rebalance(
        self,
        as_of_date: date,
        bars_by_symbol: dict[str, list[Bar]],
        specs: dict[str, InstrumentSpec],
    ) -> None:
        """Rebalance the portfolio on a weekly basis (called on Fridays)."""
        ranked = self._rank_universe(bars_by_symbol, specs)
        target_symbols = {sym for sym, _ in ranked[: self.top_n]}

        # Close positions no longer in the top-N
        for sym in list(self.positions.keys()):
            if sym not in target_symbols:
                pos = self.positions.pop(sym)
                bars = bars_by_symbol.get(sym, [])
                if not bars:
                    continue
                spec = specs[sym]
                exit_price_gbp = _to_gbp_price(bars[-1].close, spec, self.fx)
                proceeds = pos.quantity * exit_price_gbp
                cost = _commission(proceeds, spec.is_uk_share)
                self.cash += proceeds - cost
                self.trade_log.append(
                    {
                        "date": str(as_of_date),
                        "action": "SELL",
                        "symbol": sym,
                        "price_gbp": exit_price_gbp,
                        "quantity": pos.quantity,
                        "pnl_gbp": proceeds - pos.cost_gbp - cost,
                    }
                )

        # Check stops on remaining positions
        for sym, pos in list(self.positions.items()):
            bars = bars_by_symbol.get(sym, [])
            if not bars:
                continue
            spec = specs[sym]
            current_price_gbp = _to_gbp_price(bars[-1].close, spec, self.fx)
            if current_price_gbp <= pos.stop_price_gbp:
                proceeds = pos.quantity * pos.stop_price_gbp
                cost = _commission(proceeds, spec.is_uk_share)
                self.cash += proceeds - cost
                self.trade_log.append(
                    {
                        "date": str(as_of_date),
                        "action": "STOP",
                        "symbol": sym,
                        "price_gbp": pos.stop_price_gbp,
                        "quantity": pos.quantity,
                        "pnl_gbp": proceeds - pos.cost_gbp - cost,
                    }
                )
                del self.positions[sym]

        # Open new positions in target symbols not already held
        new_targets = [s for s in target_symbols if s not in self.positions]
        n_slots = self.top_n - len(self.positions)
        if n_slots <= 0 or not new_targets or self.cash < 100:
            return

        # Equal-weight allocation
        target_equity = self.equity
        slot_size = target_equity / self.top_n

        for sym in new_targets[:n_slots]:
            bars = bars_by_symbol.get(sym, [])
            if not bars:
                continue
            spec = specs.get(sym)
            if spec is None:
                continue
            entry_price_gbp = _to_gbp_price(bars[-1].close, spec, self.fx)
            if entry_price_gbp <= 0:
                continue
            atr_native = _atr(bars)
            atr_gbp = _to_gbp_price(atr_native, spec, self.fx)
            stop_gbp = entry_price_gbp - 2 * atr_gbp

            quantity = slot_size / entry_price_gbp
            cost_gbp = quantity * entry_price_gbp
            commission = _commission(cost_gbp, spec.is_uk_share)
            total_cost = cost_gbp + commission

            if total_cost > self.cash:
                continue

            self.cash -= total_cost
            self.positions[sym] = BaselinePosition(
                symbol=sym,
                exchange=spec.exchange,
                entry_date=as_of_date,
                entry_price_gbp=entry_price_gbp,
                stop_price_gbp=stop_gbp,
                quantity=quantity,
                cost_gbp=cost_gbp,
            )
            self.trade_log.append(
                {
                    "date": str(as_of_date),
                    "action": "BUY",
                    "symbol": sym,
                    "price_gbp": entry_price_gbp,
                    "quantity": quantity,
                    "stop_gbp": stop_gbp,
                }
            )

    def mark_to_market(
        self,
        as_of_date: date,
        bars_by_symbol: dict[str, list[Bar]],
        specs: dict[str, InstrumentSpec],
    ) -> float:
        """Compute current equity (cash + open positions at current prices)."""
        equity = self.cash
        for sym, pos in self.positions.items():
            bars = bars_by_symbol.get(sym, [])
            if not bars:
                equity += pos.cost_gbp
                continue
            spec = specs.get(sym)
            if spec is None:
                equity += pos.cost_gbp
                continue
            price_gbp = _to_gbp_price(bars[-1].close, spec, self.fx)
            equity += pos.quantity * price_gbp
        self.equity = equity
        return equity
