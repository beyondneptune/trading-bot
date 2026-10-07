"""Tests for data/database.py."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from data.database import get_connection, init_db


class TestInitDb:
    def test_creates_file(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        assert not db_path.exists()
        init_db(db_path)
        assert db_path.exists()

    def test_wal_mode(self, tmp_db: sqlite3.Connection):
        row = tmp_db.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

    def test_foreign_keys_on(self, tmp_db: sqlite3.Connection):
        row = tmp_db.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1

    def test_idempotent(self, tmp_path: Path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        init_db(db_path)  # second call must not raise

    def test_all_tables_exist(self, tmp_db: sqlite3.Connection):
        expected = {
            "instruments",
            "watchlist_changes",
            "bars",
            "news",
            "ai_reports",
            "debates",
            "signals",
            "approvals",
            "orders",
            "fills",
            "positions",
            "position_reviews",
            "equity_snapshots",
            "strategy_versions",
            "setup_registry",
            "shadow_trades",
            "proposals",
            "ai_spend",
            "reconciliation_log",
        }
        rows = tmp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        actual = {r[0] for r in rows}
        missing = expected - actual
        assert not missing, f"Missing tables: {missing}"


class TestBarsTable:
    def test_insert_and_retrieve_bar(self, tmp_db: sqlite3.Connection):
        ts = datetime(2025, 6, 1, 9, 0, tzinfo=timezone.utc).isoformat()
        tmp_db.execute(
            """INSERT INTO bars (symbol, exchange, bar_type, ts, open, high, low, close, volume, price_unit)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("AZN", "LSE", "1d", ts, 7400.0, 7500.0, 7350.0, 7450.0, 1_000_000, "GBX"),
        )
        tmp_db.commit()
        row = tmp_db.execute(
            "SELECT * FROM bars WHERE symbol=? AND exchange=?", ("AZN", "LSE")
        ).fetchone()
        assert row is not None
        assert row["close"] == pytest.approx(7450.0)
        assert row["price_unit"] == "GBX"

    def test_unique_constraint(self, tmp_db: sqlite3.Connection):
        ts = "2025-06-01T09:00:00+00:00"
        params = ("AZN", "LSE", "1d", ts, 7400.0, 7500.0, 7350.0, 7450.0, 1_000_000, "GBX")
        sql = """INSERT INTO bars (symbol, exchange, bar_type, ts, open, high, low, close, volume, price_unit)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        tmp_db.execute(sql, params)
        tmp_db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.execute(sql, params)
            tmp_db.commit()


class TestInstrumentsTable:
    def test_insert_instrument(self, tmp_db: sqlite3.Connection):
        tmp_db.execute(
            """INSERT INTO instruments (symbol, exchange, currency, price_unit, sector, added_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("AZN", "LSE", "GBP", "GBX", "healthcare", "2025-01-01T00:00:00Z"),
        )
        tmp_db.commit()
        row = tmp_db.execute(
            "SELECT * FROM instruments WHERE symbol='AZN'"
        ).fetchone()
        assert row["price_unit"] == "GBX"

    def test_primary_key_symbol_exchange(self, tmp_db: sqlite3.Connection):
        sql = """INSERT INTO instruments (symbol, exchange, currency, price_unit, added_at)
                 VALUES (?, ?, ?, ?, ?)"""
        tmp_db.execute(sql, ("AZN", "LSE", "GBP", "GBX", "2025-01-01T00:00:00Z"))
        tmp_db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.execute(sql, ("AZN", "LSE", "GBP", "GBX", "2025-02-01T00:00:00Z"))
            tmp_db.commit()
