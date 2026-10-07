"""Phase L1 — broker interface + Alpaca adapter (tested via FakeBroker; no network).

Covers: paper-account identity, safe account config, hard stop, positions/orders,
kill switch / close-everything, reconciliation, the masked startup banner, and
the broker factory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from broker.base import BrokerAccount, startup_banner
from broker.fake import FakeBroker
from lab.controls import close_everything, hard_stop, kill_switch, reconcile


def _db(tmp_path: Path):
    from data.database import get_connection, init_db
    p = str(tmp_path / "lab_paper_test.db")
    init_db(p)
    return get_connection(p)


class TestAccountIdentity:
    def test_pa_number_is_paper(self):
        a = BrokerAccount("PA3XYZ1234", "ACTIVE", 0, 0, True, "2", 3, False)
        assert a.is_paper_number is True
        assert a.masked_number == "PA…1234"

    def test_non_pa_is_not_paper(self):
        a = BrokerAccount("U0001234", "ACTIVE", 0, 0, True, "2", 3, False)
        assert a.is_paper_number is False


class TestFakeBroker:
    def test_connect_returns_paper_account(self):
        b = FakeBroker()
        acct = b.connect()
        assert acct.is_paper_number and b.is_paper

    def test_safe_config_disables_short_and_margin(self):
        b = FakeBroker(shorting_enabled=True, multiplier="2", options_level=3)
        acct = b.set_safe_account_config()
        assert acct.shorting_enabled is False
        assert acct.multiplier == "1"
        assert acct.options_level == 1

    def test_hard_stop_toggles(self):
        b = FakeBroker()
        hard_stop(b, True)
        assert b.get_account().trade_suspended_by_user is True
        hard_stop(b, False)
        assert b.get_account().trade_suspended_by_user is False

    def test_kill_switch_cancels_open_orders(self):
        b = FakeBroker()
        b._add_order("SPY", 1, status="new")
        b._add_order("SPY", 1, status="new", oid="o2")
        assert kill_switch(b) == 2
        assert b.get_orders(open_only=True) == []

    def test_close_everything_requires_confirm(self):
        b = FakeBroker()
        b._add_position("SPY", 10, 500.0)
        with pytest.raises(ValueError):
            close_everything(b)                 # no confirm
        assert close_everything(b, confirm=True) == 1
        assert b.get_positions() == []


class TestReconcile:
    def test_match(self, tmp_path):
        conn = _db(tmp_path)
        b = FakeBroker()
        b._add_position("SPY", 10, 500.0)
        conn.execute(
            "INSERT INTO positions (symbol,exchange,quantity,avg_cost,currency,opened_at)"
            " VALUES ('SPY','ARCA',10,500.0,'USD','2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        assert reconcile(b, conn).ok is True

    def test_mismatch(self, tmp_path):
        conn = _db(tmp_path)
        b = FakeBroker()
        b._add_position("SPY", 10, 500.0)   # broker has 10, DB has none
        res = reconcile(b, conn)
        assert res.ok is False
        assert res.mismatches[0]["symbol"] == "SPY"


class TestOptionsBlock:
    def test_is_option_symbol(self):
        from broker.base import is_option_symbol
        assert is_option_symbol("SPY231215C00450000") is True
        assert is_option_symbol("SPY") is False
        assert is_option_symbol("AAPL") is False

    def test_submit_order_blocks_options_by_asset_class(self):
        from broker.base import OptionsNotAllowedError
        b = FakeBroker()
        with pytest.raises(OptionsNotAllowedError):
            b.submit_order(symbol="SPY", qty=1, side="sell", asset_class="option")

    def test_submit_order_blocks_occ_symbol(self):
        from broker.base import OptionsNotAllowedError
        b = FakeBroker()
        with pytest.raises(OptionsNotAllowedError):
            b.submit_order(symbol="SPY231215C00450000", qty=1, side="buy")

    def test_submit_order_allows_equity(self):
        b = FakeBroker()
        o = b.submit_order(symbol="SPY", qty=1, side="buy")
        assert o.symbol == "SPY" and o.status == "new"


class TestOptionsLevelMonitor:
    def test_records_and_alerts_on_change(self, tmp_path):
        from lab.controls import check_options_level
        conn = _db(tmp_path)
        b = FakeBroker(options_level=3)
        level, changed = check_options_level(conn, b)
        assert level == 3 and changed is False          # first time: just records
        b._acct.options_level = 5                        # broker changes it
        level, changed = check_options_level(conn, b)
        assert level == 5 and changed is True
        alerts = conn.execute(
            "SELECT COUNT(*) c FROM system_events WHERE event_type='ALERT'"
        ).fetchone()["c"]
        assert alerts == 1


class TestBannerAndFactory:
    def test_banner_masks_account(self):
        acct = BrokerAccount("PA3XYZ1234", "ACTIVE", 0, 0, False, "1", 1, False)
        banner = startup_banner("paper", "alpaca", "lab_paper.db", acct)
        assert "PA…1234" in banner
        assert "PA3XYZ1234" not in banner
        assert "paper" in banner and "alpaca" in banner and "lab_paper.db" in banner

    def test_factory_alpaca(self):
        from broker import make_broker
        from broker.alpaca import AlpacaBroker
        from config.settings import Settings
        b = make_broker(Settings(trading_backend="alpaca"))
        assert isinstance(b, AlpacaBroker)

    def test_factory_rejects_non_alpaca(self):
        from broker import make_broker
        from config.settings import Settings
        with pytest.raises(ValueError):
            make_broker(Settings(trading_backend="none"))
