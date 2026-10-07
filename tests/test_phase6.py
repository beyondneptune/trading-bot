"""Phase 6 tests: paper execution — orders, stops, kill switch, reconciliation, heartbeat."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

UTC = timezone.utc


def _setup_db(tmp_path: Path):
    from data.database import get_connection, init_db
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = get_connection(db_path)
    return conn, db_path


def _insert_approval(conn, signal_id: int = 1) -> int:
    conn.execute(
        """INSERT INTO signals
           (symbol, exchange, signal_type, setup_version, action, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("AZN", "LSE", "breakout", "v1", "BUY", datetime.now(UTC).isoformat()),
    )
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO approvals
           (signal_id, decision, interface, decided_at, order_ref)
           VALUES (?, ?, ?, ?, ?)""",
        (sid, "APPROVED", "test", datetime.now(UTC).isoformat(), "test-ref-001"),
    )
    conn.commit()
    approval_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return approval_id


# ---------------------------------------------------------------------------
# execution/orders.py — bracket orders
# ---------------------------------------------------------------------------

class TestOrders:
    def _make_spec(self, approval_id: int, order_ref: str = "test-ref-001"):
        from execution.orders import OrderSpec
        return OrderSpec(
            approval_id=approval_id,
            order_ref=order_ref,
            symbol="AZN",
            exchange="LSE",
            action="BUY",
            quantity=10,
            limit_price=7000.0,
            stop_price=6650.0,
            target_price=7700.0,
            currency="GBP",
        )

    def test_submit_mock_returns_filled(self, tmp_path: Path):
        from execution.orders import submit_bracket_order
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        spec = self._make_spec(approval_id)
        result = submit_bracket_order(conn, spec, phase=6, mock_mode=True)
        assert result.status == "FILLED"

    def test_submit_mock_creates_position(self, tmp_path: Path):
        from execution.orders import submit_bracket_order
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        spec = self._make_spec(approval_id)
        submit_bracket_order(conn, spec, phase=6, mock_mode=True)
        pos = conn.execute(
            "SELECT * FROM positions WHERE symbol='AZN'"
        ).fetchone()
        assert pos is not None
        assert pos["quantity"] == 10

    def test_submit_mock_records_fill(self, tmp_path: Path):
        from execution.orders import submit_bracket_order
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        spec = self._make_spec(approval_id)
        submit_bracket_order(conn, spec, phase=6, mock_mode=True)
        fills = conn.execute("SELECT * FROM fills").fetchall()
        assert len(fills) == 1
        assert fills[0]["quantity"] == 10

    def test_idempotency_second_submit_returns_same(self, tmp_path: Path):
        from execution.orders import submit_bracket_order
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        spec = self._make_spec(approval_id)
        r1 = submit_bracket_order(conn, spec, phase=6, mock_mode=True)
        r2 = submit_bracket_order(conn, spec, phase=6, mock_mode=True)
        assert r1.id == r2.id
        assert r2.status == "FILLED"

    def test_phase_lock_below_6_raises(self, tmp_path: Path):
        from execution.orders import submit_bracket_order, PhaseLockError
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        spec = self._make_spec(approval_id)
        with pytest.raises(PhaseLockError):
            submit_bracket_order(conn, spec, phase=5, mock_mode=True)

    def test_cancel_pending_order(self, tmp_path: Path):
        from execution.orders import submit_bracket_order, cancel_order
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        spec = self._make_spec(approval_id, order_ref="pending-ref")
        # Disable mock_mode so order stays PENDING_SUBMIT (not auto-filled)
        # We can't easily do that without phase 6, so instead just use cancel on a
        # PENDING_SUBMIT order inserted manually after a proper approval.
        conn.execute(
            """INSERT INTO orders
               (approval_id, order_ref, symbol, exchange, action, order_type,
                quantity, limit_price, stop_price, target_price, currency,
                status, submitted_at, updated_at)
               VALUES (?, 'pending-ref2', 'AZN', 'LSE', 'BUY', 'LMT',
                10, 7000.0, 6650.0, 7700.0, 'GBP',
                'PENDING_SUBMIT', ?, ?)""",
            (approval_id, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
        conn.commit()
        cancel_order(conn, "pending-ref2", "test")
        row = conn.execute(
            "SELECT status FROM orders WHERE order_ref='pending-ref2'"
        ).fetchone()
        assert row["status"] == "CANCELLED"

    def test_cancel_filled_order_raises(self, tmp_path: Path):
        from execution.orders import cancel_order, ExecutionError
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        conn.execute(
            """INSERT INTO orders
               (approval_id, order_ref, symbol, exchange, action, order_type,
                quantity, limit_price, stop_price, target_price, currency,
                status, submitted_at, updated_at)
               VALUES (?, 'filled-ref', 'AZN', 'LSE', 'BUY', 'LMT',
                10, 7000.0, 6650.0, 7700.0, 'GBP',
                'FILLED', ?, ?)""",
            (approval_id, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
        conn.commit()
        with pytest.raises(ExecutionError):
            cancel_order(conn, "filled-ref")

    def test_paper_slippage_applied(self, tmp_path: Path):
        from execution.orders import submit_bracket_order
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        spec = self._make_spec(approval_id)
        submit_bracket_order(conn, spec, phase=6, mock_mode=True)
        fill = conn.execute("SELECT fill_price FROM fills").fetchone()
        # BUY: fill_price should be slightly above limit_price (slippage)
        assert fill["fill_price"] >= spec.limit_price


# ---------------------------------------------------------------------------
# execution/kill_switch.py
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_not_killed_initially(self, tmp_path: Path):
        from execution.kill_switch import is_killed
        conn, _ = _setup_db(tmp_path)
        assert is_killed(conn) is False

    def test_activate_sets_killed(self, tmp_path: Path):
        from execution.kill_switch import activate_kill_switch, is_killed
        conn, _ = _setup_db(tmp_path)
        activate_kill_switch(conn)
        assert is_killed(conn) is True

    def test_clear_resumes(self, tmp_path: Path):
        from execution.kill_switch import activate_kill_switch, clear_kill_switch, is_killed
        conn, _ = _setup_db(tmp_path)
        activate_kill_switch(conn)
        clear_kill_switch(conn)
        assert is_killed(conn) is False

    def test_activate_cancels_pending_orders(self, tmp_path: Path):
        from execution.kill_switch import activate_kill_switch
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        for ref in ("ref-1", "ref-2"):
            conn.execute(
                """INSERT INTO orders
                   (approval_id, order_ref, symbol, exchange, action, order_type,
                    quantity, limit_price, stop_price, target_price, currency,
                    status, submitted_at, updated_at)
                   VALUES (?, ?, 'AZN', 'LSE', 'BUY', 'LMT',
                    10, 7000.0, 6650.0, 7700.0, 'GBP',
                    'PENDING_SUBMIT', ?, ?)""",
                (approval_id, ref, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
            )
        conn.commit()
        cancelled = activate_kill_switch(conn)
        assert cancelled == 2

    def test_activate_does_not_cancel_filled_orders(self, tmp_path: Path):
        from execution.kill_switch import activate_kill_switch
        conn, _ = _setup_db(tmp_path)
        approval_id = _insert_approval(conn)
        conn.execute(
            """INSERT INTO orders
               (approval_id, order_ref, symbol, exchange, action, order_type,
                quantity, limit_price, stop_price, target_price, currency,
                status, submitted_at, updated_at)
               VALUES (?, 'filled-ref', 'AZN', 'LSE', 'BUY', 'LMT',
                10, 7000.0, 6650.0, 7700.0, 'GBP',
                'FILLED', ?, ?)""",
            (approval_id, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
        conn.commit()
        cancelled = activate_kill_switch(conn)
        assert cancelled == 0


# ---------------------------------------------------------------------------
# execution/stops.py
# ---------------------------------------------------------------------------

class TestStops:
    def _insert_position(self, conn, qty=10, avg_cost=7000.0, stop=6650.0, target=7700.0):
        conn.execute(
            """INSERT INTO positions
               (symbol, exchange, quantity, avg_cost, currency,
                stop_price, target_price, opened_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("AZN", "LSE", qty, avg_cost, "GBP", stop, target,
             datetime.now(UTC).isoformat()),
        )
        conn.commit()

    def test_hold_when_price_in_range(self, tmp_path: Path):
        from execution.stops import check_stops
        conn, _ = _setup_db(tmp_path)
        self._insert_position(conn)
        results = check_stops(conn, "AZN", 7200.0)
        assert all(r.action == "HOLD" for r in results)

    def test_stop_hit_closes_position(self, tmp_path: Path):
        from execution.stops import check_stops
        conn, _ = _setup_db(tmp_path)
        self._insert_position(conn)
        results = check_stops(conn, "AZN", 6600.0)
        assert any(r.action == "STOP_HIT" for r in results)
        closed = conn.execute(
            "SELECT closed_at FROM positions WHERE symbol='AZN'"
        ).fetchone()
        assert closed["closed_at"] is not None

    def test_target_hit_closes_position(self, tmp_path: Path):
        from execution.stops import check_stops
        conn, _ = _setup_db(tmp_path)
        self._insert_position(conn)
        results = check_stops(conn, "AZN", 7800.0)
        assert any(r.action == "TARGET_HIT" for r in results)

    def test_no_position_returns_empty(self, tmp_path: Path):
        from execution.stops import check_stops
        conn, _ = _setup_db(tmp_path)
        results = check_stops(conn, "AZN", 7200.0)
        assert results == []


