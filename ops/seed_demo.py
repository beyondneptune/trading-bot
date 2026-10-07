"""Seed the database with realistic mock data for a dashboard walkthrough.

Populates: pending signals (Today), equity curve (Performance), an open
position (Positions), and decided approvals (Journal). Mock/paper only —
safe to run against trading_bot.db. Idempotent-ish: clears demo rows first.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from data.database import get_connection, init_db

UTC = timezone.utc
DB = "trading_bot.db"


def main() -> None:
    init_db(DB)
    conn = get_connection(DB)
    now = datetime.now(UTC)

    # Clean slate for demo tables
    for tbl in ("approvals", "signals", "positions", "equity_snapshots"):
        conn.execute(f"DELETE FROM {tbl}")
    conn.commit()

    # --- Equity curve: 20 daily snapshots, 10,000 → ~10,420 with wobble ---
    curve = [10000, 10040, 9990, 10080, 10150, 10110, 10210, 10180, 10260,
             10230, 10190, 10300, 10350, 10290, 10380, 10340, 10410, 10370,
             10400, 10420]
    for i, eq in enumerate(curve):
        snapped = now - timedelta(days=len(curve) - 1 - i)
        conn.execute(
            """INSERT INTO equity_snapshots
               (equity_gbp, cash_gbp, open_positions, portfolio_heat, snapped_at)
               VALUES (?, ?, ?, ?, ?)""",
            (float(eq), float(eq) * 0.72, 1 if i > 12 else 0,
             0.018 if i > 12 else 0.0, snapped.isoformat()),
        )

    # --- Three pending signals (Today page) ---
    pending = [
        # symbol, exch, type, action, entry, stop, target, size, risk, rr, cost_r, regime, mult
        ("AZN",  "LSE", "breakout",   "BUY", 11250.0, 10900.0, 11950.0, 28,  98.0, 2.0, 0.07, "Normal",   1.0),
        ("SHEL", "LSE", "dip_to_sup", "BUY",  2680.0,  2600.0,  2840.0, 120, 96.0, 2.0, 0.09, "Normal",   1.0),
        ("VOD",  "LSE", "breakout",   "BUY",    74.5,    71.0,    81.5, 1300, 45.5, 2.0, 0.12, "Elevated", 0.5),
    ]
    for (sym, exch, stype, action, entry, stop, target, size,
         risk, rr, cost_r, regime, mult) in pending:
        conn.execute(
            """INSERT INTO signals
               (symbol, exchange, signal_type, setup_version, action,
                entry_price, stop_price, target_price, size, equity_at_signal,
                risk_gbp, reward_risk, cost_r, regime, regime_multiplier,
                risk_checks, valid_until, created_at)
               VALUES (?, ?, ?, 'v1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sym, exch, stype, action, entry, stop, target, size, 10420.0,
             risk, rr, cost_r, regime, mult, "all_passed",
             (now + timedelta(hours=3)).isoformat(), now.isoformat()),
        )

    # --- One decided signal + approval each for the Journal page ---
    def decided(sym, action, decision, conf, mood, note, hrs_ago):
        conn.execute(
            """INSERT INTO signals
               (symbol, exchange, signal_type, setup_version, action,
                entry_price, stop_price, target_price, size, equity_at_signal,
                risk_gbp, reward_risk, cost_r, regime, regime_multiplier,
                risk_checks, created_at)
               VALUES (?, 'LSE', 'breakout', 'v1', ?, 100, 97, 106, 50, 10400,
                       80, 2.0, 0.08, 'Normal', 1.0, 'all_passed', ?)""",
            (sym, action, (now - timedelta(hours=hrs_ago)).isoformat()),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO approvals
               (signal_id, decision, interface, reason, confidence, mood,
                decided_at, order_ref)
               VALUES (?, ?, 'dashboard', ?, ?, ?, ?, ?)""",
            (sid, decision, note, conf, mood,
             (now - timedelta(hours=hrs_ago)).isoformat(),
             f"demo-{sym}-{hrs_ago}" if decision == "APPROVED" else None),
        )

    decided("BP", "BUY", "APPROVED", 4, "Confident",
            "Clean breakout above resistance on volume.", 26)
    decided("TSCO", "BUY", "REJECTED", 2, "Sceptical",
            "Cost too close to limit, spread looked wide.", 25)

    # --- One open position (Positions page) ---
    conn.execute(
        """INSERT INTO positions
           (symbol, exchange, quantity, avg_cost, currency,
            stop_price, target_price, opened_at)
           VALUES ('BP', 'LSE', 50, 100.0, 'GBP', 97.0, 106.0, ?)""",
        ((now - timedelta(hours=26)).isoformat(),
    ))

    conn.commit()
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("signals", "approvals", "positions", "equity_snapshots")
    }
    conn.close()
    print("Seeded demo data:", counts)


if __name__ == "__main__":
    sys.exit(main())
