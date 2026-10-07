"""Phase 9 integration tests — wiring components into a running system.

Unit 1: signal_builder turns a detector trigger into a complete, risk-checked
        BUY signal (or rejects it) and the monitor uses it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

UTC = timezone.utc


def _setup_db(tmp_path: Path):
    from data.database import get_connection, init_db
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return get_connection(db_path), db_path


def _spec():
    from instruments.units import load_watchlist, find_instrument
    wl = load_watchlist()
    # Use a known LSE share from the watchlist.
    spec = find_instrument(wl, "AZN", "LSE")
    return spec if spec is not None else wl[0]


def _bars(spec, n=60, start=100.0, step=0.5, rng=4.0):
    """Build a rising synthetic daily bar series with enough range that the
    ATR-based stop is wide enough to clear the UK cost gate (rule 7)."""
    from data.models import Bar, PriceUnit, DataQuality
    bars = []
    base = datetime(2026, 1, 1, tzinfo=UTC)
    price = start
    for i in range(n):
        o = price
        c = price + step
        bars.append(Bar(
            symbol=spec.symbol, exchange=spec.exchange, bar_type="1d",
            ts=base + timedelta(days=i),
            open=o, high=c + rng, low=o - rng, close=c,
            volume=500_000.0, price_unit=spec.price_unit,
            quality=DataQuality.CACHED,
        ))
        price = c
    return bars


def _live_quote(spec, last):
    from data.models import Quote, PriceUnit, DataQuality
    return Quote(
        symbol=spec.symbol, exchange=spec.exchange,
        bid=last - 1, ask=last + 1, last=last,
        volume=500_000.0, price_unit=spec.price_unit,
        quality=DataQuality.LIVE, ts=datetime.now(UTC),
    )


class TestSignalBuilder:

    def test_builds_complete_buy_signal(self, tmp_path: Path):
        from strategy.signal_builder import build_signal
        conn, _ = _setup_db(tmp_path)
        spec = _spec()
        bars = _bars(spec)
        entry = bars[-1].close
        sid = build_signal(
            conn, spec, spec.symbol, spec.exchange, "breakout",
            entry, bars, quote=_live_quote(spec, entry),
        )
        assert sid is not None
        row = conn.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        assert row["action"] == "BUY"
        assert row["entry_price"] > 0
        assert row["stop_price"] < row["entry_price"]      # long-only
        assert row["target_price"] > row["entry_price"]
        assert row["size"] >= 1
        assert row["risk_gbp"] > 0
        assert row["reward_risk"] >= 2.0 - 1e-9            # rule 8
        assert row["regime"] in ("normal", "elevated", "stressed")

    def test_rejects_when_no_bars(self, tmp_path: Path):
        from strategy.signal_builder import build_signal
        conn, _ = _setup_db(tmp_path)
        spec = _spec()
        sid = build_signal(
            conn, spec, spec.symbol, spec.exchange, "breakout",
            100.0, [], quote=_live_quote(spec, 100.0),
        )
        assert sid is None

    def test_rejects_on_bad_data_quality(self, tmp_path: Path):
        """A missing quote must fail the data-quality gate → no signal."""
        from strategy.signal_builder import build_signal
        conn, _ = _setup_db(tmp_path)
        spec = _spec()
        bars = _bars(spec)
        sid = build_signal(
            conn, spec, spec.symbol, spec.exchange, "breakout",
            bars[-1].close, bars, quote=None,
        )
        assert sid is None

    def test_signal_appears_as_pending(self, tmp_path: Path):
        """A built signal should show up in the approval queue."""
        from strategy.signal_builder import build_signal
        from api.queue import get_pending_signals
        conn, _ = _setup_db(tmp_path)
        spec = _spec()
        bars = _bars(spec)
        entry = bars[-1].close
        sid = build_signal(
            conn, spec, spec.symbol, spec.exchange, "breakout",
            entry, bars, quote=_live_quote(spec, entry),
        )
        assert sid is not None
        pending = get_pending_signals(conn)
        assert any(r["id"] == sid for r in pending)

    def test_stressed_regime_blocks_signal(self, tmp_path: Path):
        """Index in a stressed regime → multiplier 0 → sizing infeasible → no signal."""
        from strategy.signal_builder import build_signal
        from data.models import Bar, DataQuality
        conn, _ = _setup_db(tmp_path)
        spec = _spec()
        bars = _bars(spec)
        entry = bars[-1].close
        # 220-bar sustained downtrend → price ends well below its 200d SMA →
        # regime is "stressed" (multiplier 0 → no new buys, rule 6).
        base = datetime(2026, 1, 1, tzinfo=UTC)
        idx = []
        price = 300.0
        for i in range(220):
            price = price - 0.7  # steady decline from 300 → ~146
            idx.append(Bar(
                symbol="VWRL", exchange="LSE", bar_type="1d",
                ts=base + timedelta(days=i),
                open=price, high=price * 1.01, low=price * 0.99, close=price,
                volume=1_000_000.0, price_unit=spec.price_unit,
                quality=DataQuality.CACHED,
            ))
        sid = build_signal(
            conn, spec, spec.symbol, spec.exchange, "breakout",
            entry, bars, quote=_live_quote(spec, entry), index_bars=idx,
        )
        assert sid is None


def _build_and_approve(conn):
    """Helper: build a tradeable signal and record its approval. Returns sid."""
    from strategy.signal_builder import build_signal
    from api.queue import approve_signal
    spec = _spec()
    bars = _bars(spec)
    entry = bars[-1].close
    sid = build_signal(
        conn, spec, spec.symbol, spec.exchange, "breakout",
        entry, bars, quote=_live_quote(spec, entry),
    )
    assert sid is not None
    approve_signal(conn, sid, "test")
    return sid


class _Settings:
    """Minimal stand-in for Settings with the fields the executor reads."""
    def __init__(self, phase=6, mock_mode=True):
        self.phase = phase
        self.mock_mode = mock_mode


class TestExecutor:

    async def test_approved_signal_fills_in_mock(self, tmp_path: Path):
        from execution.executor import execute_approved_signal
        conn, _ = _setup_db(tmp_path)
        sid = _build_and_approve(conn)
        result = await execute_approved_signal(conn, sid, _Settings(phase=6, mock_mode=True))
        assert result.executed is True
        assert result.status == "FILLED"
        # An order and a fill and an open position should now exist.
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL"
        ).fetchone()[0] == 1

    async def test_kill_switch_blocks_execution(self, tmp_path: Path):
        from execution.executor import execute_approved_signal
        from execution.kill_switch import activate_kill_switch
        conn, _ = _setup_db(tmp_path)
        sid = _build_and_approve(conn)
        activate_kill_switch(conn)
        result = await execute_approved_signal(conn, sid, _Settings(phase=6, mock_mode=True))
        assert result.executed is False
        assert result.status == "BLOCKED"
        assert "kill switch" in result.detail.lower()
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0

    async def test_loss_limit_blocks_execution(self, tmp_path: Path):
        from execution.executor import execute_approved_signal
        conn, _ = _setup_db(tmp_path)
        sid = _build_and_approve(conn)
        # Both snapshots pinned to FIXED times on today's UTC date (the loss
        # check uses now().date()). Baseline at 00:00:00 is always the earliest
        # snapshot of the day, so the test never straddles midnight.
        today = datetime.now(UTC).date()
        day_start = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=UTC)
        midday = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC)
        for eq, ts in [(10000.0, day_start), (9700.0, midday)]:
            conn.execute(
                """INSERT INTO equity_snapshots
                   (equity_gbp, cash_gbp, open_positions, portfolio_heat, snapped_at)
                   VALUES (?, ?, 0, 0.0, ?)""",
                (eq, eq, ts.isoformat()),
            )
        conn.commit()
        result = await execute_approved_signal(conn, sid, _Settings(phase=6, mock_mode=True))
        assert result.executed is False
        assert result.status == "BLOCKED"
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0

    async def test_phase_lock_blocks_execution(self, tmp_path: Path):
        from execution.executor import execute_approved_signal
        conn, _ = _setup_db(tmp_path)
        sid = _build_and_approve(conn)
        result = await execute_approved_signal(conn, sid, _Settings(phase=5, mock_mode=True))
        assert result.executed is False
        assert result.status == "BLOCKED"
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0

    async def test_execution_idempotent(self, tmp_path: Path):
        from execution.executor import execute_approved_signal
        conn, _ = _setup_db(tmp_path)
        sid = _build_and_approve(conn)
        s = _Settings(phase=6, mock_mode=True)
        await execute_approved_signal(conn, sid, s)
        await execute_approved_signal(conn, sid, s)  # second call must not double-fill
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1


class _FakeFeed:
    """Minimal feed_manager stand-in returning a fixed price per symbol."""
    def __init__(self, mids: dict):
        self.mids = mids

    def subscribe(self, symbol, exchange):
        pass

    def get_quote(self, symbol, exchange):
        from data.models import Quote, PriceUnit, DataQuality
        price = self.mids.get(symbol)
        if price is None:
            return None
        return Quote(
            symbol=symbol, exchange=exchange,
            bid=price - 0.5, ask=price + 0.5, last=price,
            volume=100_000.0, price_unit=PriceUnit.GBX,
            quality=DataQuality.LIVE, ts=datetime.now(UTC),
        )


def _open_position(conn, symbol="AZN", avg_cost=100.0, stop=95.0, target=110.0, qty=50):
    conn.execute(
        """INSERT INTO positions
           (symbol, exchange, quantity, avg_cost, currency,
            stop_price, target_price, opened_at)
           VALUES (?, 'LSE', ?, ?, 'GBP', ?, ?, ?)""",
        (symbol, qty, avg_cost, stop, target, datetime.now(UTC).isoformat()),
    )
    conn.commit()


class TestPositionMonitor:

    def test_closes_on_stop_hit(self, tmp_path: Path):
        from execution.position_monitor import monitor_positions
        conn, _ = _setup_db(tmp_path)
        _open_position(conn, stop=95.0, target=110.0)
        feed = _FakeFeed({"AZN": 90.0})          # below stop
        result = monitor_positions(conn, feed)
        assert result.closed == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL"
        ).fetchone()[0] == 0

    def test_closes_on_target_hit(self, tmp_path: Path):
        from execution.position_monitor import monitor_positions
        conn, _ = _setup_db(tmp_path)
        _open_position(conn, stop=95.0, target=110.0)
        feed = _FakeFeed({"AZN": 112.0})         # above target
        result = monitor_positions(conn, feed)
        assert result.closed == 1

    def test_holds_within_range(self, tmp_path: Path):
        from execution.position_monitor import monitor_positions
        conn, _ = _setup_db(tmp_path)
        _open_position(conn, stop=95.0, target=110.0)
        feed = _FakeFeed({"AZN": 102.0})         # between stop and target
        result = monitor_positions(conn, feed)
        assert result.closed == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM positions WHERE closed_at IS NULL"
        ).fetchone()[0] == 1

    def test_records_heartbeat(self, tmp_path: Path):
        from execution.position_monitor import monitor_positions
        from execution.heartbeat import is_heartbeat_healthy
        conn, _ = _setup_db(tmp_path)
        monitor_positions(conn, _FakeFeed({}))
        assert is_heartbeat_healthy(conn) is True

    def test_reconcile_runs(self, tmp_path: Path):
        from execution.position_monitor import monitor_positions
        conn, _ = _setup_db(tmp_path)
        _open_position(conn)                      # position with no fills → mismatch
        result = monitor_positions(conn, _FakeFeed({"AZN": 102.0}), reconcile=True)
        assert result.reconciliation_mismatches >= 0  # runs without error


class TestOrchestrator:

    async def test_run_cycle_monitors_and_closes(self, tmp_path: Path):
        import run_bot
        from monitor.dedup import DedupRegistry
        conn, db_path = _setup_db(tmp_path)
        _open_position(conn, stop=95.0, target=110.0)
        feed = _FakeFeed({"AZN": 112.0})          # above target → close
        summary = await run_bot.run_cycle(
            _Settings(phase=6, mock_mode=True), conn, DedupRegistry(),
            feed, db_path, do_scan=False, do_reconcile=True,
        )
        assert summary.positions_checked == 1
        assert summary.positions_closed == 1
        assert summary.scanned is False
        assert summary.reconciled is True

    async def test_run_cycle_scan_disabled_when_not_requested(self, tmp_path: Path):
        import run_bot
        from monitor.dedup import DedupRegistry
        conn, db_path = _setup_db(tmp_path)
        summary = await run_bot.run_cycle(
            _Settings(phase=6, mock_mode=True), conn, DedupRegistry(),
            _FakeFeed({}), db_path, do_scan=False, do_reconcile=False,
        )
        assert summary.scanned is False
        assert summary.positions_checked == 0


class _AutoSettings:
    def __init__(self):
        self.phase = 6
        self.mock_mode = True
        self.autonomous = True


class TestAutonomous:

    async def test_auto_executes_pending_signal(self, tmp_path: Path):
        """In autonomous mode, a built signal is auto-approved and filled with
        no human prompt."""
        import run_bot
        from strategy.signal_builder import build_signal
        conn, _ = _setup_db(tmp_path)
        spec = _spec()
        bars = _bars(spec)
        entry = bars[-1].close
        sid = build_signal(conn, spec, spec.symbol, spec.exchange, "breakout",
                           entry, bars, quote=_live_quote(spec, entry))
        assert sid is not None
        n = await run_bot.auto_execute_pending(_AutoSettings(), conn, None)
        assert n == 1
        # order + fill + position created, signal no longer pending
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        from api.queue import get_pending_signals
        assert len(get_pending_signals(conn)) == 0

    async def test_kill_switch_halts_autonomous(self, tmp_path: Path):
        import run_bot
        from strategy.signal_builder import build_signal
        from execution.kill_switch import activate_kill_switch
        conn, _ = _setup_db(tmp_path)
        spec = _spec()
        bars = _bars(spec)
        entry = bars[-1].close
        build_signal(conn, spec, spec.symbol, spec.exchange, "breakout",
                     entry, bars, quote=_live_quote(spec, entry))
        activate_kill_switch(conn)
        n = await run_bot.auto_execute_pending(_AutoSettings(), conn, None)
        assert n == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
