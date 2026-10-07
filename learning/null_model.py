"""Null model: tests whether observed R statistics could be explained by
random chance.

The null hypothesis: "My system has no edge — a random strategy with the
same number of trades and the same average winner/loser magnitudes would
produce equally good results by luck."

We use Monte Carlo simulation:
  1. Note observed win_rate and average |R| from actual trades
  2. Simulate N trial sequences of n trades from a zero-expectancy baseline:
     - Each simulated trade: +avg_win_abs with prob 0.5, -avg_loss_abs with prob 0.5
  3. Count simulations where expectancy >= observed expectancy
  4. p-value = count / N

If p-value < 0.05, we reject the null at 95% confidence → edge detected.

Minimum sample: 30 trades required before testing.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

MIN_TRADES_FOR_TEST = 30


@dataclass
class NullModelResult:
    n: int
    observed_expectancy: float
    p_value: float
    simulations: int
    passed: bool       # True if we can reject the null (p < threshold)
    threshold: float

    @property
    def verdict(self) -> str:
        if self.n < MIN_TRADES_FOR_TEST:
            return f"INSUFFICIENT_DATA (need {MIN_TRADES_FOR_TEST}, have {self.n})"
        if self.passed:
            return f"EDGE_DETECTED (p={self.p_value:.3f} < {self.threshold})"
        return f"NO_EDGE (p={self.p_value:.3f} >= {self.threshold})"


def run_permutation_test(
    r_multiples: Sequence[float],
    n_simulations: int = 10_000,
    p_threshold: float = 0.05,
    seed: int = 42,
) -> NullModelResult:
    """Monte Carlo test for trading edge against a zero-expectancy null."""
    rs = list(r_multiples)
    n = len(rs)

    if n < MIN_TRADES_FOR_TEST:
        return NullModelResult(
            n=n,
            observed_expectancy=sum(rs) / n if n > 0 else 0.0,
            p_value=1.0,
            simulations=0,
            passed=False,
            threshold=p_threshold,
        )

    observed = sum(rs) / n
    rng = random.Random(seed)

    # Build symmetric null distribution from the magnitude of trades
    winners = [abs(r) for r in rs if r > 0]
    losers = [abs(r) for r in rs if r < 0]
    avg_win = sum(winners) / len(winners) if winners else 1.0
    avg_loss = sum(losers) / len(losers) if losers else 1.0

    count_at_least_as_good = 0
    for _ in range(n_simulations):
        sim = sum(
            avg_win if rng.random() < 0.5 else -avg_loss
            for _ in range(n)
        )
        sim_expectancy = sim / n
        if sim_expectancy >= observed:
            count_at_least_as_good += 1

    p_value = count_at_least_as_good / n_simulations
    # Clamp to avoid p=0 from finite samples
    p_value = max(p_value, 1 / (n_simulations + 1))
    passed = p_value < p_threshold

    return NullModelResult(
        n=n,
        observed_expectancy=observed,
        p_value=p_value,
        simulations=n_simulations,
        passed=passed,
        threshold=p_threshold,
    )
