"""Live data detection and streaming.

IB provides either live (real-time) or delayed (15-min delayed) data
depending on the user's market data subscriptions.  We must label every
price and disable intraday alerts for instruments with delayed data
(CLAUDE.md rule 12).

In mock_mode, all quotes are synthetic and labelled DataQuality.MOCK.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from data.mock_data import generate_quote
from data.models import DataQuality, PriceUnit, Quote
from instruments.units import InstrumentSpec

logger = logging.getLogger(__name__)

UTC = timezone.utc

# IB streams up to this many concurrent market-data lines before throttling.
# Keep watched symbols + open positions under this limit; rotate the rest.
IB_MAX_CONCURRENT_LINES = 100


class LiveDataError(RuntimeError):
    """Raised when required live data is unavailable."""


class DataFeedStatus:
    """Tracks the live/delayed status and staleness of a symbol's feed."""

    STALE_THRESHOLD = timedelta(seconds=30)

    def __init__(self, symbol: str, exchange: str) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.quality: DataQuality = DataQuality.DELAYED
        self.last_quote: Optional[Quote] = None
        self.last_updated: Optional[datetime] = None

    def update(self, quote: Quote) -> None:
        self.last_quote = quote
        self.quality = quote.quality
        self.last_updated = datetime.now(UTC)

    @property
    def is_stale(self) -> bool:
        if self.last_updated is None:
            return True
        return (datetime.now(UTC) - self.last_updated) > self.STALE_THRESHOLD

    @property
    def alerts_enabled(self) -> bool:
        """Intraday alerts only if data is live and fresh (rule 12)."""
        return self.quality == DataQuality.LIVE and not self.is_stale


class MockFeedManager:
    """Simulates live data feeds without connecting to IB."""

    def __init__(self, specs: list[InstrumentSpec]) -> None:
        self._specs = {(s.symbol, s.exchange): s for s in specs}
        self._statuses: dict[tuple[str, str], DataFeedStatus] = {}
        self._callbacks: list[Callable[[Quote], None]] = []

    def subscribe(self, symbol: str, exchange: str) -> None:
        key = (symbol, exchange)
        if key not in self._statuses:
            self._statuses[key] = DataFeedStatus(symbol, exchange)

    def on_quote(self, callback: Callable[[Quote], None]) -> None:
        self._callbacks.append(callback)

    async def stream(self, interval_seconds: float = 1.0) -> None:
        """Emit mock quotes at regular intervals."""
        tick = 0
        while True:
            for key, status in self._statuses.items():
                spec = self._specs.get(key)
                if spec is None:
                    continue
                quote = generate_quote(spec, seed=tick)
                status.update(quote)
                for cb in self._callbacks:
                    cb(quote)
            tick += 1
            await asyncio.sleep(interval_seconds)

    def get_quote(self, symbol: str, exchange: str) -> Optional[Quote]:
        status = self._statuses.get((symbol, exchange))
        if status:
            if status.last_quote is not None:
                return status.last_quote
            # Subscribed but stream not yet ticked — return a fresh mock quote
            spec = self._specs.get((symbol, exchange))
            if spec:
                return generate_quote(spec)
        spec = self._specs.get((symbol, exchange))
        if spec:
            return generate_quote(spec)
        return None

    def get_status(self, symbol: str, exchange: str) -> Optional[DataFeedStatus]:
        return self._statuses.get((symbol, exchange))


class LiveFeedManager:
    """Streams quotes from IB (delayed data on accounts without a live
    subscription). Call `await warmup()` once before scanning so the tickers
    have time to populate; then `get_quote()` reads the current values.

    IB quotes LSE instruments in PENCE. We convert to the instrument's native
    unit: GBX stays pence (factor 1); GBP is pounds (factor 1/priceMagnifier).
    """

    def __init__(self, ib_conn, specs: list[InstrumentSpec]) -> None:
        self._ib_conn = ib_conn
        self._specs = {(s.symbol, s.exchange): s for s in specs}
        self._statuses: dict[tuple[str, str], DataFeedStatus] = {}
        self._callbacks: list[Callable[[Quote], None]] = []
        self._tickers: dict[tuple[str, str], object] = {}
        self._factors: dict[tuple[str, str], float] = {}
        self._market_type_set = False

    def _ib(self):
        return self._ib_conn._ib if self._ib_conn is not None else None

    async def warmup(self, wait_seconds: int = 6) -> None:
        """Qualify contracts and start (delayed) market-data lines for every
        watchlist symbol, then wait briefly for first ticks."""
        ib = self._ib()
        if ib is None:
            return
        from ib_async import Stock
        ib.reqMarketDataType(3)  # delayed
        self._market_type_set = True
        for key, spec in self._specs.items():
            if key in self._tickers:
                continue
            contract = Stock(spec.symbol, "SMART", spec.currency, primaryExchange=spec.exchange)
            try:
                details = await ib.reqContractDetailsAsync(contract)
            except Exception as e:
                logger.warning("Feed: qualify failed for %s: %s", spec.symbol, e)
                continue
            if not details:
                continue
            contract = details[0].contract
            pm = details[0].priceMagnifier or 1
            self._factors[key] = (1.0 / pm) if spec.price_unit == PriceUnit.GBP else 1.0
            self._tickers[key] = ib.reqMktData(contract, "", snapshot=False)
            self._statuses[key] = DataFeedStatus(spec.symbol, spec.exchange)
        await asyncio.sleep(wait_seconds)
        logger.info("Feed warmed: %d market-data lines", len(self._tickers))

    def subscribe(self, symbol: str, exchange: str) -> None:
        # Real subscription happens in warmup(); this stays a cheap no-op so
        # the existing scan loop can call it per symbol without side effects.
        key = (symbol, exchange)
        if key not in self._statuses:
            self._statuses[key] = DataFeedStatus(symbol, exchange)

    def on_quote(self, callback: Callable[[Quote], None]) -> None:
        self._callbacks.append(callback)

    @staticmethod
    def _valid(x) -> Optional[float]:
        return float(x) if (x is not None and x == x and x > 0) else None

    def get_quote(self, symbol: str, exchange: str) -> Optional[Quote]:
        key = (symbol, exchange)
        ticker = self._tickers.get(key)
        if ticker is None:
            return None
        factor = self._factors.get(key, 1.0)
        spec = self._specs.get(key)
        last = self._valid(ticker.last) or self._valid(ticker.close)
        bid = self._valid(ticker.bid)
        ask = self._valid(ticker.ask)
        if last is None and bid and ask:
            last = (bid + ask) / 2
        if last is None:
            last = bid or ask
        if last is None:
            return None
        bid = bid or last
        ask = ask or last
        return Quote(
            symbol=symbol, exchange=exchange,
            bid=bid * factor, ask=ask * factor, last=last * factor,
            volume=float(self._valid(ticker.volume) or 0.0),
            price_unit=spec.price_unit if spec else PriceUnit.GBX,
            quality=DataQuality.DELAYED,
            ts=datetime.now(UTC),
        )

    def get_status(self, symbol: str, exchange: str) -> Optional[DataFeedStatus]:
        return self._statuses.get((symbol, exchange))


def make_feed_manager(ib_conn, specs: list[InstrumentSpec]):
    """Return MockFeedManager or LiveFeedManager based on settings."""
    from config.settings import load_settings
    settings = load_settings()
    if settings.mock_mode:
        return MockFeedManager(specs)
    return LiveFeedManager(ib_conn, specs)
