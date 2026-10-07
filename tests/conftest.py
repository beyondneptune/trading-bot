"""Shared pytest fixtures."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from data.database import init_db, get_connection


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """In-memory-style DB in a temp file, fully initialised."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()
