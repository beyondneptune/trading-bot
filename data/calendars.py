"""Exchange calendar helpers.

All datetimes returned are timezone-aware.  UK time is Europe/London
(handles BST/GMT automatically).  The critical edge case documented
in SPEC.md section 3: UK clocks change Sun 26 Oct 2025 and Sun 25 Oct
2026; US clocks change Sun 2 Nov 2025 and Sun 1 Nov 2026.  During the
week between those dates the NYSE open is 13:30 UK time instead of
14:30.  exchange_calendars handles this automatically when you use
timezone-aware datetimes — the tests below verify it explicitly.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

UK_TZ = ZoneInfo("Europe/London")
NY_TZ = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def lse_calendar() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XLON")


def nyse_calendar() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS")


def is_lse_open(dt: datetime) -> bool:
    """True if LSE is open at the given timezone-aware datetime."""
    cal = lse_calendar()
    return bool(cal.is_open_on_minute(pd.Timestamp(dt)))


def is_nyse_open(dt: datetime) -> bool:
    cal = nyse_calendar()
    return bool(cal.is_open_on_minute(pd.Timestamp(dt)))


def lse_session(d: date) -> tuple[datetime, datetime] | None:
    """Return (open, close) in Europe/London time for LSE on date d.
    Returns None if LSE is closed that day."""
    cal = lse_calendar()
    ts = pd.Timestamp(d)
    if not cal.is_session(ts):
        return None
    schedule = cal.schedule.loc[ts]
    open_dt: datetime = schedule["open"].to_pydatetime()
    close_dt: datetime = schedule["close"].to_pydatetime()
    return open_dt.astimezone(UK_TZ), close_dt.astimezone(UK_TZ)


def nyse_session_in_uk_time(d: date) -> tuple[datetime, datetime] | None:
    """Return NYSE (open, close) expressed in Europe/London time.
    Returns None if NYSE is closed.

    During the week when UK clocks have gone back but US has not yet,
    this returns 13:30–20:00 UK time instead of the usual 14:30–21:00.
    """
    cal = nyse_calendar()
    ts = pd.Timestamp(d)
    if not cal.is_session(ts):
        return None
    schedule = cal.schedule.loc[ts]
    open_dt: datetime = schedule["open"].to_pydatetime()
    close_dt: datetime = schedule["close"].to_pydatetime()
    return open_dt.astimezone(UK_TZ), close_dt.astimezone(UK_TZ)


def is_pre_open_window(dt: datetime, exchange: str, minutes: int = 15) -> bool:
    """True if dt is within `minutes` of the session open (no-entry window)."""
    d = dt.date()
    session = lse_session(d) if exchange == "LSE" else nyse_session_in_uk_time(d)
    if session is None:
        return False
    open_dt, _ = session
    delta = (dt.astimezone(UK_TZ) - open_dt).total_seconds() / 60
    return 0 <= delta < minutes


def is_pre_close_window(dt: datetime, exchange: str, minutes: int = 10) -> bool:
    """True if dt is within `minutes` of the session close (no-entry window)."""
    d = dt.date()
    session = lse_session(d) if exchange == "LSE" else nyse_session_in_uk_time(d)
    if session is None:
        return False
    _, close_dt = session
    delta = (close_dt - dt.astimezone(UK_TZ)).total_seconds() / 60
    return 0 <= delta < minutes
