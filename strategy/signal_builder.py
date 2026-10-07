"""Turn a detector alert into a complete, risk-checked, tradeable signal.

This is the bridge that was missing between Phase 5 (detectors, which only
know "symbol X fired at price P") and Phase 2 (the risk engine, which needs
entry/stop/target to size a trade). It:

  1. Derives entry / stop / target levels from the alert and recent bars.
  2. Reads current account state (equity, cash, open positions).
  3. Runs the full pre-trade risk check (all 17 rules via run_risk_checks).
  4. ONLY if every rule passes, writes a BUY signal row for the user to
     approve. Otherwise the alert is dropped (and the reason logged).

Levels (long-only):
  entry  = alert trigger price
  stop   = entry - ATR_MULT × ATR(bars)          (volatility-based)
  target = entry + REWARD_RISK × (entry - stop)  (≥ 2:1 to satisfy rule 8)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from data.market_context import compute_regime
from data.models import Bar, Quote
from instruments.units import InstrumentSpec
from risk.checker import run_risk_checks
from risk.heat import OpenPosition
from strategy.baseline import _atr

logger = logging.getLogger(__name__)

UTC = timezone.utc

ATR_MULT = 2.0          # stop sits 2× ATR below entry
REWARD_RISK = 2.0       # target gives a 2:1 reward:risk (rule 8 minimum)
SIGNAL_TTL_HOURS = 6    # how long a signal stays valid for approval

# Sensible defaults when the account has no snapshot yet (fresh DB / demo).
_DEFAULT_EQUITY_GBP = 10_000.0
_DEFAULT_CASH_RATIO = 0.72


def _latest_account(conn: sqlite3.Connection) -> tuple[float, float]:
    """Return (equity_gbp, cash_gbp) from the most recent snapshot."""
    row = conn.execute(
        "SELECT equity_gbp, cash_gbp FROM equity_snapshots "
        "ORDER BY snapped_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return _DEFAULT_EQUITY_GBP, _DEFAULT_EQUITY_GBP * _DEFAULT_CASH_RATIO
    return float(row[0]), float(row[1])


def _open_positions(conn: sqlite3.Connection) -> list[OpenPosition]:
    """Load currently-open positions as OpenPosition objects for heat check."""
    rows = conn.execute(
        """SELECT symbol, exchange, quantity, avg_cost, stop_price
           FROM positions WHERE closed_at IS NULL"""
    ).fetchall()
    out: list[OpenPosition] = []
    for r in rows:
        stop = r["stop_price"] if r["stop_price"] is not None else r["avg_cost"]
        out.append(OpenPosition(
            symbol=r["symbol"], exchange=r["exchange"],
            quantity=float(r["quantity"]),
            entry_price_gbp=float(r["avg_cost"]),
            stop_price_gbp=float(stop),
        ))
    return out


def build_signal(
    conn: sqlite3.Connection,
    spec: InstrumentSpec,
    symbol: str,
    exchange: str,
    signal_type: str,
    trigger_price: float,
    bars: list[Bar],
    *,
    quote: Optional[Quote] = None,
    index_bars: Optional[list[Bar]] = None,
    atr_mult: float = ATR_MULT,
    reward_risk: float = REWARD_RISK,
    fx_usd_gbp: float = 1.0,
) -> Optional[int]:
    """Build and persist a tradeable BUY signal from a detector trigger.

    Returns the new signal id, or None if the trade failed any risk rule
    (in which case nothing is written).
    """
    if trigger_price <= 0 or not bars:
        logger.debug("build_signal(%s): no price/bars — skipped", symbol)
        return None

    entry = float(trigger_price)
    atr = _atr(bars)
    if atr <= 0:
        logger.debug("build_signal(%s): ATR non-positive — skipped", symbol)
        return None

    stop = entry - atr_mult * atr
    if stop <= 0:
        logger.debug("build_signal(%s): stop <= 0 — skipped", symbol)
        return None
    target = entry + reward_risk * (entry - stop)

    # Regime from the index (fall back to the instrument's own bars).
    regime = compute_regime(index_bars if index_bars is not None else bars)

    equity_gbp, cash_gbp = _latest_account(conn)
    open_positions = _open_positions(conn)

    report = run_risk_checks(
        spec=spec,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        equity_gbp=equity_gbp,
        cash_gbp=cash_gbp,
        open_positions=open_positions,
        quote=quote,
        bars_by_symbol={symbol: bars},
        conn=conn,
        regime_multiplier=regime.multiplier,
        fx_usd_gbp=fx_usd_gbp,
        # Daily/swing bot on delayed data: evaluate end-of-day so the data
        # quality gate (rule 12) accepts delayed quotes. Intraday would need a
        # live market-data subscription.
        is_intraday=False,
    )

    if not report.passed or report.sizing is None or not report.sizing.feasible:
        failed = ", ".join(c.description for c in report.failed_rules()) or "sizing"
        logger.info(
            "Signal rejected for %s/%s: %s",
            symbol, exchange, failed,
        )
        return None

    reward_risk_actual = (target - entry) / (entry - stop) if entry > stop else 0.0
    cost_r = report.costs.cost_as_r if report.costs else None
    now = datetime.now(UTC)

    conn.execute(
        """INSERT INTO signals
           (symbol, exchange, signal_type, setup_version, action,
            entry_price, stop_price, target_price, size, equity_at_signal,
            risk_gbp, reward_risk, cost_r, regime, regime_multiplier,
            risk_checks, valid_until, created_at)
           VALUES (?, ?, ?, 'signal-v1', 'BUY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            symbol, exchange, signal_type,
            round(entry, 4), round(stop, 4), round(target, 4),
            report.sizing.quantity, equity_gbp,
            report.sizing.risk_gbp, round(reward_risk_actual, 2),
            cost_r, regime.label, regime.multiplier,
            json.dumps(report.to_chips()),
            (now + timedelta(hours=SIGNAL_TTL_HOURS)).isoformat(),
            now.isoformat(),
        ),
    )
    conn.commit()
    signal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    logger.info(
        "Signal BUILT: %s/%s %s qty=%d entry=%.4g stop=%.4g target=%.4g "
        "risk=£%.0f regime=%s → signal_id=%d",
        symbol, exchange, signal_type, report.sizing.quantity,
        entry, stop, target, report.sizing.risk_gbp, regime.label, signal_id,
    )
    return signal_id
