"""Backtester for the baseline rules strategy.

Runs the BaselineStrategy over historical data and reports:
  CAGR, max drawdown, Sharpe, worst month, trade count, win rate.

Also runs buy-and-hold benchmarks for VWRL, S&P 500 ETF, and cash.

Per SPEC.md section 9:
  - Paper slippage haircut applied to all stats.
  - If baseline doesn't beat buy-and-hold after costs, say so plainly.
  - AI's picks cannot be honestly backtested (model knows history).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from data.models import Bar
from instruments.units import InstrumentSpec
from strategy.baseline import BaselineStrategy

UTC = timezone.utc


@dataclass
class BacktestResult:
    strategy_name: str
    start_date: date
    end_date: date
    starting_equity: float
    ending_equity: float
    cagr: float
    max_drawdown: float
    sharpe: float
    worst_month_return: float
    trade_count: int
    win_rate: float
    paper_slippage_haircut: float
    haircut_ending_equity: float
    haircut_cagr: float
    notes: list[str]

    def summary(self) -> str:
        lines = [
            f"=== {self.strategy_name} ({self.start_date} → {self.end_date}) ===",
            f"  Starting equity:     £{self.starting_equity:,.0f}",
            f"  Ending equity:       £{self.ending_equity:,.0f}",
            f"  Ending (haircut):    £{self.haircut_ending_equity:,.0f}",
            f"  CAGR:                {self.cagr:.1%}",
            f"  CAGR (haircut):      {self.haircut_cagr:.1%}",
            f"  Max drawdown:        {self.max_drawdown:.1%}",
            f"  Sharpe:              {self.sharpe:.2f}",
            f"  Worst month:         {self.worst_month_return:.1%}",
            f"  Trades:              {self.trade_count}",
            f"  Win rate:            {self.win_rate:.1%}",
        ]
        for note in self.notes:
            lines.append(f"  NOTE: {note}")
        return "\n".join(lines)


def _cagr(start: float, end: float, years: float) -> float:
    if start <= 0 or years <= 0:
        return 0.0
    return (end / start) ** (1 / years) - 1


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sharpe(monthly_returns: list[float], rf: float = 0.04) -> float:
    if len(monthly_returns) < 2:
        return 0.0
    annual_rf = (1 + rf) ** (1 / 12) - 1
    excess = [r - annual_rf for r in monthly_returns]
    mean = statistics.mean(excess)
    std = statistics.stdev(excess)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(12)


def _buy_and_hold(
    bars: list[Bar],
    starting_equity: float,
    fx_usd_gbp: float = 0.79,
    paper_haircut: float = 0.001,
) -> list[float]:
    """Equity curve for buy-and-hold (one purchase at start, hold to end)."""
    if not bars:
        return [starting_equity]
    from instruments.units import PriceUnit, to_gbp

    spec_unit = bars[0].price_unit
    entry_price = to_gbp(bars[0].close, spec_unit, fx_usd_gbp)
    entry_price_with_slippage = entry_price * (1 + paper_haircut)
    if entry_price_with_slippage <= 0:
        return [starting_equity]
    shares = starting_equity / entry_price_with_slippage

    curve = []
    for b in bars:
        price = to_gbp(b.close, spec_unit, fx_usd_gbp)
        curve.append(shares * price)
    return curve


def run_backtest(
    bars_by_symbol: dict[str, list[Bar]],
    specs: dict[str, InstrumentSpec],
    benchmark_bars: Optional[dict[str, list[Bar]]] = None,
    starting_equity: float = 12_500.0,
    fx_usd_gbp: float = 0.79,
    top_n: int = 5,
) -> list[BacktestResult]:
    """Run the baseline strategy and benchmarks, return results for all."""
    from config.settings import load_risk_limits
    limits = load_risk_limits()
    paper_haircut = limits.costs.paper_slippage_haircut

    # Collect all trading dates from the universe
    all_dates: list[date] = sorted(
        {b.ts.date() for bars in bars_by_symbol.values() for b in bars}
    )
    if len(all_dates) < 252:
        return []

    start_date = all_dates[0]
    end_date = all_dates[-1]
    years = (end_date - start_date).days / 365.25

    strategy = BaselineStrategy(
        starting_equity_gbp=starting_equity,
        top_n=top_n,
        fx_usd_gbp=fx_usd_gbp,
    )

    equity_curve: list[float] = []
    monthly_returns: list[float] = []
    prev_month_equity = starting_equity

    for d in all_dates:
        # Slice bars up to and including d for each symbol
        current_bars: dict[str, list[Bar]] = {}
        for sym, bars in bars_by_symbol.items():
            cb = [b for b in bars if b.ts.date() <= d]
            if cb:
                current_bars[sym] = cb

        # Rebalance on Fridays (weekday == 4)
        weekday = datetime(*d.timetuple()[:3]).weekday()
        if weekday == 4:
            strategy.rebalance(d, current_bars, specs)

        equity = strategy.mark_to_market(d, current_bars, specs)
        equity_curve.append(equity)

        # Monthly returns
        if len(equity_curve) >= 21:
            m_ret = equity / equity_curve[-21] - 1
            if len(equity_curve) % 21 == 0:
                monthly_returns.append(m_ret)

    ending_equity = equity_curve[-1] if equity_curve else starting_equity
    haircut_equity = ending_equity * (1 - paper_haircut * 2 * len(strategy.trade_log))
    haircut_cagr = _cagr(starting_equity, max(haircut_equity, 0.01), years)

    wins = [t for t in strategy.trade_log if t["action"] in ("SELL", "STOP") and t.get("pnl_gbp", 0) > 0]
    closes = [t for t in strategy.trade_log if t["action"] in ("SELL", "STOP")]
    win_rate = len(wins) / len(closes) if closes else 0.0

    notes: list[str] = []
    if not monthly_returns:
        notes.append("Insufficient history for monthly statistics.")
    sharpe = _sharpe(monthly_returns) if monthly_returns else 0.0
    worst_month = min(monthly_returns) if monthly_returns else 0.0

    results = [
        BacktestResult(
            strategy_name="Baseline rules strategy",
            start_date=start_date,
            end_date=end_date,
            starting_equity=starting_equity,
            ending_equity=ending_equity,
            cagr=_cagr(starting_equity, ending_equity, years),
            max_drawdown=_max_drawdown(equity_curve),
            sharpe=sharpe,
            worst_month_return=worst_month,
            trade_count=len(strategy.trade_log),
            win_rate=win_rate,
            paper_slippage_haircut=paper_haircut,
            haircut_ending_equity=haircut_equity,
            haircut_cagr=haircut_cagr,
            notes=notes,
        )
    ]

    # Buy-and-hold benchmarks
    if benchmark_bars:
        for bench_name, bars in benchmark_bars.items():
            if not bars:
                continue
            curve = _buy_and_hold(bars, starting_equity, fx_usd_gbp, paper_haircut)
            end_eq = curve[-1]
            bh_months = []
            for i in range(21, len(curve), 21):
                bh_months.append(curve[i] / curve[i - 21] - 1)

            beats = results[0].haircut_cagr > _cagr(starting_equity, end_eq, years)
            if not beats:
                notes_bh = [
                    f"Baseline does NOT beat {bench_name} buy-and-hold after costs."
                ]
            else:
                notes_bh = [f"Baseline beats {bench_name} buy-and-hold after costs."]

            results.append(
                BacktestResult(
                    strategy_name=f"Buy-and-hold {bench_name}",
                    start_date=start_date,
                    end_date=end_date,
                    starting_equity=starting_equity,
                    ending_equity=end_eq,
                    cagr=_cagr(starting_equity, end_eq, years),
                    max_drawdown=_max_drawdown(curve),
                    sharpe=_sharpe(bh_months) if bh_months else 0.0,
                    worst_month_return=min(bh_months) if bh_months else 0.0,
                    trade_count=1,
                    win_rate=1.0 if end_eq > starting_equity else 0.0,
                    paper_slippage_haircut=paper_haircut,
                    haircut_ending_equity=end_eq * (1 - paper_haircut * 2),
                    haircut_cagr=_cagr(starting_equity, end_eq * (1 - paper_haircut * 2), years),
                    notes=notes_bh,
                )
            )

    return results
