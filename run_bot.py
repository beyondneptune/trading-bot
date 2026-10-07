"""The running paper-trading bot — the orchestrator that ties everything
together.

One process that:
  1. Connects to IB (mock connection in mock mode, real IB Gateway in live).
  2. Periodically SCANS the watchlist for setups → writes approvable signals.
  3. Periodically MONITORS open positions → auto-closes on stop/target,
     records a heartbeat, and reconciles.

It never places an order on its own — orders happen only when you approve a
signal in the dashboard/Telegram (that path runs its own safety gates).

Usage:
    python run_bot.py            # run forever (Ctrl-C to stop)
    python run_bot.py --once     # run a single cycle and exit (smoke test)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import Settings, load_settings
from data.calendars import is_lse_open, is_nyse_open
from data.database import get_connection, init_db
from data.live import make_feed_manager
from execution.position_monitor import monitor_positions
from instruments.units import load_watchlist
from monitor.dedup import DedupRegistry
from monitor.monitor import run_one_scan
from ops.ib_connection import make_connection

logger = logging.getLogger(__name__)
UTC = timezone.utc

MONITOR_INTERVAL_SECONDS = 30
SCAN_EVERY_N_TICKS = 2           # scan every 2nd monitor tick (~60s)
RECONCILE_EVERY_N_TICKS = 30     # reconcile ~every 15 min
REFRESH_EVERY_N_TICKS = 20       # refresh market data ~every 10 min
LEARN_EVERY_N_TICKS = 10         # propose-only learning pass ~every 5 min


@dataclass
class CycleSummary:
    scanned: bool = False
    alerts: int = 0
    positions_checked: int = 0
    positions_closed: int = 0
    reconciled: bool = False
    auto_executed: int = 0


async def auto_execute_pending(settings: Settings, conn: Any, ib_conn: Any) -> int:
    """FULL-AUTO mode: approve and execute every pending signal with no human
    prompt. All hard risk limits and the kill switch still apply (enforced in
    the signal build and in execute_approved_signal). Returns #executed."""
    from api.queue import ApprovalError, approve_signal, get_pending_signals
    from execution.executor import execute_approved_signal
    from execution.kill_switch import is_killed

    if is_killed(conn):
        logger.warning("Autonomous: kill switch active — skipping execution.")
        return 0

    executed = 0
    for sig in get_pending_signals(conn):
        sid = sig["id"]
        if sig["action"] != "BUY":
            continue
        try:
            approve_signal(conn, sid, "autonomous", reason="auto-approved")
        except ApprovalError:
            continue  # already decided
        result = await execute_approved_signal(conn, sid, settings, ib_conn=ib_conn)
        if result.executed:
            executed += 1
        logger.info("Autonomous %s: %s", sig["symbol"], result.detail)
    return executed


async def refresh_market_data(ib_conn: Any, db_path: str) -> None:
    """Pull the latest daily bars so the bot hunts fresh data, not a snapshot."""
    try:
        from data.ib_history import fetch_and_store_daily_bars
        await fetch_and_store_daily_bars(ib_conn, db_path=db_path, duration="5 D")
        logger.info("Market data refreshed.")
    except Exception as e:
        logger.error("Data refresh failed: %s", e)


