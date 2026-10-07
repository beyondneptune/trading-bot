"""Market context: regime note and multiplier.

The regime multiplier gates new position sizing (CLAUDE.md rule 6):
  normal  → 1.0×
  elevated → 0.5×
  stressed → 0.0  (no new buys; manage existing only)

Computed from:
  - 20-day realised volatility of the index ETFs (VWRL, CSPX) as a
    percentile of the trailing 252-day window
  - Whether the index is above or below its 200-day SMA

Thresholds live in config/risk_limits.yaml under `regime:`.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config.settings import load_risk_limits
from data.models import Bar

logger = logging.getLogger(__name__)

UTC = timezone.utc


@dataclass
class MarketRegime:
    label: str                      # "normal" | "elevated" | "stressed"
    multiplier: float               # 0.0, 0.5, or 1.0
    vol_percentile: float           # 0–100
    index_above_200d_sma: bool
    note: str                       # human-readable summary
    computed_at: datetime


def _realised_vol_20d(closes: list[float]) -> float:
    """Annualised 20-day realised volatility from daily closes."""
    if len(closes) < 21:
        return 0.0
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(len(closes) - 20, len(closes))
    ]
    return statistics.stdev(rets) * math.sqrt(252)


def _vol_percentile(
    closes: list[float],
    window: int = 252,
    vol_window: int = 20,
) -> float:
    """Percentile of current 20d vol within the trailing `window` days."""
    if len(closes) < window + vol_window:
        return 50.0  # default to middle if not enough history

    vols = []
    for i in range(vol_window, window + vol_window):
        subset = closes[i - vol_window : i + 1]
        vols.append(_realised_vol_20d(subset))

    current = _realised_vol_20d(closes[-vol_window - 1 :])
    below = sum(1 for v in vols if v < current)
    return below / len(vols) * 100


def _sma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def compute_regime(
    index_bars: list[Bar],
    as_of: Optional[datetime] = None,
) -> MarketRegime:
    """Compute regime from index ETF bars.

    Args:
        index_bars: Daily bars for an index ETF (e.g. VWRL/CSPX).
        as_of: Timestamp for the note; defaults to now.
    """
    if as_of is None:
        as_of = datetime.now(UTC)

    limits = load_risk_limits()
    elevated_pct = limits.regime.elevated_vol_percentile
    stressed_pct = limits.regime.stressed_vol_percentile

    if not index_bars:
        return MarketRegime(
            label="normal",
            multiplier=1.0,
            vol_percentile=50.0,
            index_above_200d_sma=True,
            note="Insufficient data — defaulting to normal regime.",
            computed_at=as_of,
        )

    closes = [b.close for b in sorted(index_bars, key=lambda b: b.ts)]
    vol_pct = _vol_percentile(closes)
    sma200 = _sma(closes, 200)
    above_sma = closes[-1] > sma200 if sma200 is not None else True

    if vol_pct >= stressed_pct or not above_sma:
        label, mult = "stressed", 0.0
    elif vol_pct >= elevated_pct:
        label, mult = "elevated", 0.5
    else:
        label, mult = "normal", 1.0

    note = (
        f"Regime: {label}. "
        f"Vol percentile: {vol_pct:.0f}% "
        f"(elevated≥{elevated_pct}, stressed≥{stressed_pct}). "
        f"Index {'above' if above_sma else 'below'} 200d SMA. "
        f"New-position multiplier: {mult}×."
    )

    return MarketRegime(
        label=label,
        multiplier=mult,
        vol_percentile=vol_pct,
        index_above_200d_sma=above_sma,
        note=note,
        computed_at=as_of,
    )
