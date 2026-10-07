"""Economic/event calendar interface.

Used for no-entry blackout windows around major releases (CPI, FOMC, jobs).
In mock_mode, returns a handful of plausible upcoming events.
Real sources to wire up (propose to user): Investing.com calendar via
scraping/API, or TradingEconomics free tier.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from data.models import EconomicEvent

logger = logging.getLogger(__name__)

UTC = timezone.utc

# Blackout window: no new entries this many minutes either side of a
# high-importance event.  Configurable; kept here for Phase 1.
BLACKOUT_MINUTES_BEFORE = 15
BLACKOUT_MINUTES_AFTER = 30


class EventsProvider(Protocol):
    async def upcoming(
        self,
        within_hours: int,
        importance: str,  # "high" | "medium" | "low"
        currencies: list[str],
    ) -> list[EconomicEvent]: ...


class MockEventsProvider:
    """Returns a fixed set of mock economic events for testing."""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self._events: list[EconomicEvent] = [
            EconomicEvent(
                name="US CPI",
                scheduled_at=now + timedelta(days=5, hours=13, minutes=30),
                importance="high",
                currency="USD",
                forecast="3.2%",
                previous="3.1%",
            ),
            EconomicEvent(
                name="Bank of England Rate Decision",
                scheduled_at=now + timedelta(days=10, hours=12),
                importance="high",
                currency="GBP",
            ),
            EconomicEvent(
                name="US Non-Farm Payrolls",
                scheduled_at=now + timedelta(days=4, hours=13, minutes=30),
                importance="high",
                currency="USD",
            ),
            EconomicEvent(
                name="UK GDP",
                scheduled_at=now + timedelta(hours=2),
                importance="medium",
                currency="GBP",
            ),
        ]

    async def upcoming(
        self,
        within_hours: int = 24,
        importance: str = "high",
        currencies: list[str] | None = None,
    ) -> list[EconomicEvent]:
        now = datetime.now(UTC)
        cutoff = now + timedelta(hours=within_hours)
        results = []
        for ev in self._events:
            if ev.scheduled_at > cutoff:
                continue
            if importance == "high" and ev.importance != "high":
                continue
            if currencies and ev.currency not in currencies:
                continue
            results.append(ev)
        return results


def is_event_blackout(
    dt: datetime,
    events: list[EconomicEvent],
    currencies: list[str] | None = None,
) -> bool:
    """True if dt falls within a blackout window of any high-importance event."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    for ev in events:
        if ev.importance != "high":
            continue
        if currencies and ev.currency not in currencies:
            continue
        sched = ev.scheduled_at
        if sched.tzinfo is None:
            sched = sched.replace(tzinfo=UTC)
        window_start = sched - timedelta(minutes=BLACKOUT_MINUTES_BEFORE)
        window_end = sched + timedelta(minutes=BLACKOUT_MINUTES_AFTER)
        if window_start <= dt <= window_end:
            return True
    return False


def make_events_provider() -> EventsProvider:
    return MockEventsProvider()
