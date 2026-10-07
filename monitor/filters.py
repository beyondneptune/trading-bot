"""Alert filters: conditions that suppress an alert before it reaches the
fast AI check.

Each filter returns (pass: bool, reason: str). An alert is suppressed if ANY
filter returns pass=False.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from data.events import is_event_blackout
from data.models import DataQuality
from data.calendars import is_lse_open, is_nyse_open
from instruments.units import InstrumentSpec

if TYPE_CHECKING:
    from data.models import EarningsEvent, EconomicEvent


def filter_market_closed(spec: InstrumentSpec) -> tuple[bool, str]:
    """Suppress if the home market is not open."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if spec.exchange in ("LSE", "XLON"):
        if not is_lse_open(now):
            return False, "LSE not open"
    elif spec.exchange in ("NASDAQ", "NYSE", "XNAS", "XNYS"):
        if not is_nyse_open(now):
            return False, "NYSE not open"
    return True, ""


def filter_earnings_blackout(
    spec: InstrumentSpec,
    earnings: Optional["EarningsEvent"] = None,
) -> tuple[bool, str]:
    from data.fundamentals import is_earnings_blackout, EARNINGS_BLACKOUT_DAYS
    # is_etf=False is conservative: ETFs have earnings=None so the call returns False anyway
    if is_earnings_blackout(spec.symbol, spec.exchange, False, earnings):
        return False, f"earnings blackout ({EARNINGS_BLACKOUT_DAYS}d)"
    return True, ""


def filter_event_blackout(
    spec: InstrumentSpec,
    events: list | None = None,
) -> tuple[bool, str]:
    from datetime import datetime, timezone
    if events and is_event_blackout(datetime.now(timezone.utc), events):
        return False, "economic event blackout"
    return True, ""


def filter_data_quality(
    quality: DataQuality,
    allow_delayed: bool = False,
) -> tuple[bool, str]:
    """Suppress if data is MOCK or STALE (intraday decisions need real prices)."""
    if quality == DataQuality.MOCK:
        return False, "mock data — intraday alert suppressed"
    if quality == DataQuality.CACHED:
        return False, "cached data — price may be stale"
    if quality == DataQuality.DELAYED and not allow_delayed:
        return False, "delayed data — not suitable for intraday"
    return True, ""


def filter_spread_too_wide(
    spread_pct: float,
    max_spread_pct: float = 0.005,
) -> tuple[bool, str]:
    if spread_pct > max_spread_pct:
        return False, f"spread {spread_pct:.3%} > {max_spread_pct:.3%}"
    return True, ""


def run_all_filters(
    spec: InstrumentSpec,
    quality: DataQuality,
    spread_pct: float,
    earnings: Optional["EarningsEvent"] = None,
    allow_delayed: bool = False,
    events: list | None = None,
) -> tuple[bool, str]:
    """Run all filters in order. Returns (pass, first_fail_reason)."""
    checks = [
        filter_market_closed(spec),
        filter_data_quality(quality, allow_delayed),
        filter_spread_too_wide(spread_pct),
        filter_earnings_blackout(spec, earnings),
        filter_event_blackout(spec, events),
    ]
    for ok, reason in checks:
        if not ok:
            return False, reason
    return True, ""
