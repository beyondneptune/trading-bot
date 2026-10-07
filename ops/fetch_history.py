"""Fetch ~1 year of daily bars from IB for the whole watchlist (read-only).

Run:  PYTHONPATH=. .venv311/bin/python ops/fetch_history.py
"""

from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.ERROR)


async def main() -> int:
    from ops.ib_connection import LiveIBConnection
    from data.ib_history import fetch_and_store_daily_bars
    from data.database import init_db
    from config.settings import load_settings

    db_path = load_settings().database.path
    init_db(db_path)
    print(f"Target DB: {db_path}")
    conn = LiveIBConnection("127.0.0.1", 4002, client_id=14, readonly=True)
    await conn.connect()
    print("Connected. Fetching daily bars…")
    result = await fetch_and_store_daily_bars(conn, db_path=db_path)
    await conn.disconnect()
    for sym, n in result.items():
        print(f"  {sym:6} {n} bars")
    total = sum(result.values())
    print(f"Total: {total} bars stored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
