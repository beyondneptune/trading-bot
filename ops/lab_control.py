"""Command-line lab controls (CLAUDE.md rules 9/10) — usable now, before the
dashboard Lab page exists (that's L5). Acts on the Alpaca PAPER account.

Commands:
  status                 show account, positions, open orders, suspend state
  kill                   KILL SWITCH: cancel all open orders (stops left in place)
  hard-stop on|off       HARD STOP: suspend / resume ALL trading at Alpaca
  close-everything       CLOSE EVERYTHING: liquidate all positions (needs --confirm)

Usage:
  PYTHONPATH=. .venv311/bin/python ops/lab_control.py status
  PYTHONPATH=. .venv311/bin/python ops/lab_control.py kill
  PYTHONPATH=. .venv311/bin/python ops/lab_control.py hard-stop on
  PYTHONPATH=. .venv311/bin/python ops/lab_control.py hard-stop off
  PYTHONPATH=. .venv311/bin/python ops/lab_control.py close-everything --confirm
"""

from __future__ import annotations

import argparse
import logging

logging.basicConfig(level=logging.WARNING)


def main() -> int:
    import config.settings  # loads .env
    from config.settings import require_python_311
    from broker.alpaca import AlpacaBroker
    from broker.base import startup_banner
    from lab.controls import close_everything, hard_stop, kill_switch

    require_python_311()

    parser = argparse.ArgumentParser(description="Lab command-line controls.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("kill")
    hs = sub.add_parser("hard-stop")
    hs.add_argument("state", choices=["on", "off"])
    ce = sub.add_parser("close-everything")
    ce.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    broker = AlpacaBroker()
    acct = broker.connect()
    print(startup_banner("paper", "alpaca", "lab_paper.db", acct))

    if args.cmd == "status":
        print(f"  suspended : {acct.trade_suspended_by_user}")
        print(f"  shorting  : {acct.shorting_enabled}   multiplier: {acct.multiplier}")
        positions = broker.get_positions()
        orders = broker.get_orders(open_only=True)
        print(f"  positions : {len(positions)}   open orders: {len(orders)}")
        for p in positions:
            print(f"    {p.symbol}: {p.qty} @ {p.avg_entry_price}")
        for o in orders:
            print(f"    order {o.symbol} {o.side} {o.qty} [{o.status}] {o.client_order_id}")

    elif args.cmd == "kill":
        n = kill_switch(broker)
        print(f"🛑 KILL SWITCH: cancelled {n} open order(s). Protective stops left in place.")

    elif args.cmd == "hard-stop":
        on = args.state == "on"
        hard_stop(broker, on)
        print(f"🚨 HARD STOP {'ENGAGED — Alpaca will reject new orders' if on else 'lifted'}.")

    elif args.cmd == "close-everything":
        if not args.confirm:
            print("Refusing: 'close-everything' sells ALL positions — re-run with --confirm.")
            return 2
        n = close_everything(broker, confirm=True)
        print(f"💥 CLOSE EVERYTHING: closed {n} position(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
