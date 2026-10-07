"""Alert de-duplication: suppresses repeated alerts for the same symbol+type
within a cooldown window."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

UTC = timezone.utc

# No re-alert for the same symbol+type until this many seconds have passed
_COOLDOWN_SECONDS: dict[str, int] = {
    "planned_entry": 900,
    "breakout": 600,
    "dip_to_support": 900,
    "breaking_news": 300,
    "position_pressure": 600,
}


class DedupRegistry:
    """In-memory store of last-fired times per (symbol, alert_type)."""

    def __init__(self) -> None:
        self._last_fired: dict[tuple[str, str], datetime] = {}

    def is_duplicate(
        self,
        symbol: str,
        alert_type: str,
        now: Optional[datetime] = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        key = (symbol.upper(), alert_type)
        last = self._last_fired.get(key)
        if last is None:
            return False
        cooldown = _COOLDOWN_SECONDS.get(alert_type, 600)
        return (now - last).total_seconds() < cooldown

    def record(
        self,
        symbol: str,
        alert_type: str,
        now: Optional[datetime] = None,
    ) -> None:
        now = now or datetime.now(UTC)
        self._last_fired[(symbol.upper(), alert_type)] = now

    def reset(self, symbol: str, alert_type: str) -> None:
        self._last_fired.pop((symbol.upper(), alert_type), None)
