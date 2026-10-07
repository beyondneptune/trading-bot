"""Fundamentals interface: earnings dates, basic valuation.

Behind an interface so the data source can be swapped later.
In mock_mode, returns plausible fake data.

The earnings rule (SPEC section 5, point 9):
  No new positions within 3 days before a company reports.
  A position held through results is sized at half.
  ETFs are exempt.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Protocol

from data.models import EarningsEvent

logger = logging.getLogger(__name__)

UTC = timezone.utc
EARNINGS_BLACKOUT_DAYS = 3


class FundamentalsProvider(Protocol):
    async def next_earnings(self, symbol: str, exchange: str) -> Optional[EarningsEvent]: ...
    async def pe_ratio(self, symbol: str, exchange: str) -> Optional[float]: ...


class MockFundamentalsProvider:
    """Returns plausible fake fundamentals for testing."""

    _NEXT_EARNINGS_OFFSET = {
        "AZN": 45, "SHEL": 60, "HSBA": 30, "BP": 55, "LSEG": 40,
        "AAPL": 20, "MSFT": 35, "GOOGL": 25, "AMZN": 15, "NVDA": 10,
    }
    _PE = {
        "AZN": 22.0, "SHEL": 8.0, "HSBA": 9.0, "AAPL": 28.0,
        "MSFT": 32.0, "GOOGL": 24.0, "AMZN": 45.0, "NVDA": 55.0,
    }

    async def next_earnings(
        self, symbol: str, exchange: str
    ) -> Optional[EarningsEvent]:
        days = self._NEXT_EARNINGS_OFFSET.get(symbol.upper(), 90)
        report_date = datetime.now(UTC) + timedelta(days=days)
        return EarningsEvent(
            symbol=symbol.upper(),
            exchange=exchange.upper(),
            report_date=report_date,
            confirmed=False,
        )

    async def pe_ratio(self, symbol: str, exchange: str) -> Optional[float]:
        return self._PE.get(symbol.upper())


def is_earnings_blackout(
    symbol: str,
    exchange: str,
    is_etf: bool,
    earnings: Optional[EarningsEvent],
    as_of: Optional[datetime] = None,
) -> bool:
    """True if we're within EARNINGS_BLACKOUT_DAYS of the next report.

    ETFs are always False (exempt from the earnings rule).
    """
    if is_etf:
        return False
    if earnings is None:
        return False
    if as_of is None:
        as_of = datetime.now(UTC)
    report = earnings.report_date
    if report.tzinfo is None:
        report = report.replace(tzinfo=UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    days_until = (report - as_of).days
    return 0 <= days_until <= EARNINGS_BLACKOUT_DAYS


def make_fundamentals_provider(ib_conn=None) -> FundamentalsProvider:
    from config.settings import load_settings
    settings = load_settings()
    if settings.mock_mode or ib_conn is None:
        return MockFundamentalsProvider()
    return MockFundamentalsProvider()  # real IB fundamentals in a later iteration
