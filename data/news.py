"""News interface: IB news API + web-search stub.

The real implementation (Phase 3) will wire up Anthropic web search.
This module provides the interface and mock/IB stubs so other modules
can import and call it now.

All news items are timestamped.  Items older than 24 hours are treated
as background only (labelled in materiality = "background").
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from data.mock_data import generate_news
from data.models import NewsItem

logger = logging.getLogger(__name__)

UTC = timezone.utc
BACKGROUND_AGE = timedelta(hours=24)


class NewsProvider(Protocol):
    async def fetch(self, symbol: str, max_items: int) -> list[NewsItem]: ...


class MockNewsProvider:
    async def fetch(self, symbol: str, max_items: int = 5) -> list[NewsItem]:
        return generate_news(symbol, n=max_items)


class IBNewsProvider:
    """Fetches news via IB's news API (whatever free providers the account has)."""

    def __init__(self, ib_conn) -> None:
        self._ib = ib_conn

    async def fetch(self, symbol: str, max_items: int = 10) -> list[NewsItem]:
        try:
            from ib_async import Stock  # type: ignore[import]
            contract = Stock(symbol, "SMART", "USD")
            headlines = await self._ib.ib.reqNewsArticlesAsync(
                contract, providerCodes="BRFG+DJNL"
            )
            now = datetime.now(UTC)
            items = []
            for h in headlines[:max_items]:
                pub = getattr(h, "time", now)
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=UTC)
                items.append(
                    NewsItem(
                        headline=h.headline,
                        source=getattr(h, "providerCode", "IB"),
                        symbols=[symbol],
                        published_at=pub,
                        retrieved_at=now,
                    )
                )
            return items
        except Exception as exc:
            logger.warning("IB news fetch failed for %s: %s", symbol, exc)
            return []


def age_label(item: NewsItem, now: datetime | None = None) -> str:
    """Return 'fresh' or 'background' based on age."""
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    pub = item.published_at
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=UTC)
    return "background" if (now - pub) > BACKGROUND_AGE else "fresh"


def make_news_provider(ib_conn=None) -> NewsProvider:
    from config.settings import load_settings
    settings = load_settings()
    if settings.mock_mode or ib_conn is None:
        return MockNewsProvider()
    return IBNewsProvider(ib_conn)
