"""Portfolio heat and correlation clustering — CLAUDE.md rule 5.

Portfolio heat = sum of (entry - stop) × quantity across all open positions,
expressed as a fraction of current equity.  Must stay ≤ 5%.

Correlated cluster rule: positions whose 60-day return correlation is ≥ 0.8
are grouped; their combined heat must stay ≤ 2%.

Correlation is computed lazily from cached daily bar returns.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from config.settings import load_risk_limits
from data.models import Bar
from instruments.units import PriceUnit, to_gbp


@dataclass
class OpenPosition:
    symbol: str
    exchange: str
    quantity: float
    entry_price_gbp: float
    stop_price_gbp: float

    @property
    def heat_gbp(self) -> float:
        return self.quantity * (self.entry_price_gbp - self.stop_price_gbp)


@dataclass
class HeatCheckResult:
    total_heat_pct: float
    max_heat_pct: float             # from config
    portfolio_ok: bool
    cluster_violations: list[tuple[list[str], float]]   # (symbols, heat_pct)
    new_position_heat_pct: float
    feasible: bool
    reason: str


def _daily_returns(bars: list[Bar]) -> list[float]:
    closes = [b.close for b in sorted(bars, key=lambda b: b.ts)]
    if len(closes) < 2:
        return []
    return [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
    ]


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 5:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def compute_return_correlations(
    bars_by_symbol: dict[str, list[Bar]],
    window: int = 60,
) -> dict[tuple[str, str], float]:
    """Compute pairwise 60-day return correlations for all symbol pairs."""
    rets: dict[str, list[float]] = {}
    for sym, bars in bars_by_symbol.items():
        r = _daily_returns(bars[-window - 1 :])
        rets[sym] = r

    symbols = list(rets.keys())
    corr: dict[tuple[str, str], float] = {}
    for i, s1 in enumerate(symbols):
        for s2 in symbols[i + 1 :]:
            c = _pearson(rets[s1], rets[s2])
            corr[(s1, s2)] = c
            corr[(s2, s1)] = c
    return corr


def find_clusters(
    symbols: list[str],
    correlations: dict[tuple[str, str], float],
    threshold: float,
) -> list[list[str]]:
    """Group symbols into clusters where all pairs have corr ≥ threshold.

    Uses a simple union-find / greedy grouping.
    """
    parent: dict[str, str] = {s: s for s in symbols}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (s1, s2), c in correlations.items():
        if c >= threshold:
            r1, r2 = find(s1), find(s2)
            if r1 != r2:
                parent[r1] = r2

    groups: dict[str, list[str]] = {}
    for s in symbols:
        root = find(s)
        groups.setdefault(root, []).append(s)
    return [g for g in groups.values() if len(g) > 1]


def check_portfolio_heat(
    positions: list[OpenPosition],
    new_position: OpenPosition,
    equity_gbp: float,
    bars_by_symbol: dict[str, list[Bar]],
) -> HeatCheckResult:
    """Check if adding `new_position` would violate heat limits."""
    limits = load_risk_limits()
    max_heat = limits.max_portfolio_heat
    max_cluster = limits.max_cluster_heat
    corr_threshold = limits.cluster_correlation_threshold

    existing_heat = sum(p.heat_gbp for p in positions)
    new_heat = new_position.heat_gbp
    total_heat = existing_heat + new_heat
    total_heat_pct = total_heat / equity_gbp if equity_gbp > 0 else 0.0
    new_heat_pct = new_heat / equity_gbp if equity_gbp > 0 else 0.0

    if total_heat_pct > max_heat:
        return HeatCheckResult(
            total_heat_pct=total_heat_pct,
            max_heat_pct=max_heat,
            portfolio_ok=False,
            cluster_violations=[],
            new_position_heat_pct=new_heat_pct,
            feasible=False,
            reason=f"Portfolio heat {total_heat_pct:.1%} would exceed {max_heat:.0%} limit.",
        )

    # Cluster check
    all_positions = positions + [new_position]
    all_symbols = [p.symbol for p in all_positions]
    correlations = compute_return_correlations(bars_by_symbol)
    clusters = find_clusters(all_symbols, correlations, corr_threshold)

    violations = []
    for cluster in clusters:
        cluster_heat = sum(
            p.heat_gbp for p in all_positions if p.symbol in cluster
        )
        cluster_heat_pct = cluster_heat / equity_gbp if equity_gbp > 0 else 0.0
        if cluster_heat_pct > max_cluster:
            violations.append((cluster, cluster_heat_pct))

    if violations:
        v_strs = [
            f"{'+'.join(syms)}: {pct:.1%}" for syms, pct in violations
        ]
        return HeatCheckResult(
            total_heat_pct=total_heat_pct,
            max_heat_pct=max_heat,
            portfolio_ok=False,
            cluster_violations=violations,
            new_position_heat_pct=new_heat_pct,
            feasible=False,
            reason=f"Cluster heat limit ({max_cluster:.0%}) exceeded: {'; '.join(v_strs)}.",
        )

    return HeatCheckResult(
        total_heat_pct=total_heat_pct,
        max_heat_pct=max_heat,
        portfolio_ok=True,
        cluster_violations=[],
        new_position_heat_pct=new_heat_pct,
        feasible=True,
        reason="Heat checks passed.",
    )
