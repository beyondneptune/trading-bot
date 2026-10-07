"""Phase 4a tests: API security, approval queue, Telegram chat-ID filter."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

UTC = timezone.utc


# ---------------------------------------------------------------------------
# api/queue.py — approval queue
# ---------------------------------------------------------------------------

class TestApprovalQueue:
    def _setup(self, tmp_path: Path):
        from data.database import get_connection, init_db
        db_path = tmp_path / "test.db"
        init_db(str(db_path))
        conn = get_connection(str(db_path))
        # Insert a test signal
        conn.execute(
            """INSERT INTO signals
               (symbol, exchange, signal_type, setup_version, action, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("AZN", "LSE", "dip_to_support", "v1", "BUY",
             datetime.now(UTC).isoformat()),
        )
        conn.commit()
        signal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return conn, signal_id

    def test_approve_returns_order_ref(self, tmp_path: Path):
        from api.queue import approve_signal
        conn, sid = self._setup(tmp_path)
        order_ref = approve_signal(conn, sid, "test")
        assert isinstance(order_ref, str)
        assert len(order_ref) > 10

    def test_double_approve_raises(self, tmp_path: Path):
        from api.queue import ApprovalError, approve_signal
        conn, sid = self._setup(tmp_path)
        approve_signal(conn, sid, "test")
        with pytest.raises(ApprovalError):
            approve_signal(conn, sid, "test")

    def test_approve_then_reject_raises(self, tmp_path: Path):
        from api.queue import ApprovalError, approve_signal, reject_signal
        conn, sid = self._setup(tmp_path)
        approve_signal(conn, sid, "test")
        with pytest.raises(ApprovalError):
            reject_signal(conn, sid, "test")

    def test_reject_works(self, tmp_path: Path):
        from api.queue import reject_signal
        conn, sid = self._setup(tmp_path)
        reject_signal(conn, sid, "dashboard", reason="Too risky")
        row = conn.execute(
            "SELECT decision FROM approvals WHERE signal_id=?", (sid,)
        ).fetchone()
        assert row["decision"] == "REJECTED"

    def test_expired_signal_cannot_be_approved(self, tmp_path: Path):
        from api.queue import ApprovalError, approve_signal, expire_signal
        conn, sid = self._setup(tmp_path)
        expire_signal(conn, sid, "Price moved away")
        with pytest.raises(ApprovalError) as exc:
            approve_signal(conn, sid, "dashboard")
        assert "expired" in str(exc.value).lower()

    def test_order_ref_unique_per_approval(self, tmp_path: Path):
        from data.database import get_connection, init_db
        from api.queue import approve_signal
        db_path = tmp_path / "test2.db"
        init_db(str(db_path))
        conn = get_connection(str(db_path))
        # Create two signals
        for _ in range(2):
            conn.execute(
                """INSERT INTO signals
                   (symbol, exchange, signal_type, setup_version, action, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("AZN", "LSE", "breakout", "v1", "BUY", datetime.now(UTC).isoformat()),
            )
        conn.commit()
        rows = conn.execute("SELECT id FROM signals").fetchall()
        refs = [approve_signal(conn, r[0], "test") for r in rows]
        assert refs[0] != refs[1]

    def test_get_pending_signals(self, tmp_path: Path):
        from api.queue import get_pending_signals
        conn, sid = self._setup(tmp_path)
        pending = get_pending_signals(conn)
        assert any(r["id"] == sid for r in pending)

    def test_approved_signal_not_in_pending(self, tmp_path: Path):
        from api.queue import approve_signal, get_pending_signals
        conn, sid = self._setup(tmp_path)
        approve_signal(conn, sid, "test")
        pending = get_pending_signals(conn)
        assert not any(r["id"] == sid for r in pending)


# ---------------------------------------------------------------------------
# api/auth.py — session token and origin check
# ---------------------------------------------------------------------------

class TestAuth:
    def test_require_token_passes_with_correct_token(self):
        """Import and verify the token logic works end-to-end."""
        from api.auth import _SESSION_TOKEN, get_session_token
        assert get_session_token() == _SESSION_TOKEN
        assert len(_SESSION_TOKEN) > 10

    def test_localhost_origin_allowed(self):
        from api.auth import _allowed_origin
        assert _allowed_origin("http://localhost:5173") is True
        assert _allowed_origin("http://127.0.0.1:5173") is True

    def test_external_origin_blocked(self):
        from api.auth import _allowed_origin
        assert _allowed_origin("https://evil.com") is False
        assert _allowed_origin("http://192.168.1.1:5173") is False

    def test_no_origin_allowed(self):
        from api.auth import _allowed_origin
        assert _allowed_origin(None) is True  # same-origin browser request


# ---------------------------------------------------------------------------
# api/app.py — HTTP endpoints (using FastAPI TestClient)
# ---------------------------------------------------------------------------

class TestAPIEndpoints:
    def _setup(self, tmp_path: Path, monkeypatch):
        """Create a test app with a fresh database."""
        import api.app as app_module
        from api.auth import _SESSION_TOKEN

        db_path = str(tmp_path / "test.db")
        from data.database import init_db
        init_db(db_path)
        monkeypatch.setattr(app_module, "_DB_PATH", db_path)
        return db_path, _SESSION_TOKEN

    def test_status_endpoint(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        from api.app import app
        self._setup(tmp_path, monkeypatch)
        client = TestClient(app, base_url="http://localhost")
        resp = client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "PAPER"

    def test_approve_requires_token(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        from api.app import app
        self._setup(tmp_path, monkeypatch)
        client = TestClient(app, base_url="http://localhost")
        resp = client.post("/api/approve", json={"signal_id": 1})
        assert resp.status_code == 401

    def test_reject_requires_token(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        from api.app import app
        self._setup(tmp_path, monkeypatch)
        client = TestClient(app, base_url="http://localhost")
        resp = client.post("/api/reject", json={"signal_id": 1})
        assert resp.status_code == 401

    def test_approve_with_token(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        import api.app as app_module
        from api.app import app
        from api.auth import _SESSION_TOKEN
        from data.database import get_connection

        db_path, token = self._setup(tmp_path, monkeypatch)
        conn = get_connection(db_path)
        conn.execute(
            """INSERT INTO signals
               (symbol, exchange, signal_type, setup_version, action, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("AZN", "LSE", "breakout", "v1", "BUY", datetime.now(UTC).isoformat()),
        )
        conn.commit()
        signal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        client = TestClient(app, base_url="http://localhost")
        resp = client.post(
            "/api/approve",
            json={"signal_id": signal_id},
            headers={"X-Session-Token": token},
        )
        assert resp.status_code == 200
        assert "order_ref" in resp.json()

    def test_double_approve_returns_409(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        from api.app import app
        from api.auth import _SESSION_TOKEN
        from data.database import get_connection

        db_path, token = self._setup(tmp_path, monkeypatch)
        conn = get_connection(db_path)
        conn.execute(
            """INSERT INTO signals
               (symbol, exchange, signal_type, setup_version, action, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("AZN", "LSE", "breakout", "v1", "BUY", datetime.now(UTC).isoformat()),
        )
        conn.commit()
        signal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        client = TestClient(app, base_url="http://localhost")
        headers = {"X-Session-Token": token}
        resp1 = client.post("/api/approve", json={"signal_id": signal_id}, headers=headers)
        resp2 = client.post("/api/approve", json={"signal_id": signal_id}, headers=headers)
        assert resp1.status_code == 200
        assert resp2.status_code == 409

    def test_kill_requires_confirm(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        from api.app import app
        from api.auth import _SESSION_TOKEN

        self._setup(tmp_path, monkeypatch)
        client = TestClient(app, base_url="http://localhost")
        token = _SESSION_TOKEN
        resp = client.post(
            "/api/kill",
            json={"confirm": False},
            headers={"X-Session-Token": token},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ui/telegram/bot.py — chat-ID filter and formatting
# ---------------------------------------------------------------------------

class TestTelegram:
    def test_disallowed_chat_id_rejected(self, monkeypatch):
        from ui.telegram.bot import _is_allowed
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        assert _is_allowed(12345) is True
        assert _is_allowed(99999) is False

    def test_no_chat_id_env_blocks_all(self, monkeypatch):
        from ui.telegram.bot import _is_allowed
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert _is_allowed(12345) is False

    def test_format_alert_card_has_required_fields(self):
        from ui.telegram.bot import format_alert_card
        signal = {
            "symbol": "AZN", "exchange": "LSE",
            "signal_type": "dip_to_support", "action": "BUY",
            "entry_price": 7000, "stop_price": 6650, "target_price": 7700,
            "size": 5, "risk_gbp": 17.50, "cost_r": 0.07,
            "regime": "normal", "valid_until": "14:05",
        }
        card = format_alert_card(signal, equity_gbp=12_500)
        assert "AZN" in card
        assert "BUY" in card
        assert "7000" in card or "7,000" in card or "7 000" in card

    def test_format_morning_brief(self):
        from ui.telegram.bot import format_morning_brief
        brief = format_morning_brief([], [], [], "normal")
        assert "Morning Brief" in brief
        assert "normal" in brief
