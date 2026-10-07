"""Deterministic mock data generator for testing and demo runs.

Generates realistic-looking OHLCV bars using a seeded geometric Brownian
motion so tests are reproducible.  All returned datetimes are UTC-aware.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Sequence

from data.models import Bar, DataQuality, NewsItem, Quote
from instruments.units import InstrumentSpec, PriceUnit

UTC = timezone.utc


def _gbm_prices(
    start: float,
    n: int,
    mu: float = 0.0001,
    sigma: float = 0.015,
    seed: int = 42,
) -> list[float]:
    """Geometric Brownian Motion close prices."""
    rng = random.Random(seed)
    prices = [start]
    for _ in range(n - 1):
        z = rng.gauss(0, 1)
        prices.append(prices[-1] * math.exp((mu - 0.5 * sigma**2) + sigma * z))
    return prices


def generate_daily_bars(
    spec: InstrumentSpec,
    n_days: int = 252 * 3,
    end_date: datetime | None = None,
    seed: int = 42,
) -> list[Bar]:
    """Generate n_days of synthetic daily OHLCV bars."""
    if end_date is None:
        end_date = datetime(2026, 9, 30, 16, 30, tzinfo=UTC)

    start_price = 7_000.0 if spec.price_unit == PriceUnit.GBX else 100.0

    closes = _gbm_prices(start_price, n_days, seed=seed)

    bars: list[Bar] = []
    rng = random.Random(seed + 1)
    day = end_date - timedelta(days=n_days - 1)

    for i, close in enumerate(closes):
        intraday_range = close * rng.uniform(0.005, 0.025)
        high = close + intraday_range * rng.uniform(0.3, 1.0)
        low = close - intraday_range * rng.uniform(0.3, 1.0)
        open_ = low + (high - low) * rng.random()
        volume = rng.randint(500_000, 5_000_000)

        ts = day + timedelta(days=i)
        ts = ts.replace(hour=16, minute=30, second=0, microsecond=0, tzinfo=UTC)

        bars.append(
            Bar(
                symbol=spec.symbol,
                exchange=spec.exchange,
                bar_type="1d",
                ts=ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=float(volume),
                price_unit=spec.price_unit,
                quality=DataQuality.MOCK,
            )
        )
    return bars


def generate_intraday_bars(
    spec: InstrumentSpec,
    date: datetime,
    bar_size_minutes: int = 5,
    seed: int = 99,
) -> list[Bar]:
    """Generate one day's intraday bars."""
    open_time = date.replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=UTC)
    close_time = date.replace(hour=16, minute=30, second=0, microsecond=0, tzinfo=UTC)

    n = int((close_time - open_time).total_seconds() / 60 / bar_size_minutes)
    start_price = 7_000.0 if spec.price_unit == PriceUnit.GBX else 100.0
    closes = _gbm_prices(start_price, n, sigma=0.003, seed=seed)

    bars: list[Bar] = []
    rng = random.Random(seed + 1)
    for i, close in enumerate(closes):
        ts = open_time + timedelta(minutes=i * bar_size_minutes)
        r = close * 0.003
        high = close + r * rng.random()
        low = close - r * rng.random()
        open_ = low + (high - low) * rng.random()
        bars.append(
            Bar(
                symbol=spec.symbol,
                exchange=spec.exchange,
                bar_type=f"{bar_size_minutes}m",
                ts=ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=float(rng.randint(10_000, 200_000)),
                price_unit=spec.price_unit,
                quality=DataQuality.MOCK,
            )
        )
    return bars


def generate_quote(spec: InstrumentSpec, seed: int = 0) -> Quote:
    rng = random.Random(seed)
    mid = 7_000.0 if spec.price_unit == PriceUnit.GBX else 100.0
    mid *= 1 + rng.gauss(0, 0.01)
    spread = mid * 0.001
    return Quote(
        symbol=spec.symbol,
        exchange=spec.exchange,
        bid=mid - spread / 2,
        ask=mid + spread / 2,
        last=mid,
        volume=float(rng.randint(100_000, 1_000_000)),
        price_unit=spec.price_unit,
        quality=DataQuality.MOCK,
        ts=datetime(2026, 9, 30, 10, 0, tzinfo=UTC),
    )


def generate_news(symbol: str, n: int = 5, seed: int = 0) -> list[NewsItem]:
    rng = random.Random(seed)
    headlines = [
        f"{symbol} reports strong quarterly earnings",
        f"{symbol} announces new strategic partnership",
        f"Analyst upgrades {symbol} to Buy",
        f"{symbol} faces regulatory scrutiny",
        f"Sector rotation benefits {symbol}",
        f"{symbol} CEO comments on macro outlook",
        f"{symbol} raises full-year guidance",
    ]
    now = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
    items = []
    for i in range(n):
        published = now - timedelta(hours=rng.randint(1, 72))
        items.append(
            NewsItem(
                headline=rng.choice(headlines),
                source="mock",
                symbols=[symbol],
                published_at=published,
                retrieved_at=now,
                materiality=rng.choice(["low", "medium", "high"]),
            )
        )
    return items
