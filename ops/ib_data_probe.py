"""Read-only market-data probe. Places NO orders.

Connects to Gateway and asks for a snapshot quote on a few instruments to
see what data the paper account can access (live vs delayed) and in what
price units LSE instruments come back. Informs how we price the first order.
"""

from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.ERROR)

HOST, PORT, CLIENT_ID = "127.0.0.1", 4002, 12

# (symbol, exchange, currency) — one GBP ETF, one USD ETF, one GBX share
TEST = [
    ("ISF", "LSE", "GBP"),
    ("VUSA", "LSE", "USD"),
    ("AZN", "LSE", "GBP"),
]

_MDT = {1: "LIVE", 2: "FROZEN", 3: "DELAYED", 4: "DELAYED-FROZEN"}


async def _probe(ib, symbol, exchange, currency):
    from ib_async import Stock
    # LSE instruments route via SMART with primaryExchange=LSE.
    contract = Stock(symbol, "SMART", currency, primaryExchange=exchange)
    try:
        await ib.qualifyContractsAsync(contract)
    except Exception as e:
        return f"{symbol:5} could not qualify contract ({e})"
    if not contract.conId:
        return f"{symbol:5} NOT FOUND (no security definition for {currency} on {exchange})"

    ticker = ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
    # wait up to ~6s for a price to arrive
    for _ in range(12):
        await asyncio.sleep(0.5)
        if any(v == v and v > 0 for v in (ticker.last, ticker.close, ticker.bid, ticker.ask)):
            break
    mdt = _MDT.get(getattr(ticker, "marketDataType", 0), "?")
    ib.cancelMktData(contract)

    def fmt(v):
        return f"{v:.2f}" if (v == v and v is not None) else "—"

    return (f"{symbol:5} [{mdt:14}] bid={fmt(ticker.bid)} ask={fmt(ticker.ask)} "
            f"last={fmt(ticker.last)} close={fmt(ticker.close)}")


async def main() -> int:
    from ib_async import IB
    ib = IB()
    print(f"Connecting to {HOST}:{PORT} (read-only)…")
    try:
        await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, readonly=True, timeout=15)
    except Exception as e:
        print(f"❌ Could not connect: {e}")
        return 1

    for mdt in (1, 3):   # try LIVE first, then DELAYED
        ib.reqMarketDataType(mdt)
        print(f"\n--- Requesting {_MDT[mdt]} data ---")
        for sym, exch, ccy in TEST:
            print("  " + await _probe(ib, sym, exch, ccy))

    ib.disconnect()
    print("\nDone. (read-only — nothing was traded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
