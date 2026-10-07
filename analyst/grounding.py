"""Grounding check — SPEC section 5, point 3.

After the AI judge returns its idea, code verifies every price level
cited (entry, stop, target) against the tool data.

Rules:
  - Every cited level must be within TOLERANCE_PCT of a level returned
    by get_support_resistance, the current price, or recent OHLCV data.
  - An idea that cites a level that cannot be grounded is rejected, and
    the reason is logged.
  - The AI must not do its own arithmetic; it reads levels from tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TOLERANCE_PCT = 0.03  # 3% — a cited level must be within 3% of a tool-returned level


@dataclass
class GroundingResult:
    passed: bool
    checked_levels: list[dict]  # {level_name, cited_value, nearest_reference, distance_pct, passed}
    reason: str


def _nearest_reference(value: float, references: list[float]) -> tuple[float, float]:
    """Return (nearest_ref, distance_pct)."""
    if not references:
        return 0.0, float("inf")
    nearest = min(references, key=lambda r: abs(r - value))
    dist = abs(nearest - value) / nearest if nearest != 0 else float("inf")
    return nearest, dist


def verify_price_levels(
    entry_price: float,
    stop_price: float,
    target_price: float,
    support_resistance: dict,
    indicators: dict,
    bars_data: dict,
) -> GroundingResult:
    """Verify that the AI's cited levels are grounded in tool data.

    Args:
        entry_price: Price the AI proposes to enter at.
        stop_price: Stop-loss level the AI proposes.
        target_price: Target level the AI proposes.
        support_resistance: Output of get_support_resistance().
        indicators: Output of get_indicators().
        bars_data: Output of get_bars().
    """
    # Build reference set from all tool data
    references: list[float] = []

    # From support/resistance
    references.extend(support_resistance.get("support_levels", []))
    references.extend(support_resistance.get("resistance_levels", []))
    references.append(support_resistance.get("current_price", 0.0))

    # From indicators (SMA levels are valid reference points)
    for key in ("sma_20", "sma_50", "sma_200"):
        v = indicators.get(key)
        if v:
            references.append(v)

    # From bars: recent highs and lows
    if bars_data.get("bars"):
        for b in bars_data["bars"][-20:]:
            references.append(b["high"])
            references.append(b["low"])
            references.append(b["close"])

    references = [r for r in references if r > 0]

    levels_to_check = [
        ("entry", entry_price),
        ("stop", stop_price),
        ("target", target_price),
    ]

    checked = []
    all_passed = True

    for name, value in levels_to_check:
        if value <= 0:
            checked.append({
                "level_name": name,
                "cited_value": value,
                "nearest_reference": None,
                "distance_pct": None,
                "passed": False,
            })
            all_passed = False
            continue

        nearest, dist = _nearest_reference(value, references)
        passed = dist <= TOLERANCE_PCT
        if not passed:
            logger.warning(
                "Grounding fail — %s level %.2f is %.1f%% from nearest reference %.2f "
                "(tolerance %.0f%%)",
                name, value, dist * 100, nearest, TOLERANCE_PCT * 100,
            )
            all_passed = False

        checked.append({
            "level_name": name,
            "cited_value": round(value, 2),
            "nearest_reference": round(nearest, 2),
            "distance_pct": round(dist * 100, 2),
            "passed": passed,
        })

    if all_passed:
        reason = "All price levels grounded in tool data."
    else:
        failed = [c["level_name"] for c in checked if not c["passed"]]
        reason = (
            f"Grounding failed for: {', '.join(failed)}. "
            "Levels not found in support/resistance, indicators, or recent bars."
        )

    return GroundingResult(
        passed=all_passed,
        checked_levels=checked,
        reason=reason,
    )
