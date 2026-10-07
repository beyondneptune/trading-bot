"""Intraday monitor: polls price data for every watchlist symbol and fires
alerts through the detector → filter → fast-check → signal pipeline.

One scan cycle:
  1. For each symbol, get current quote + recent bars
  2. Run all 5 detectors
  3. For each new alert: run filters, de-dup check
  4. Fast AI check (haiku, ~$0.001 per alert)
  5. If YES/SKIP: write signal to DB and notify

The monitor does NOT place orders — that is Phase 6 execution.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config.settings import Settings
from data.live import make_feed_manager
from data.news import make_news_provider
from data.history import get_daily_bars_sync
from instruments.units import InstrumentSpec, load_watchlist
from monitor.alerts import Alert, AlertState, AlertType
from monitor.dedup import DedupRegistry
from monitor.detectors import (
    detect_breakout,
    detect_dip_to_support,
    detect_breaking_news,
    detect_position_pressure,
)
from monitor.fast_check import fast_check
from monitor.filters import run_all_filters

logger = logging.getLogger(__name__)
UTC = timezone.utc

SCAN_INTERVAL_SECONDS = 30


def _bars_summary(bars: list, n: int = 10) -> str:
    """Compact OHLCV summary for the fast AI check prompt."""
    recent = bars[-n:]
    lines = []
    for b in recent:
        ts = b.ts.strftime("%m-%d %H:%M") if hasattr(b.ts, "strftime") else str(b.ts)
        lines.append(f"{ts} O={b.open:.4g} H={b.high:.4g} L={b.low:.4g} C={b.close:.4g}")
    return "\n".join(lines)


# Alert types that represent a new entry and should become an approvable
# BUY signal. Others (breaking news, position pressure) are informational.
_ENTRY_ALERTS = {AlertType.BREAKOUT, AlertType.DIP_TO_SUPPORT, AlertType.PLANNED_ENTRY}


async def _process_alert(
    alert: Alert,
    dedup: DedupRegistry,
    settings: Settings,
    conn: sqlite3.Connection,
    bars_summary: str,
    bars: list,
    quote=None,
) -> None:
    """Filters, de-dupes, fast-checks, and — for entry setups — runs the full
    pre-trade risk check and writes a tradeable BUY signal."""
    from instruments.units import find_instrument, load_watchlist
    from data.models import DataQuality
    from strategy.signal_builder import build_signal

    spec = find_instrument(load_watchlist(), alert.symbol, alert.exchange)
    if spec is None:
        alert.suppress("symbol not in watchlist")
        return

    # De-duplication
    if dedup.is_duplicate(alert.symbol, alert.alert_type.value):
        alert.suppress("duplicate within cooldown window")
        return

    # Filters — in mock mode, treat quality as LIVE so filters don't suppress
    ok, reason = run_all_filters(
        spec,
        quality=DataQuality.LIVE,
        spread_pct=0.001 if settings.mock_mode else 0.005,
        allow_delayed=False,
    )
    if not ok:
        alert.suppress(reason)
        logger.debug("Alert %s/%s suppressed: %s", alert.symbol, alert.alert_type, reason)
        return

    # Fast AI check
    verdict = await fast_check(
        symbol=alert.symbol,
        alert_type=alert.alert_type.value,
        trigger_price=alert.trigger_price,
        bars_summary=bars_summary,
        note=alert.note,
        mock_mode=settings.mock_mode,
    )

    if verdict == "NO":
        alert.suppress("fast AI check returned NO")
        return

    # Non-entry alerts (news, position pressure) are informational only —
    # they notify but do not create an approvable BUY order.
    if alert.alert_type not in _ENTRY_ALERTS:
        alert.mark_live()
        dedup.record(alert.symbol, alert.alert_type.value)
        logger.info(
            "Alert NOTED (informational): %s %s @ %.4g",
            alert.symbol, alert.alert_type.value, alert.trigger_price,
        )
        return

    # In mock mode the feed is MOCK-quality, which the data-quality gate
    # rejects for intraday. Present a LIVE-quality copy so the pipeline runs.
    rc_quote = quote
    if quote is not None and settings.mock_mode:
        rc_quote = quote.model_copy(
            update={"quality": DataQuality.LIVE, "ts": datetime.now(UTC)}
        )

    signal_id = build_signal(
        conn, spec,
        alert.symbol, alert.exchange, alert.alert_type.value,
        alert.trigger_price, bars, quote=rc_quote,
    )
    if signal_id is None:
        alert.suppress("failed pre-trade risk checks")
        return

    alert.mark_live()
    dedup.record(alert.symbol, alert.alert_type.value)
    alert.mark_sent(signal_id)
    logger.info(
        "Alert SENT: %s %s @ %.4g → signal_id=%d",
        alert.symbol, alert.alert_type.value, alert.trigger_price, signal_id,
    )


async def run_one_scan(
    settings: Settings,
    conn: sqlite3.Connection,
    dedup: DedupRegistry,
    db_path: str = "",
    ib_conn=None,
    feed_manager=None,
) -> list[Alert]:
    """Run one full scan cycle. Returns all alerts generated (including
    suppressed ones, for logging/testing).

    ib_conn is the live IB connection; ignored in mock mode. Pass a
    pre-warmed feed_manager (live mode) so quotes are already populated;
    otherwise one is created (and warmed, if live) here.
    """
    instruments = load_watchlist()
    if feed_manager is None:
        feed_manager = make_feed_manager(ib_conn, instruments)
        if not settings.mock_mode and hasattr(feed_manager, "warmup"):
            await feed_manager.warmup(wait_seconds=12)
    news_provider = make_news_provider()

    alerts: list[Alert] = []
    now = datetime.now(UTC)

    for spec in instruments:
        try:
            # Subscribe and get quote
            feed_manager.subscribe(spec.symbol, spec.exchange)
            quote = feed_manager.get_quote(spec.symbol, spec.exchange)
            if quote is None:
                continue

            current_price = quote.mid

            # Get cached bars (use db_path if provided, else skip)
            bars = get_daily_bars_sync(spec, db_path) if db_path else []
            summary = _bars_summary(bars) if bars else ""

            # Run detectors
            candidates: list[Optional[Alert]] = [
                detect_breakout(spec.symbol, spec.exchange, bars),
                detect_dip_to_support(spec.symbol, spec.exchange, bars),
            ]

            # Breaking news
            news = await news_provider.fetch(spec.symbol, max_items=5)
            candidates.append(
                detect_breaking_news(spec.symbol, spec.exchange, news)
            )

            # Position pressure — check open positions in DB
            open_positions = conn.execute(
                "SELECT stop_price, target_price FROM positions "
                "WHERE symbol=? AND closed_at IS NULL",
                (spec.symbol,),
            ).fetchall()
            for pos in open_positions:
                if pos["stop_price"] and pos["target_price"]:
                    candidates.append(
                        detect_position_pressure(
                            spec.symbol, spec.exchange,
                            current_price,
                            pos["stop_price"],
                            pos["target_price"],
                        )
                    )

            # Process non-None alerts
            for alert in candidates:
                if alert is None:
                    continue
                alerts.append(alert)
                await _process_alert(
                    alert, dedup, settings, conn, summary, bars, quote
                )

        except Exception as e:
            logger.error("Scan error for %s: %s", spec.symbol, e)

    # Expire stale pending alerts (alerts that were never processed)
    for alert in alerts:
        if alert.state == AlertState.PENDING and alert.is_expired(now):
            alert.expire()

    return alerts


async def run_monitor(
    settings: Settings,
    conn: sqlite3.Connection,
    db_path: str = "",
) -> None:
    """Continuous monitor loop. Runs until cancelled."""
    dedup = DedupRegistry()
    logger.info("Intraday monitor started (scan every %ds)", SCAN_INTERVAL_SECONDS)
    while True:
        try:
            alerts = await run_one_scan(settings, conn, dedup, db_path=db_path)
            live = sum(1 for a in alerts if a.state.value in ("live", "sent"))
            if live:
                logger.info("Scan complete: %d alerts sent, %d total", live, len(alerts))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Monitor scan failed: %s", e)
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)
