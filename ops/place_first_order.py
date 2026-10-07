"""Supervised FIRST paper order — places ONE small bracket order on the IB
paper account and reports what happens. Paper money only.

Safety:
  - Refuses to run unless the account is a paper (DU...) account.
  - Tiny default size (1 share).
  - Prints every step; waits for the fill and reports it.
  - Records the order/fill/position into the local DB so the dashboard shows it.

Requires: IB Gateway logged into the paper account with
          "Read-Only API" UNCHECKED (otherwise IB rejects the order).

Run:  PYTHONPATH=. .venv311/bin/python ops/place_first_order.py AZN 1
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.ERROR)
UTC = timezone.utc

HOST, PORT, CLIENT_ID = "127.0.0.1", 4002, 1


def _round_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return round(price, 2)
    return round(round(price / tick) * tick, 6)


async def _tick_for_price(ib, details, price: float) -> float:
    """Return the LSE price-band tick that applies at `price`, via the
    contract's market rule. Falls back to 1.0 (one penny) for GBX stocks."""
    ids = (details.marketRuleIds or "").split(",")
    for rid in ids:
        rid = rid.strip()
        if not rid:
            continue
        try:
            increments = await ib.reqMarketRuleAsync(int(rid))
        except Exception:
            continue
        applicable = None
        for pi in sorted(increments, key=lambda x: x.lowEdge):
            if price >= pi.lowEdge:
                applicable = pi.increment
        if applicable and applicable > 0:
            return float(applicable)
    return 1.0


async def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AZN"
    quantity = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    from ib_async import IB, Stock
    from risk.guards import assert_paper_account
    from instruments.units import find_instrument, load_watchlist
    from data.database import get_connection
    from execution.orders import _record_fill, _open_position, OrderSpec

    spec = find_instrument(load_watchlist(), symbol, "LSE")
    if spec is None:
        print(f"❌ {symbol} not in watchlist.")
        return 1
    currency = spec.currency

    ib = IB()
    print(f"Connecting to Gateway {HOST}:{PORT} (orders ENABLED)…")
    try:
        await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, readonly=False, timeout=15)
    except Exception as e:
        print(f"❌ Could not connect: {e}")
        return 1

    acct = (ib.managedAccounts() or [""])[0]
    try:
        assert_paper_account(acct)
    except SystemExit:
        print(f"⚠️  {acct} is not a paper account — aborting.")
        ib.disconnect()
        return 2
    print(f"✅ Paper account: {acct}")

    # Resolve the contract + tick size
    contract = Stock(symbol, "SMART", currency, primaryExchange="LSE")
    details = await ib.reqContractDetailsAsync(contract)
    if not details:
        print(f"❌ No IB contract for {symbol}.")
        ib.disconnect()
        return 1
    contract = details[0].contract
    # LSE shares: market data is in pence but ORDERS are priced in pounds.
    # priceMagnifier (100 for GBX shares) converts between the two.
    pm = details[0].priceMagnifier or 1
    print(f"Contract OK: conId={contract.conId}, priceMagnifier={pm}")

    # Delayed quote → marketable limit
    ib.reqMarketDataType(3)  # delayed
    ticker = ib.reqMktData(contract, "", snapshot=False)
    for _ in range(16):
        await asyncio.sleep(0.5)
        if (ticker.ask == ticker.ask and ticker.ask > 0) or \
           (ticker.close == ticker.close and ticker.close > 0):
            break
    ref = ticker.ask if (ticker.ask == ticker.ask and ticker.ask > 0) else ticker.close
    ib.cancelMktData(contract)
    if not (ref and ref > 0):
        print("❌ Could not get a reference price (even delayed). Is the market open?")
        ib.disconnect()
        return 1

    # Orders are priced in PENCE (same unit as market data), but the tick-size
    # rule is expressed in POUNDS. So look up the pound-tick at the pound price,
    # then scale it back to pence: tick_pence = pound_tick × priceMagnifier.
    pound_price = ref / pm
    pound_tick = await _tick_for_price(ib, details[0], pound_price)
    tick = pound_tick * pm
    entry = _round_tick(ref * 1.005, tick)   # slightly above ask → marketable
    stop = _round_tick(entry * 0.95, tick)
    target = _round_tick(entry * 1.08, tick)
    print(f"Delayed ~{ref:.2f} pence (tick={tick} pence) → "
          f"entry(limit)={entry} stop={stop} target={target} qty={quantity}")

    # Build + place the bracket (parent LMT + take-profit + stop-loss)
    bracket = ib.bracketOrder("BUY", quantity, limitPrice=entry,
                              takeProfitPrice=target, stopLossPrice=stop)
    print(f"Placing bracket (parent id {bracket.parent.orderId})…")
    trades = [ib.placeOrder(contract, o) for o in bracket]
    parent = trades[0]

    # Wait for the parent to resolve
    for _ in range(20):
        await asyncio.sleep(1.0)
        st = parent.orderStatus.status
        if st in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
            break
    st = parent.orderStatus.status
    filled = parent.orderStatus.filled
    avg = parent.orderStatus.avgFillPrice
    print(f"Parent order status: {st} (filled {filled} @ {avg})")

    # Record into the local DB so the dashboard reflects it. Order prices and
    # the fill are already in pence, which is how the dashboard shows LSE shares.
    if st == "Filled" and avg and avg > 0:
        entry_p, stop_p, target_p, fill_p = entry, stop, target, avg
        ref_str = f"live-first-{parent.order.orderId}"
        conn = get_connection("trading_bot.db")
        now = now_iso = datetime.now(UTC).isoformat()
        # orders.approval_id is NOT NULL, so create a signal + approval first.
        conn.execute(
            """INSERT INTO signals (symbol, exchange, signal_type, setup_version,
               action, entry_price, stop_price, target_price, size, created_at)
               VALUES (?, 'LSE', 'manual-first', 'live-v1', 'BUY', ?, ?, ?, ?, ?)""",
            (symbol, entry_p, stop_p, target_p, quantity, now),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO approvals (signal_id, decision, interface, reason,
               decided_at, order_ref)
               VALUES (?, 'APPROVED', 'live-test', 'supervised first order', ?, ?)""",
            (sid, now, ref_str),
        )
        approval_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO orders
               (approval_id, order_ref, ib_order_id, symbol, exchange, action,
                order_type, quantity, limit_price, stop_price, target_price,
                currency, status, submitted_at, updated_at)
               VALUES (?, ?, ?, ?, 'LSE', 'BUY', 'LMT', ?, ?, ?, ?, ?, 'FILLED', ?, ?)""",
            (approval_id, ref_str, parent.order.orderId, symbol,
             quantity, entry_p, stop_p, target_p, currency, now, now),
        )
        conn.commit()
        order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        ospec = OrderSpec(approval_id=approval_id, order_ref=ref_str,
                          symbol=symbol, exchange="LSE", action="BUY", quantity=quantity,
                          limit_price=entry_p, stop_price=stop_p, target_price=target_p,
                          currency=currency)
        _record_fill(conn, order_id, float(fill_p), quantity, commission=0.0, currency=currency)
        _open_position(conn, ospec, float(fill_p), order_id)
        conn.commit()
        conn.close()
        print("✅ Recorded to DB — check the Positions page on the dashboard.")

    print("\nAlso check IB Gateway and the Client Portal → the order/position should appear.")
    ib.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
