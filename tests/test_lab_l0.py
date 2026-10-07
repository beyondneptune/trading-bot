"""Phase L0 (strategy lab) — safety & plumbing tests.

Covers: IB trading switched off behind a flag, no synthetic data outside mock
mode, the one-database-per-mode guard, and the hardened dashboard auth
(random token + Origin/Host checks). All use fake data; no network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# L0-a: IB trading path is off unless trading_backend == "ib"
# ---------------------------------------------------------------------------

class TestIbTradingFlag:
    async def test_live_order_refused_when_backend_not_ib(self):
        from execution.live_orders import submit_bracket_order_live
        from execution.orders import ExecutionError, OrderSpec
        spec = OrderSpec(
            approval_id=1, order_ref="x", symbol="AAPL", exchange="NASDAQ",
            action="BUY", quantity=1, limit_price=100.0, stop_price=95.0,
            target_price=110.0, currency="USD",
        )
        # settings.yaml default is trading_backend: none -> must refuse.
        with pytest.raises(ExecutionError):
            await submit_bracket_order_live(None, spec, None, phase=6)


# ---------------------------------------------------------------------------
# L0-d: no synthetic data outside mock mode (CLAUDE.md rule 11)
# ---------------------------------------------------------------------------

class TestNoMockOutsideMockMode:
    def _spec(self):
        from instruments.units import find_instrument, load_watchlist
        wl = load_watchlist()
        return find_instrument(wl, "AAPL", "NASDAQ") or wl[0]

    def test_raises_when_no_data_and_not_mock(self, tmp_path: Path, monkeypatch):
        from data.database import init_db
        import data.history as hist
        db = str(tmp_path / "lab_paper.db")
        init_db(db)
        monkeypatch.setattr(hist, "load_settings",
                            lambda: SimpleNamespace(mock_mode=False))
        with pytest.raises(hist.NoDataError):
            hist.get_daily_bars_sync(self._spec(), db)

    def test_generates_mock_only_in_mock_mode(self, tmp_path: Path, monkeypatch):
        from data.database import init_db
        import data.history as hist
        db = str(tmp_path / "lab_mock.db")
        init_db(db)
        monkeypatch.setattr(hist, "load_settings",
                            lambda: SimpleNamespace(mock_mode=True))
        bars = hist.get_daily_bars_sync(self._spec(), db)
        assert len(bars) > 0


# ---------------------------------------------------------------------------
# L0-c: one database per mode
# ---------------------------------------------------------------------------

class TestDbModeGuard:
    def _settings(self, mock_mode, path):
        from config.settings import Settings, DatabaseConfig
        return Settings(mock_mode=mock_mode, database=DatabaseConfig(path=path))

    def test_mock_mode_requires_mock_db(self):
        from config.settings import assert_db_for_mode
        assert_db_for_mode(self._settings(True, "lab_mock.db"))      # ok
        with pytest.raises(RuntimeError):
            assert_db_for_mode(self._settings(True, "lab_paper.db"))  # mismatch

    def test_paper_mode_rejects_mock_db(self):
        from config.settings import assert_db_for_mode
        assert_db_for_mode(self._settings(False, "lab_paper.db"))     # ok
        with pytest.raises(RuntimeError):
            assert_db_for_mode(self._settings(False, "lab_mock.db"))  # mismatch

    def test_test_db_is_exempt(self):
        from config.settings import assert_db_for_mode
        assert_db_for_mode(self._settings(False, "/tmp/test.db"))     # no raise


# ---------------------------------------------------------------------------
# L0-e: hardened dashboard auth
# ---------------------------------------------------------------------------

class TestDashboardAuth:
    def test_token_is_random_hex(self):
        from api.auth import get_session_token
        tok = get_session_token()
        assert len(tok) == 64 and all(c in "0123456789abcdef" for c in tok)

    def test_host_check(self):
        from api.auth import _allowed_host
        assert _allowed_host("localhost:8000") is True
        assert _allowed_host("127.0.0.1") is True
        assert _allowed_host("evil.example.com") is False
        assert _allowed_host(None) is False

    def test_origin_check(self):
        from api.auth import _allowed_origin
        assert _allowed_origin("http://localhost:5173") is True
        assert _allowed_origin(None) is True
        assert _allowed_origin("http://evil.example.com") is False
