"""Safe, read-only connection test to IB Gateway.

Connects read-only (cannot place orders), confirms the account is a paper
(DU...) account, and prints the account balances. Proves the bot can talk
to Gateway before we go any further.

Run:  PYTHONPATH=. .venv311/bin/python ops/ib_connect_test.py
"""

from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.WARNING)

HOST = "127.0.0.1"
PORT = 4002          # IB Gateway paper port
CLIENT_ID = 11       # distinct from the bot's default (1)


async def main() -> int:
    from ib_async import IB
    from risk.guards import assert_paper_account

    ib = IB()
    print(f"Connecting to IB Gateway at {HOST}:{PORT} (read-only)…")
    try:
        await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, readonly=True, timeout=15)
    except Exception as e:
        print(f"❌ Could not connect: {e}")
        print("   • Is Gateway logged in and running?")
        print("   • Configure → Settings → API → Settings: 'Enable ActiveX and")
        print("     Socket Clients' checked, Socket port = 4002.")
        print("   • If a popup appeared in Gateway asking to accept the")
        print("     connection, click Accept (or add 127.0.0.1 to Trusted IPs).")
        return 1

    accounts = ib.managedAccounts()
    acct = accounts[0] if accounts else ""
    print(f"Connected. Managed account(s): {accounts}")

    try:
        assert_paper_account(acct)
        print(f"✅ Paper account confirmed: {acct}")
    except SystemExit:
        print(f"⚠️  {acct} is NOT a paper (DU) account — the bot will refuse to")
        print("    trade on it. Gateway is logged into a LIVE account. Log into")
        print("    Gateway with your PAPER account instead.")
        ib.disconnect()
        return 2

    try:
        summary = await ib.accountSummaryAsync(acct)
        wanted = {"NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds"}
        print("Account balances:")
        for row in summary:
            if row.tag in wanted:
                print(f"   {row.tag}: {row.value} {row.currency}")
    except Exception as e:
        print(f"(Could not fetch balances: {e})")

    ib.disconnect()
    print("Disconnected cleanly. ✅  Connection test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