def learning_pass(conn: Any) -> dict:
    """PROPOSE-ONLY self-improvement: compute performance metrics and record a
    LEARNING snapshot the dashboard can show. Never changes the strategy
    unattended (keeps Safety Rule 16)."""
    import json
    from datetime import datetime, timezone
    from learning.r_stats import compute_r_stats, load_r_multiples

    rs = compute_r_stats(load_r_multiples(conn))
    summary: dict = {
        "n_trades": rs.n,
        "expectancy_r": round(rs.expectancy, 3),
        "win_rate": round(rs.win_rate, 3),
        "profit_factor": round(rs.profit_factor, 3),
        "viable": rs.is_viable,
    }
    try:
        from learning.readiness import check_readiness
        summary["readiness"] = check_readiness(conn).overall
    except Exception:
        summary["readiness"] = False
    if rs.n >= 30:
        try:
            from learning.null_model import run_permutation_test
            summary["edge_p_value"] = round(
                run_permutation_test(load_r_multiples(conn)).p_value, 3)
        except Exception:
            pass
    conn.execute(
        "INSERT INTO system_events (event_type, note, logged_at) VALUES ('LEARNING', ?, ?)",
        (json.dumps(summary), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    logger.info("Learning pass: %s", summary)
    return summary


def _market_open(now: datetime, mock_mode: bool) -> bool:
    """In mock mode the market is always 'open' so demos work any time."""
    if mock_mode:
        return True
    return is_lse_open(now) or is_nyse_open(now)


async def run_cycle(
    settings: Settings,
    conn: Any,
    dedup: DedupRegistry,
    feed_manager: Any,
    db_path: str,
    *,
    do_scan: bool,
    do_reconcile: bool,
    ib_conn: Any = None,
) -> CycleSummary:
    """One monitor tick: optionally scan for new signals, then monitor
    open positions (stops/target/heartbeat/reconcile)."""
    summary = CycleSummary()
    now = datetime.now(UTC)

    if do_scan and _market_open(now, settings.mock_mode):
        try:
            alerts = await run_one_scan(
                settings, conn, dedup, db_path, ib_conn, feed_manager=feed_manager,
            )
            summary.scanned = True
            summary.alerts = len(alerts)
        except Exception as e:
            logger.error("Scan failed: %s", e)

    mp = monitor_positions(conn, feed_manager, reconcile=do_reconcile)
    summary.positions_checked = mp.checked
    summary.positions_closed = mp.closed
    summary.reconciled = do_reconcile

    if getattr(settings, "autonomous", False):
        summary.auto_executed = await auto_execute_pending(settings, conn, ib_conn)

    return summary


async def main_loop(once: bool = False) -> None:
    from config.settings import guard_database
    settings = load_settings()
    guard_database(settings)       # one DB per mode (filename + in-DB stamp)
    conn = get_connection(settings.database.path)
    instruments = load_watchlist()

    ib = make_connection()
    await ib.connect()   # mock: no-op + paper guard; live: connects to Gateway
    logger.info(
        "Bot online — env=%s mock=%s phase=%s account=%s",
        settings.env, settings.mock_mode, settings.phase, ib.account_id,
    )

    feed_manager = make_feed_manager(
        None if settings.mock_mode else ib, instruments
    )
    # Live feed needs a warmup so delayed-data tickers populate before scanning.
    if not settings.mock_mode and hasattr(feed_manager, "warmup"):
        logger.info("Warming up live market-data feed…")
        await feed_manager.warmup(wait_seconds=12)
    dedup = DedupRegistry()

    try:
        if once:
            summary = await run_cycle(
                settings, conn, dedup, feed_manager, settings.database.path,
                do_scan=True, do_reconcile=True, ib_conn=ib,
            )
            logger.info("Single cycle complete: %s", summary)
            return

        tick = 0
        while True:
            try:
                # Keep the data fresh so it hunts continuously, not a snapshot.
                if tick > 0 and tick % REFRESH_EVERY_N_TICKS == 0 and not settings.mock_mode:
                    await refresh_market_data(ib, settings.database.path)
                summary = await run_cycle(
                    settings, conn, dedup, feed_manager, settings.database.path,
                    do_scan=(tick % SCAN_EVERY_N_TICKS == 0),
                    do_reconcile=(tick % RECONCILE_EVERY_N_TICKS == 0),
                    ib_conn=ib,
                )
                if summary.scanned or summary.positions_closed or summary.auto_executed:
                    logger.info("Cycle %d: %s", tick, summary)
                # Propose-only self-improvement snapshot.
                if tick % LEARN_EVERY_N_TICKS == 0:
                    learning_pass(conn)
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception as e:
                # One bad cycle must not kill an all-day run; log and continue.
                logger.error("Cycle %d failed (continuing): %s", tick, e)
            tick += 1
            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown requested — stopping bot.")
    finally:
        await ib.disconnect()
        conn.close()


def main() -> None:
    from config.settings import require_python_311
    require_python_311()
    parser = argparse.ArgumentParser(description="Run the paper-trading bot.")
    parser.add_argument("--once", action="store_true",
                        help="Run a single cycle and exit (smoke test).")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main_loop(once=args.once))


if __name__ == "__main__":
    main()
