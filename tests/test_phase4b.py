"""Phase 4b tests: React dashboard source files, /api/journal endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

UTC = timezone.utc
WEB = Path(__file__).parent.parent / "ui" / "web"


# ---------------------------------------------------------------------------
# Frontend source structure sanity checks
# ---------------------------------------------------------------------------

class TestFrontendFiles:
    def test_package_json_exists(self):
        assert (WEB / "package.json").exists()

    def test_node_modules_populated(self):
        assert (WEB / "node_modules" / "react").is_dir()

    def test_vite_config_exists(self):
        assert (WEB / "vite.config.ts").exists()

    def test_tsconfig_exists(self):
        assert (WEB / "tsconfig.json").exists()

    def test_index_html_exists(self):
        assert (WEB / "index.html").exists()

    def test_main_tsx_exists(self):
        assert (WEB / "src" / "main.tsx").exists()

    def test_app_tsx_exists(self):
        assert (WEB / "src" / "App.tsx").exists()

    def test_api_client_exists(self):
        assert (WEB / "src" / "api" / "client.ts").exists()

    def test_websocket_hook_exists(self):
        assert (WEB / "src" / "hooks" / "useWebSocket.ts").exists()

    def test_header_component_exists(self):
        assert (WEB / "src" / "components" / "Header.tsx").exists()

    def test_safety_strip_component_exists(self):
        assert (WEB / "src" / "components" / "SafetyStrip.tsx").exists()

    def test_alert_card_component_exists(self):
        assert (WEB / "src" / "components" / "AlertCard.tsx").exists()

    def test_today_page_exists(self):
        assert (WEB / "src" / "pages" / "Today.tsx").exists()

    def test_positions_page_exists(self):
        assert (WEB / "src" / "pages" / "Positions.tsx").exists()

    def test_performance_page_exists(self):
        assert (WEB / "src" / "pages" / "Performance.tsx").exists()

    def test_journal_page_exists(self):
        assert (WEB / "src" / "pages" / "Journal.tsx").exists()

    def test_learn_page_exists(self):
        assert (WEB / "src" / "pages" / "Learn.tsx").exists()

    def test_dist_built(self):
        assert (WEB / "dist" / "index.html").exists(), (
            "Run 'npm run build' inside ui/web to produce dist/"
        )

    def test_dist_js_bundle_exists(self):
        js_files = list((WEB / "dist" / "assets").glob("*.js"))
        assert len(js_files) > 0

    def test_vite_config_proxies_api(self):
        content = (WEB / "vite.config.ts").read_text()
        assert "/api" in content
        assert "8000" in content

    def test_client_ts_has_kill_endpoint(self):
        content = (WEB / "src" / "api" / "client.ts").read_text()
        assert "/api/kill" in content

    def test_client_ts_has_session_token(self):
        content = (WEB / "src" / "api" / "client.ts").read_text()
        assert "X-Session-Token" in content

    def test_app_tsx_has_kill_switch_nav(self):
        content = (WEB / "src" / "App.tsx").read_text()
        assert "KILL" in content or "kill" in content

    def test_alert_card_has_approve_and_reject(self):
        content = (WEB / "src" / "components" / "AlertCard.tsx").read_text()
        assert "APPROVE" in content
        assert "REJECT" in content

    def test_header_shows_paper_badge(self):
        content = (WEB / "src" / "components" / "Header.tsx").read_text()
        assert "PAPER" in content

    def test_learn_page_has_beginner_content(self):
        content = (WEB / "src" / "pages" / "Learn.tsx").read_text()
        assert "stop loss" in content.lower() or "Stop Loss" in content
        assert "paper" in content.lower()

    def test_lightweight_charts_in_package_json(self):
        import json
        pkg = json.loads((WEB / "package.json").read_text())
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        assert any("lightweight-charts" in k for k in deps)


# ---------------------------------------------------------------------------
# api/app.py — /api/journal endpoint
# ---------------------------------------------------------------------------

class TestJournalEndpoint:
    def _setup(self, tmp_path: Path, monkeypatch):
        import api.app as app_module
        from api.auth import _SESSION_TOKEN
        from data.database import get_connection, init_db

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        monkeypatch.setattr(app_module, "_DB_PATH", db_path)
        conn = get_connection(db_path)
        # Insert signal + approval
        conn.execute(
            """INSERT INTO signals
               (symbol, exchange, signal_type, setup_version, action, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("AZN", "LSE", "dip_to_support", "v1", "BUY", datetime.now(UTC).isoformat()),
        )
        conn.commit()
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO approvals
               (signal_id, decision, interface, reason, confidence, mood, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sid, "APPROVED", "dashboard", "Looks good", 4, "Confident",
             datetime.now(UTC).isoformat()),
        )
        conn.commit()
        conn.close()
        return _SESSION_TOKEN

    def test_journal_returns_200(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        from api.app import app
        self._setup(tmp_path, monkeypatch)
        client = TestClient(app)
        resp = client.get("/api/journal")
        assert resp.status_code == 200

    def test_journal_contains_approvals(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        from api.app import app
        self._setup(tmp_path, monkeypatch)
        client = TestClient(app)
        resp = client.get("/api/journal")
        data = resp.json()
        assert "approvals" in data
        assert len(data["approvals"]) > 0

    def test_journal_approval_has_required_fields(self, tmp_path: Path, monkeypatch):
        from fastapi.testclient import TestClient
        from api.app import app
        self._setup(tmp_path, monkeypatch)
        client = TestClient(app)
        resp = client.get("/api/journal")
        a = resp.json()["approvals"][0]
        assert "decision" in a
        assert "signal_id" in a
        assert a["decision"] == "APPROVED"

    def test_journal_no_auth_required(self, tmp_path: Path, monkeypatch):
        """Journal is read-only — no token needed."""
        from fastapi.testclient import TestClient
        from api.app import app
        self._setup(tmp_path, monkeypatch)
        client = TestClient(app)
        resp = client.get("/api/journal")
        assert resp.status_code == 200

    def test_journal_empty_when_no_decisions(self, tmp_path: Path, monkeypatch):
        import api.app as app_module
        from data.database import init_db
        from fastapi.testclient import TestClient
        from api.app import app

        db_path = str(tmp_path / "empty.db")
        init_db(db_path)
        monkeypatch.setattr(app_module, "_DB_PATH", db_path)
        client = TestClient(app)
        resp = client.get("/api/journal")
        assert resp.status_code == 200
        assert resp.json()["approvals"] == []
