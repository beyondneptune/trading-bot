"""Supervised SPY test (L1) — places REAL paper orders, 1 share, step by step.

Runs the 5 steps in order, prints the result of each, and HALTS on the first
discrepancy (printing the open state for inspection — it does NOT auto-fix).
On full success the account ends flat.

Run during US market hours:
  TRADING_MOCK=false TRADING_BACKEND=alpaca TRADING_DB=lab_paper.db \
    PYTHONPATH=. .venv311/bin/python ops/spy_supervised_test.py
"""

from __future__ import annotations

import threading
import time

SYMBOL = "SPY"
DB = "lab_paper.db"


class Halt(Exception):
    pass


def _check(ok: bool, msg: str):
    print(f"   {'✅' if ok else '❌'} {msg}")
    if not ok:
        raise Halt(msg)


def _wait(fn, want, timeout=25, poll=1.0):
    """Poll fn() until it equals/satisfies `want` (callable) or timeout."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if (want(last) if callable(want) else last == want):
            return last
        time.sleep(poll)
    return last


def main() -> int:
    import config.settings  # loads .env
    from config.settings import require_python_311
    from broker.alpaca import AlpacaBroker
    from broker.base import OptionsNotAllowedError  # noqa: F401
    from data.database import get_connection
    from lab.controls import (
        close_everything, close_position, hard_stop, kill_switch, reconcile,
    )
    from lab.stream import run_stream

    require_python_311()
    broker = AlpacaBroker()
    acct = broker.connect()
    print(f"Account {acct.masked_number}  equity ${acct.equity:,.2f}")
    conn = get_connection(DB)

    # --- start the live trade-update stream (records to trade_updates) ---
    threading.Thread(target=lambda: _safe_stream(run_stream, broker, DB),
                     daemon=True).start()
    print("Trade-update stream starting; waiting 4s to connect…")
    time.sleep(4)

    def open_stops():
        return [o for o in broker.get_orders(open_only=True)
                if o.symbol == SYMBOL and o.is_protective_stop]

    def spy_qty():
        p = next((p for p in broker.get_positions() if p.symbol == SYMBOL), None)
        return p.qty if p else 0.0

    def tu_count():
        return conn.execute(
            "SELECT COUNT(*) c FROM trade_updates WHERE symbol=?", (SYMBOL,)
        ).fetchone()["c"]

    try:
        # ===== STEP 1: buy 1 SPY with GTC OTO stop =====
        price = broker.get_last_price(SYMBOL)
        stop = round(price * 0.90, 2)   # 10% below — won't trigger
        print(f"\n=== STEP 1: BUY 1 {SYMBOL} market + GTC protective stop @ {stop} "
              f"(last {price:.2f}) ===")
        coid = f"SPYTEST-{int(time.time())}"
        broker.submit_order(symbol=SYMBOL, qty=1, side="buy", order_type="market",
                            order_class="oto", stop_price=stop, tif="gtc",
                            client_order_id=coid)
        _wait(spy_qty, lambda q: q == 1, timeout=25)
        _check(spy_qty() == 1, f"position: {SYMBOL} qty = {spy_qty()}")
        stops = _wait(open_stops, lambda s: len(s) == 1, timeout=25)
        _check(len(stops) == 1, f"protective stop orders open: {len(stops)}")
        _check(stops and stops[0].tif == "gtc",
               f"stop time-in-force = {stops[0].tif if stops else 'NONE'} (want gtc)")
        time.sleep(3)  # let the stream record the fill
        _check(tu_count() > 0, f"trade_updates rows for {SYMBOL}: {tu_count()}")
        r = reconcile(broker, conn)
        _check(r.ok, f"reconcile clean (broker={r.broker_positions} db={r.db_positions})")

        # ===== STEP 2: kill — must NOT cancel the protective stop =====
        print("\n=== STEP 2: KILL (cancel entries only) ===")
        n = kill_switch(broker)
        time.sleep(2)
        _check(len(open_stops()) == 1,
               f"after kill, protective stop still open (cancelled {n} entries)")

        # ===== STEP 3: hard stop on -> order rejected; report stop liveness =====
        print("\n=== STEP 3: HARD STOP on -> expect a new order to be REJECTED ===")
        hard_stop(broker, True)
        time.sleep(1)
        rejected = False
        try:
            broker.submit_order(symbol=SYMBOL, qty=1, side="buy",
                                order_type="market", tif="day",
                                client_order_id=f"SPYTEST-rej-{int(time.time())}")
        except Exception as e:
            rejected = True
            print(f"   (order correctly rejected: {type(e).__name__})")
        _check(rejected, "new order rejected while hard stop engaged")
        still_live = len(open_stops()) == 1
        print(f"   ℹ️  existing protective stop still live while suspended: {still_live}")
        hard_stop(broker, False)
        print("   hard stop lifted.")

        # ===== STEP 4: close normally (cancel stop first, then sell) =====
        print("\n=== STEP 4: CLOSE position (cancel stop first, then sell) ===")
        close_position(broker, SYMBOL)
        _wait(spy_qty, lambda q: q == 0, timeout=25)
        _check(spy_qty() == 0, f"position flat: {SYMBOL} qty = {spy_qty()}")
        _wait(open_stops, lambda s: len(s) == 0, timeout=10)
        _check(len(open_stops()) == 0, "no open orders for SPY")
        time.sleep(2)
        r = reconcile(broker, conn)
        _check(r.ok, f"reconcile clean (broker={r.broker_positions} db={r.db_positions})")

        # ===== STEP 5: buy again, then close-everything --confirm =====
        print("\n=== STEP 5: BUY again, then CLOSE EVERYTHING --confirm ===")
        price = broker.get_last_price(SYMBOL)
        stop = round(price * 0.90, 2)
        broker.submit_order(symbol=SYMBOL, qty=1, side="buy", order_type="market",
                            order_class="oto", stop_price=stop, tif="gtc",
                            client_order_id=f"SPYTEST-2-{int(time.time())}")
        _wait(spy_qty, lambda q: q == 1, timeout=25)
        _check(spy_qty() == 1, f"re-bought: {SYMBOL} qty = {spy_qty()}")
        _wait(open_stops, lambda s: len(s) == 1, timeout=25)
        close_everything(broker, confirm=True)
        _wait(spy_qty, lambda q: q == 0, timeout=25)
        _check(spy_qty() == 0, f"flat after close-everything: {SYMBOL} qty = {spy_qty()}")
        _wait(lambda: broker.get_orders(open_only=True), lambda o: len(o) == 0, timeout=10)
        _check(len(broker.get_orders(open_only=True)) == 0, "account has no open orders")

        print("\n✅✅ ALL 5 STEPS PASSED — account left flat.")
        return 0

    except Halt as h:
        print(f"\n🛑 STOPPED: step check failed: {h}")
        print("   NOT auto-fixing. Current state:")
        print(f"     {SYMBOL} position qty: {spy_qty()}")
        for o in broker.get_orders(open_only=True):
            print(f"     open order: {o.symbol} {o.side} {o.order_type} "
                  f"[{o.status}] tif={o.tif} id={o.id}")
        return 3


def _safe_stream(run_stream, broker, db_path):
    try:
        run_stream(broker, db_path)
    except Exception as e:
        print(f"   (trade-update stream thread error: {type(e).__name__}: {e})")


if __name__ == "__main__":
    raise SystemExit(main())
