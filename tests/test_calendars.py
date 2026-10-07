"""Tests for data/calendars.py — exchange session helpers and DST edge cases.

Key edge case from SPEC.md section 3:
  UK clocks back  → Sun 25 Oct 2026 (BST→GMT)
  US clocks back  → Sun  1 Nov 2026 (EDT→EST)

  During Mon 26 Oct – Fri 30 Oct 2026, UK is on GMT (UTC+0) but
  US is still on EDT (UTC-4), so NYSE opens at 13:30 UK time instead
  of the usual 14:30.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from data.calendars import (
    UK_TZ,
    is_lse_open,
    is_nyse_open,
    is_pre_close_window,
    is_pre_open_window,
    lse_session,
    nyse_session_in_uk_time,
)


class TestLseSession:
    def test_weekday_has_session(self):
        session = lse_session(date(2025, 6, 2))  # Monday
        assert session is not None
        open_dt, close_dt = session
        assert open_dt.tzinfo is not None
        assert close_dt > open_dt

    def test_saturday_is_none(self):
        assert lse_session(date(2025, 6, 7)) is None  # Saturday

    def test_sunday_is_none(self):
        assert lse_session(date(2025, 6, 8)) is None  # Sunday

    def test_open_time_roughly_0800_uk(self):
        open_dt, _ = lse_session(date(2025, 6, 2))
        assert open_dt.astimezone(UK_TZ).hour == 8

    def test_close_time_roughly_1630_uk(self):
        _, close_dt = lse_session(date(2025, 6, 2))
        assert close_dt.astimezone(UK_TZ).hour == 16


class TestNyseSessionInUkTime:
    def test_normal_bst_period_opens_1430_uk(self):
        """During BST (summer), NYSE opens at 14:30 UK time (EDT = UTC-4, BST = UTC+1)."""
        d = date(2025, 6, 2)  # BST in effect
        session = nyse_session_in_uk_time(d)
        assert session is not None
        open_uk, _ = session
        local = open_uk.astimezone(UK_TZ)
        assert local.hour == 14
        assert local.minute == 30

    def test_normal_gmt_period_opens_1430_uk(self):
        """Normal winter: UK=GMT (UTC+0), US=EST (UTC-5) → 14:30 UK time."""
        d = date(2025, 12, 1)  # Both UK and US on standard time
        session = nyse_session_in_uk_time(d)
        assert session is not None
        open_uk, _ = session
        local = open_uk.astimezone(UK_TZ)
        assert local.hour == 14
        assert local.minute == 30

    def test_clock_change_week_opens_1330_uk(self):
        """The week of 26 Oct 2026: UK=GMT, US still EDT → NYSE opens 13:30 UK."""
        d = date(2026, 10, 27)  # Tuesday in that special week
        session = nyse_session_in_uk_time(d)
        assert session is not None
        open_uk, _ = session
        local = open_uk.astimezone(UK_TZ)
        assert local.hour == 13, (
            f"Expected 13:30 UK time during clock-change week, got {local.hour}:{local.minute:02d}"
        )
        assert local.minute == 30

    def test_after_us_clock_change_back_to_1430_uk(self):
        """After 2 Nov 2026 US clock change, back to 14:30 UK time."""
        d = date(2026, 11, 2)  # Monday after US clocks go back
        session = nyse_session_in_uk_time(d)
        assert session is not None
        open_uk, _ = session
        local = open_uk.astimezone(UK_TZ)
        assert local.hour == 14
        assert local.minute == 30

    def test_saturday_is_none(self):
        assert nyse_session_in_uk_time(date(2025, 6, 7)) is None

    def test_us_holiday_is_none(self):
        assert nyse_session_in_uk_time(date(2025, 7, 4)) is None  # Independence Day


class TestIsLseOpen:
    def test_open_during_session(self):
        # 10:00 London time on a weekday in summer
        dt = datetime(2025, 6, 2, 10, 0, tzinfo=UK_TZ)
        assert is_lse_open(dt) is True

    def test_closed_before_open(self):
        dt = datetime(2025, 6, 2, 7, 0, tzinfo=UK_TZ)
        assert is_lse_open(dt) is False

    def test_closed_on_weekend(self):
        dt = datetime(2025, 6, 7, 12, 0, tzinfo=UK_TZ)
        assert is_lse_open(dt) is False


class TestPreOpenWindow:
    def test_within_window(self):
        open_dt, _ = lse_session(date(2025, 6, 2))
        dt = open_dt + timedelta(minutes=5)
        assert is_pre_open_window(dt, "LSE", minutes=15) is True

    def test_outside_window(self):
        dt = datetime(2025, 6, 2, 10, 0, tzinfo=UK_TZ)
        assert is_pre_open_window(dt, "LSE", minutes=15) is False


class TestPreCloseWindow:
    def test_within_window(self):
        _, close_dt = lse_session(date(2025, 6, 2))
        dt = close_dt - timedelta(minutes=5)
        assert is_pre_close_window(dt, "LSE", minutes=10) is True

    def test_outside_window(self):
        dt = datetime(2025, 6, 2, 10, 0, tzinfo=UK_TZ)
        assert is_pre_close_window(dt, "LSE", minutes=10) is False