# ---------------------------------------------------------------------------
# execution/reconciliation.py
# ---------------------------------------------------------------------------

class TestReconciliation:
    def test_empty_db_reconciles_clean(self, tmp_path: Path):
        from execution.reconciliation import run_reconciliation
        conn, _ = _setup_db(tmp_path)
        report = run_reconciliation(conn)
        assert report.all_match is True
        assert report.mismatches == 0

    def test_reconciliation_logs_to_db(self, tmp_path: Path):
        from execution.reconciliation import run_reconciliation
        conn, _ = _setup_db(tmp_path)
        run_reconciliation(conn)
        row = conn.execute(
            "SELECT mismatch_type FROM reconciliation_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert "RECON" in row["mismatch_type"]

    def test_reconciliation_report_has_summary(self, tmp_path: Path):
        from execution.reconciliation import run_reconciliation
        conn, _ = _setup_db(tmp_path)
        report = run_reconciliation(conn)
        assert isinstance(report.summary, str)
        assert "0 positions" in report.summary


# ---------------------------------------------------------------------------
# execution/heartbeat.py
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_no_heartbeat_unhealthy(self, tmp_path: Path):
        from execution.heartbeat import is_heartbeat_healthy
        conn, _ = _setup_db(tmp_path)
        assert is_heartbeat_healthy(conn) is False

    def test_fresh_heartbeat_healthy(self, tmp_path: Path):
        from execution.heartbeat import record_heartbeat, is_heartbeat_healthy
        conn, _ = _setup_db(tmp_path)
        record_heartbeat(conn)
        assert is_heartbeat_healthy(conn) is True

    def test_stale_heartbeat_unhealthy(self, tmp_path: Path):
        from execution.heartbeat import is_heartbeat_healthy, STALE_THRESHOLD_SECONDS
        from data.database import get_connection
        conn, db_path = _setup_db(tmp_path)
        # Write a stale heartbeat timestamp directly
        stale_ts = (datetime.now(UTC) - timedelta(seconds=STALE_THRESHOLD_SECONDS + 10)).isoformat()
        conn.execute(
            "INSERT INTO system_events (event_type, note, logged_at) VALUES (?, ?, ?)",
            ("HEARTBEAT", "alive", stale_ts),
        )
        conn.commit()
        assert is_heartbeat_healthy(conn) is False

    def test_heartbeat_loop_can_cancel(self, tmp_path: Path):
        from execution.heartbeat import run_heartbeat
        conn, _ = _setup_db(tmp_path)

        async def _run():
            task = asyncio.create_task(run_heartbeat(conn))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.get_event_loop().run_until_complete(_run())
