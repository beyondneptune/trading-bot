"""Fast AI check: one-shot Claude call (haiku, no tools, <$0.001) that
decides whether an alert is worth surfacing to the user.

The fast check receives:
  - symbol, alert_type, trigger_price
  - the last N bars as a compact string
  - a brief note from the detector

It returns YES / NO / SKIP:
  YES  → write alert to DB, notify user (state = LIVE)
  NO   → suppress silently (state = SUPPRESSED)
  SKIP → treat as YES but flag as unverified (fast check failed/timed out)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_MOCK_FAST_CHECK = True  # flipped to False when ANTHROPIC_API_KEY is set

FAST_CHECK_MODEL = "claude-haiku-4-5-20251001"
FAST_CHECK_MAX_TOKENS = 80
_SYSTEM = (
    "You are a fast triage filter for a trading alert system. "
    "Reply with exactly one word: YES, NO, or SKIP. "
    "YES = this alert looks worth reviewing. "
    "NO = this looks like noise, skip it. "
    "SKIP = insufficient information to decide (treat as YES)."
)


def _build_prompt(
    symbol: str,
    alert_type: str,
    trigger_price: float,
    bars_summary: str,
    note: str,
) -> str:
    return (
        f"Symbol: {symbol}\n"
        f"Alert type: {alert_type}\n"
        f"Trigger price: {trigger_price:.4g}\n"
        f"Recent price action:\n{bars_summary}\n"
        f"Detector note: {note}\n\n"
        "Should this alert be surfaced to the trader?"
    )


async def fast_check(
    symbol: str,
    alert_type: str,
    trigger_price: float,
    bars_summary: str,
    note: str,
    mock_mode: bool = True,
) -> str:
    """Returns 'YES', 'NO', or 'SKIP'."""
    if mock_mode:
        return "YES"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("No ANTHROPIC_API_KEY — fast check returning SKIP")
        return "SKIP"

    try:
        import anthropic  # type: ignore
        client = anthropic.AsyncAnthropic(api_key=api_key)
        prompt = _build_prompt(symbol, alert_type, trigger_price, bars_summary, note)
        response = await client.messages.create(
            model=FAST_CHECK_MODEL,
            max_tokens=FAST_CHECK_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip().upper()
        if text not in ("YES", "NO", "SKIP"):
            logger.warning("Unexpected fast check response: %r — treating as SKIP", text)
            return "SKIP"
        return text
    except Exception as e:
        logger.error("Fast check failed: %s — returning SKIP", e)
        return "SKIP"
