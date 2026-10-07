"""Read-only Alpaca paper connection check (L1). Places NO orders.

Connects, verifies it is a PA paper account, applies + reads back the safe
account config (no shorting / no margin / options level 1), and lists current
positions and open orders.

Run:  PYTHONPATH=. .venv311/bin/python ops/alpaca_connect_test.py
"""

from __future__ import annotations

import logging

logging.basicConfig(level=logging.WARNING)


def main() -> int:
    from config.settings import require_python_311
    from broker.alpaca import AlpacaBroker
    from broker.base import PaperAccountError, startup_banner

    require_python_311()
    broker = AlpacaBroker()
    try:
        acct = broker.connect()          # raises PaperAccountError if not PA
    except PaperAccountError as e:
        print(f"❌ {e}")
        return 2

    print(startup_banner("paper", "alpaca", "lab_paper.db", acct))

    print("\nApplying safe account config (no short / no margin / options L1)…")
    acct = broker.set_safe_account_config()
    print(f"  shorting_enabled : {acct.shorting_enabled}")
    print(f"  multiplier       : {acct.multiplier}   (1 = no margin)")
    print(f"  options_level    : {acct.options_level}")
    print(f"  cash             : ${acct.cash:,.2f}")
    print(f"  equity           : ${acct.equity:,.2f}")

    positions = broker.get_positions()
    orders = broker.get_orders(open_only=True)
    print(f"\nPositions: {len(positions)} | open orders: {len(orders)}")
    for p in positions[:10]:
        print(f"  {p.symbol}: {p.qty} @ {p.avg_entry_price}")

    print("\n✅ Read-only connection check complete — NO orders were placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
