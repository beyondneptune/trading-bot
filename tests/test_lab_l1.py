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
        assert acct.options_level == 3   # options level left unchanged (hard-blocked instead)

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
            "INSERT INTO trade_updates (event, order_id, symbol, side, filled_qty, received_at)"
            " VALUES ('fill','o1','SPY','buy',10,'2026-01-01T00:00:00+00:00')"
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
        o = b.submit_order(symbol="SPY", qty=1, side="buy")   # not blocked
        assert o.symbol == "SPY" and o.status == "filled"     # buy-market fills in the fake


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


class TestStream:
    def test_handle_trade_update_records_fill(self, tmp_path):
        from lab.stream import handle_trade_update
        conn = _db(tmp_path)
        event = {
            "event": "fill",
            "order": {
                "id": "o1", "client_order_id": "S4-c1", "symbol": "SPY",
                "qty": 1, "filled_qty": 1, "filled_avg_price": 500.25,
                "status": "filled", "side": "buy",
            },
        }
        handle_trade_update(conn, event)
        row = conn.execute(
            "SELECT event, symbol, filled_avg_price, side, status "
            "FROM trade_updates"
        ).fetchone()
        assert row["event"] == "fill"
        assert row["symbol"] == "SPY"
        assert abs(row["filled_avg_price"] - 500.25) < 1e-9
        assert row["side"] == "buy" and row["status"] == "filled"


class TestStartupSequence:
    def test_startup_checks_lock_config_and_reconcile(self, tmp_path):
        from lab.run_lab import startup_checks
        conn = _db(tmp_path)
        b = FakeBroker(shorting_enabled=True, multiplier="2", options_level=3)
        acct = startup_checks(b, conn, "paper", "lab_paper_test.db")
        assert acct.shorting_enabled is False      # locked
        assert acct.multiplier == "1"              # no margin
        # options level recorded
        row = conn.execute(
            "SELECT value FROM db_meta WHERE key='alpaca_options_level'"
        ).fetchone()
        assert row["value"] == "3"

    def test_startup_warns_on_hard_stop(self, tmp_path, caplog):
        import logging
        from lab.run_lab import startup_checks
        conn = _db(tmp_path)
        b = FakeBroker(suspended=True)
        with caplog.at_level(logging.WARNING):
            startup_checks(b, conn, "paper", "lab_paper_test.db")
        assert any("HARD STOP" in r.message for r in caplog.records)


class TestOTOAndStops:
    def _oto_buy(self, b, stop=600.0, tif="gtc"):
        b.connect()
        b.submit_order(symbol="SPY", qty=1, side="buy", order_type="market",
                       order_class="oto", stop_price=stop, tif=tif,
                       client_order_id="S5-1")

    def test_oto_buy_creates_position_and_gtc_stop(self):
        b = FakeBroker()
        self._oto_buy(b)
        assert len(b.get_positions()) == 1
        stops = [o for o in b.get_orders(open_only=True) if o.is_protective_stop]
        assert len(stops) == 1
        assert stops[0].tif == "gtc" and stops[0].stop_price == 600.0

    def test_kill_leaves_protective_stop(self):
        from lab.controls import kill_switch
        b = FakeBroker()
        self._oto_buy(b)
        kill_switch(b)                       # cancels entries only
        stops = [o for o in b.get_orders(open_only=True) if o.is_protective_stop]
        assert len(stops) == 1               # stop survives the kill switch

    def test_close_position_cancels_stop_then_sells(self):
        from lab.controls import close_position
        b = FakeBroker()
        self._oto_buy(b)
        assert close_position(b, "SPY") is True
        assert b.get_positions() == []
        assert b.get_orders(open_only=True) == []   # stop cancelled, position sold

    def test_close_everything_flattens(self):
        from lab.controls import close_everything
        b = FakeBroker()
        self._oto_buy(b)
        close_everything(b, confirm=True)
        assert b.get_positions() == []
        assert b.get_orders(open_only=True) == []

    def test_ensure_stops_replaces_missing_and_alerts(self, tmp_path):
        from lab.controls import ensure_stops
        conn = _db(tmp_path)
        b = FakeBroker()
        b.connect()
        b._add_position("SPY", 1, 600.0)     # a position with NO protective stop
        replaced = ensure_stops(b, conn)
        assert replaced == ["SPY"]
        stops = [o for o in b.get_orders(open_only=True) if o.is_protective_stop]
        assert len(stops) == 1 and stops[0].tif == "gtc"
        alerts = conn.execute(
            "SELECT COUNT(*) c FROM system_events WHERE event_type='ALERT'"
        ).fetchone()["c"]
        assert alerts == 1

    def test_cancel_unfilled_entries(self):
        from lab.controls import cancel_unfilled_entries
        b = FakeBroker()
        b.connect()
        b._add_order("SPY", 1, side="buy", status="new", oid="e1")
        assert cancel_unfilled_entries(b) == 1


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
