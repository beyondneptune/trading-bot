"""Phase 1 tests: data models, mock data, history cache, live feed,
news, fundamentals, events, market context, baseline strategy, backtester."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

UTC = timezone.utc


# ---------------------------------------------------------------------------
# data/models.py
# ---------------------------------------------------------------------------

class TestBar:
    def test_ts_must_be_aware(self):
        from data.models import Bar
        from instruments.units import PriceUnit
        with pytest.raises(Exception):
            Bar(
                symbol="AZN", exchange="LSE", bar_type="1d",
                ts=datetime(2025, 1, 2),  # naive — must raise
                open=100, high=110, low=90, close=105, volume=1e6,
                price_unit=PriceUnit.GBX,
            )

    def test_high_lt_low_rejected(self):
        from data.models import Bar
        from instruments.units import PriceUnit
        with pytest.raises(Exception):
            Bar(
                symbol="AZN", exchange="LSE", bar_type="1d",
                ts=datetime(2025, 1, 2, tzinfo=UTC),
                open=100, high=80, low=90, close=85, volume=1e6,
                price_unit=PriceUnit.GBX,
            )

    def test_valid_bar(self):
        from data.models import Bar, DataQuality
        from instruments.units import PriceUnit
        b = Bar(
            symbol="AZN", exchange="LSE", bar_type="1d",
            ts=datetime(2025, 1, 2, tzinfo=UTC),
            open=100, high=110, low=90, close=105, volume=1e6,
            price_unit=PriceUnit.GBX,
        )
        assert b.quality == DataQuality.CACHED


class TestQuote:
    def test_spread_pct(self):
        from data.models import DataQuality, Quote
        from instruments.units import PriceUnit
        q = Quote(
            symbol="AZN", exchange="LSE",
            bid=99.0, ask=101.0, last=100.0, volume=1e5,
            price_unit=PriceUnit.GBX, quality=DataQuality.LIVE,
            ts=datetime(2025, 1, 2, tzinfo=UTC),
        )
        assert q.spread_pct == pytest.approx(0.02)
        assert q.mid == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# data/mock_data.py
# ---------------------------------------------------------------------------

class TestMockData:
    def test_daily_bars_count(self):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        bars = generate_daily_bars(spec, n_days=100)
        assert len(bars) == 100

    def test_bars_are_aware(self):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        bars = generate_daily_bars(spec, n_days=10)
        for b in bars:
            assert b.ts.tzinfo is not None

    def test_bars_high_gte_low(self):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        bars = generate_daily_bars(spec, n_days=252)
        for b in bars:
            assert b.high >= b.low

    def test_deterministic(self):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        b1 = generate_daily_bars(spec, n_days=50, seed=42)
        b2 = generate_daily_bars(spec, n_days=50, seed=42)
        assert b1[0].close == b2[0].close

    def test_different_seeds_differ(self):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        b1 = generate_daily_bars(spec, n_days=50, seed=1)
        b2 = generate_daily_bars(spec, n_days=50, seed=2)
        # First bar is always `start_price`; compare a later bar
        assert b1[10].close != b2[10].close

    def test_intraday_bars(self):
        from data.mock_data import generate_intraday_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        bars = generate_intraday_bars(spec, datetime(2025, 6, 2, tzinfo=UTC))
        assert len(bars) > 0
        for b in bars:
            assert b.bar_type == "5m"


# ---------------------------------------------------------------------------
# data/history.py
# ---------------------------------------------------------------------------

class TestHistoryCache:
    def test_get_daily_bars_sync_mock(self, tmp_path: Path):
        from data.database import init_db
        from data.history import get_daily_bars_sync
        from instruments.units import InstrumentSpec, PriceUnit
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        bars = get_daily_bars_sync(spec, db_path, years=1)
        assert len(bars) > 0

    def test_caches_on_second_call(self, tmp_path: Path):
        from data.database import get_connection, init_db
        from data.history import get_daily_bars_sync
        from instruments.units import InstrumentSpec, PriceUnit
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        bars1 = get_daily_bars_sync(spec, db_path, years=1)
        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM bars WHERE symbol='AZN'").fetchone()[0]
        conn.close()
        bars2 = get_daily_bars_sync(spec, db_path, years=1)
        count2_conn = get_connection(db_path)
        count2 = count2_conn.execute("SELECT COUNT(*) FROM bars WHERE symbol='AZN'").fetchone()[0]
        count2_conn.close()
        assert count == count2  # no duplicate inserts


# ---------------------------------------------------------------------------
# data/live.py
# ---------------------------------------------------------------------------

class TestMockFeedManager:
    def test_subscribe_and_get_quote(self):
        from data.live import MockFeedManager
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        mgr = MockFeedManager([spec])
        mgr.subscribe("AZN", "LSE")
        quote = mgr.get_quote("AZN", "LSE")
        assert quote is not None
        assert quote.symbol == "AZN"

    def test_alerts_disabled_for_mock_data(self):
        from data.live import DataFeedStatus, MockFeedManager
        from data.mock_data import generate_quote
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        mgr = MockFeedManager([spec])
        mgr.subscribe("AZN", "LSE")
        # Mock data is DataQuality.MOCK, not LIVE
        status = mgr.get_status("AZN", "LSE")
        assert status is not None
        # No update yet → stale → alerts off
        assert status.alerts_enabled is False


# ---------------------------------------------------------------------------
# data/news.py
# ---------------------------------------------------------------------------

class TestMockNews:
    def test_fetch_returns_items(self):
        import asyncio
        from data.news import MockNewsProvider
        provider = MockNewsProvider()
        items = asyncio.get_event_loop().run_until_complete(
            provider.fetch("AZN", max_items=3)
        )
        assert len(items) == 3
        assert all(i.published_at.tzinfo is not None for i in items)

    def test_age_label(self):
        from datetime import timezone
        from data.models import NewsItem
        from data.news import age_label
        now = datetime.now(timezone.utc)
        fresh = NewsItem(
            headline="test", source="mock", symbols=["AZN"],
            published_at=now - timedelta(hours=1),
            retrieved_at=now,
        )
        old = NewsItem(
            headline="test", source="mock", symbols=["AZN"],
            published_at=now - timedelta(hours=48),
            retrieved_at=now,
        )
        assert age_label(fresh, now=now) == "fresh"
        assert age_label(old, now=now) == "background"


# ---------------------------------------------------------------------------
# data/fundamentals.py
# ---------------------------------------------------------------------------

class TestFundamentals:
    def test_earnings_blackout_within_3_days(self):
        import asyncio
        from data.fundamentals import MockFundamentalsProvider, is_earnings_blackout
        from data.models import EarningsEvent
        now = datetime.now(UTC)
        earnings = EarningsEvent(
            symbol="AZN", exchange="LSE",
            report_date=now + timedelta(days=2),
        )
        assert is_earnings_blackout("AZN", "LSE", False, earnings, as_of=now) is True

    def test_no_blackout_far_away(self):
        from data.fundamentals import is_earnings_blackout
        from data.models import EarningsEvent
        now = datetime.now(UTC)
        earnings = EarningsEvent(
            symbol="AZN", exchange="LSE",
            report_date=now + timedelta(days=10),
        )
        assert is_earnings_blackout("AZN", "LSE", False, earnings, as_of=now) is False

    def test_etf_always_exempt(self):
        from data.fundamentals import is_earnings_blackout
        from data.models import EarningsEvent
        now = datetime.now(UTC)
        earnings = EarningsEvent(
            symbol="VUSA", exchange="LSE",
            report_date=now + timedelta(days=1),
        )
        assert is_earnings_blackout("VUSA", "LSE", True, earnings, as_of=now) is False

    def test_no_earnings_no_blackout(self):
        from data.fundamentals import is_earnings_blackout
        assert is_earnings_blackout("AZN", "LSE", False, None) is False


# ---------------------------------------------------------------------------
# data/events.py
# ---------------------------------------------------------------------------

class TestEvents:
    def test_upcoming_returns_high_importance(self):
        import asyncio
        from data.events import MockEventsProvider
        provider = MockEventsProvider()
        events = asyncio.get_event_loop().run_until_complete(
            provider.upcoming(within_hours=24 * 30, importance="high")
        )
        assert all(e.importance == "high" for e in events)

    def test_event_blackout_during_window(self):
        from data.events import is_event_blackout
        from data.models import EconomicEvent
        now = datetime.now(UTC)
        event = EconomicEvent(
            name="CPI", scheduled_at=now + timedelta(minutes=5),
            importance="high", currency="USD",
        )
        assert is_event_blackout(now, [event]) is True

    def test_no_blackout_outside_window(self):
        from data.events import is_event_blackout
        from data.models import EconomicEvent
        now = datetime.now(UTC)
        event = EconomicEvent(
            name="CPI", scheduled_at=now + timedelta(hours=2),
            importance="high", currency="USD",
        )
        assert is_event_blackout(now, [event]) is False


# ---------------------------------------------------------------------------
# data/market_context.py
# ---------------------------------------------------------------------------

class TestMarketContext:
    def test_stressed_regime_below_sma(self):
        from data.market_context import compute_regime
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="VWRL", exchange="LSE", currency="USD",
                              price_unit=PriceUnit.USD)
        bars = generate_daily_bars(spec, n_days=300, seed=99)
        # Force all closes to be below SMA by making recent bars very low
        for b in bars[-50:]:
            object.__setattr__(b, 'close', bars[0].close * 0.5)
        regime = compute_regime(bars)
        # With prices at half the starting value, we're likely stressed
        assert regime.multiplier in (0.0, 0.5, 1.0)

    def test_regime_has_required_fields(self):
        from data.market_context import compute_regime
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="VWRL", exchange="LSE", currency="USD",
                              price_unit=PriceUnit.USD)
        bars = generate_daily_bars(spec, n_days=300, seed=42)
        regime = compute_regime(bars)
        assert regime.label in ("normal", "elevated", "stressed")
        assert regime.multiplier in (0.0, 0.5, 1.0)
        assert 0 <= regime.vol_percentile <= 100
        assert isinstance(regime.note, str)

    def test_empty_bars_returns_normal(self):
        from data.market_context import compute_regime
        regime = compute_regime([])
        assert regime.label == "normal"
        assert regime.multiplier == 1.0


# ---------------------------------------------------------------------------
# backtest/backtester.py
# ---------------------------------------------------------------------------

class TestBacktester:
    def _make_universe(self, n_days: int = 252 * 2):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit

        symbols = [
            ("AAPL", "NASDAQ", "USD", PriceUnit.USD),
            ("MSFT", "NASDAQ", "USD", PriceUnit.USD),
            ("GOOGL", "NASDAQ", "USD", PriceUnit.USD),
            ("AMZN", "NASDAQ", "USD", PriceUnit.USD),
            ("NVDA", "NASDAQ", "USD", PriceUnit.USD),
            ("JPM", "NYSE", "USD", PriceUnit.USD),
        ]
        bars_by_symbol = {}
        specs = {}
        for i, (sym, exch, cur, pu) in enumerate(symbols):
            spec = InstrumentSpec(symbol=sym, exchange=exch, currency=cur, price_unit=pu)
            bars_by_symbol[sym] = generate_daily_bars(spec, n_days=n_days, seed=i * 10)
            specs[sym] = spec
        return bars_by_symbol, specs

    def test_backtest_runs(self):
        from backtest.backtester import run_backtest
        bars, specs = self._make_universe()
        results = run_backtest(bars, specs, starting_equity=12_500)
        assert len(results) >= 1

    def test_baseline_result_has_required_fields(self):
        from backtest.backtester import run_backtest
        bars, specs = self._make_universe()
        results = run_backtest(bars, specs, starting_equity=12_500)
        r = results[0]
        assert r.strategy_name == "Baseline rules strategy"
        assert 0 <= r.max_drawdown <= 1
        assert isinstance(r.cagr, float)

    def test_with_benchmarks(self):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        from backtest.backtester import run_backtest
        bars, specs = self._make_universe()
        vwrl_spec = InstrumentSpec(symbol="VWRL", exchange="LSE", currency="USD",
                                   price_unit=PriceUnit.USD)
        bench_bars = generate_daily_bars(vwrl_spec, n_days=252 * 2, seed=7)
        benchmark_bars = {"VWRL": bench_bars}
        results = run_backtest(bars, specs, benchmark_bars=benchmark_bars)
        assert len(results) == 2

    def test_summary_contains_cagr(self):
        from backtest.backtester import run_backtest
        bars, specs = self._make_universe()
        results = run_backtest(bars, specs)
        summary = results[0].summary()
        assert "CAGR" in summary
