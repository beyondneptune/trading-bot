"""The paper lab process — L1 skeleton.

Startup safety sequence (CLAUDE.md rules 1, 2, 9, 10):
  1. connect and verify it is a PA paper account,
  2. set + read back no-short / no-margin (refuse if either differs),
  3. record the options level and alert if it changed,
  4. check whether a HARD STOP is engaged (suspend_trade) and pause entries,
  5. reconcile the database against the broker (source of truth).
Then reconcile again every 5 minutes with a heartbeat.

The trade-update stream and the full slice scheduler arrive in later phases.

Run (paper):
  TRADING_MOCK=false TRADING_BACKEND=alpaca TRADING_DB=lab_paper.db \
    PYTHONPATH=. .venv311/bin/python lab/run_lab.py --once
"""

from __future__ import annotations

import argparse
import logging
import time

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_SECONDS = 300   # 5 minutes


def startup_checks(broker, conn, mode: str, db_path: str):
    """Run the full startup safety sequence. Returns the account."""
    from broker.base import startup_banner
    from lab.controls import check_options_level, reconcile

    account = broker.connect()                       # raises if not a PA account
    print(startup_banner(mode, "alpaca", db_path, account))

    account = broker.set_safe_account_config()       # no short / no margin; refuse if differs
    logger.info("Account locked: shorting=%s multiplier=%s",
                account.shorting_enabled, account.multiplier)

    level, changed = check_options_level(conn, broker)
    logger.info("Options level=%s (changed=%s) — options orders are hard-blocked.",
                level, changed)

    if account.trade_suspended_by_user:
        logger.warning("HARD STOP is ENGAGED at the broker — no new entries will "
                       "be placed until it is lifted.")

    res = reconcile(broker, conn)
    logger.info("Startup reconcile: ok=%s, %d mismatch(es).", res.ok, len(res.mismatches))
    if not res.ok:
        logger.warning("Reconcile mismatches: %s", res.mismatches)
    return account


def main_loop(once: bool = False) -> None:
    from config.settings import (
        guard_database, load_settings, mode_of, require_python_311,
    )
    from data.database import get_connection
    from broker import make_broker
    from execution.heartbeat import record_heartbeat
    from lab.controls import reconcile

    require_python_311()
    settings = load_settings()
    guard_database(settings)
    conn = get_connection(settings.database.path)
    broker = make_broker(settings)   # AlpacaBroker when trading_backend=alpaca

    startup_checks(broker, conn, mode_of(settings), settings.database.path)
    record_heartbeat(conn)

    if once:
        logger.info("Startup checks complete (--once).")
        return

    while True:
        try:
            time.sleep(RECONCILE_INTERVAL_SECONDS)
            res = reconcile(broker, conn)
            record_heartbeat(conn)
            if not res.ok:
                logger.warning("RECONCILE MISMATCH — pausing new entries. %s",
                               res.mismatches)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Lab stopping.")
            break
        except Exception as e:
            logger.error("Reconcile cycle failed (continuing): %s", e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the paper lab.")
    parser.add_argument("--once", action="store_true",
                        help="Run startup checks once and exit.")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main_loop(once=args.once)


if __name__ == "__main__":
    main()
