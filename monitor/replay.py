"""Replay harness: runs the intraday monitor's detectors over historical bars
to audit which alerts would have fired on a given date.

Usage:
    from monitor.replay import replay_session
    alerts = replay_session("AZN", "LSE", date(2026, 9, 15), db_path="bot.db")

The replay does NOT invoke the fast AI check or write to the DB — it only
exercises the detectors so you can verify alert logic on historical data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import Optional, Sequence

from data.models import Bar
from monitor.alerts import Alert
from monitor.detectors import (
    detect_breakout,
    detect_dip_to_support,
    detect_position_pressure,
)

logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass
class ReplayResult:
    symbol: str
    exchange: str
    replay_date: date
    bars_used: int
    alerts: list[Alert]

    @property
    def alert_count(self) -> int:
        return len(self.alerts)

    @property
    def types(self) -> list[str]:
        return [a.alert_type.value for a in self.alerts]


def replay_session(
    symbol: str,
    exchange: str,
    replay_date: date,
    bars: Sequence[Bar],
    stop_price: Optional[float] = None,
    target_price: Optional[float] = None,
    lookback: int = 20,
) -> ReplayResult:
    """Replay detectors for a given day using the provided bar sequence.

    `bars` should include all bars UP TO AND INCLUDING those on `replay_date`,
    ordered oldest-first. Typically you pass 60–252 days of daily bars.
    """
    # Find bars up to replay_date
    cutoff_ts = datetime.combine(replay_date, datetime.min.time()).replace(tzinfo=UTC)
    eligible = [b for b in bars if b.ts.date() <= replay_date]

    if not eligible:
        logger.warning("No bars available for %s on %s", symbol, replay_date)
        return ReplayResult(symbol, exchange, replay_date, 0, [])

    alerts: list[Alert] = []

    # Breakout
    a = detect_breakout(symbol, exchange, eligible, lookback=lookback)
    if a:
        alerts.append(a)

    # Dip to support
    a = detect_dip_to_support(symbol, exchange, eligible, lookback=lookback)
    if a:
        alerts.append(a)

    # Position pressure (if levels provided)
    if stop_price is not None and target_price is not None:
        current_price = eligible[-1].close
        a = detect_position_pressure(
            symbol, exchange, current_price, stop_price, target_price
        )
        if a:
            alerts.append(a)

    return ReplayResult(
        symbol=symbol,
        exchange=exchange,
        replay_date=replay_date,
        bars_used=len(eligible),
        alerts=alerts,
    )


def replay_date_range(
    symbol: str,
    exchange: str,
    start_date: date,
    end_date: date,
    bars: Sequence[Bar],
    lookback: int = 20,
) -> list[ReplayResult]:
    """Replay every trading day in [start_date, end_date]."""
    results = []
    current = start_date
    while current <= end_date:
        result = replay_session(symbol, exchange, current, bars, lookback=lookback)
        if result.bars_used > 0:
            results.append(result)
        current += timedelta(days=1)
    return results
