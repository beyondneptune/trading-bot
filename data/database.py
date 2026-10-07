from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS instruments (
    symbol              TEXT NOT NULL,
    exchange            TEXT NOT NULL,
    currency            TEXT NOT NULL,
    price_unit          TEXT NOT NULL,
    min_tick            REAL,
    price_magnifier     INTEGER NOT NULL DEFAULT 1,
    sector              TEXT,
    added_at            TEXT NOT NULL,
    removed_at          TEXT,
    PRIMARY KEY (symbol, exchange)
);

CREATE TABLE IF NOT EXISTS watchlist_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    action      TEXT NOT NULL,
    reason      TEXT,
    changed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    bar_type    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL,
    price_unit  TEXT NOT NULL,
    UNIQUE (symbol, exchange, bar_type, ts)
);

CREATE INDEX IF NOT EXISTS idx_bars_lookup
    ON bars (symbol, exchange, bar_type, ts);

CREATE TABLE IF NOT EXISTS news (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    headline        TEXT NOT NULL,
    body            TEXT,
    source          TEXT,
    symbols         TEXT,
    published_at    TEXT NOT NULL,
    retrieved_at    TEXT NOT NULL,
    materiality     TEXT
);

CREATE TABLE IF NOT EXISTS ai_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type     TEXT NOT NULL,
    symbol          TEXT,
    exchange        TEXT,
    prompt          TEXT NOT NULL,
    model           TEXT NOT NULL,
    output          TEXT NOT NULL,
    analyst_config  TEXT,
    prompt_version  TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        REAL,
    created_at      TEXT NOT NULL,
    phase           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS debates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER REFERENCES ai_reports(id),
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    exchange            TEXT NOT NULL,
    signal_type         TEXT NOT NULL,
    setup_version       TEXT NOT NULL,
    action              TEXT NOT NULL,
    entry_price         REAL,
    stop_price          REAL,
    target_price        REAL,
    size                INTEGER,
    equity_at_signal    REAL,
    risk_gbp            REAL,
    reward_risk         REAL,
    cost_r              REAL,
    regime              TEXT,
    regime_multiplier   REAL,
    risk_checks         TEXT,
    source_report_id    INTEGER REFERENCES ai_reports(id),
    valid_until         TEXT,
    expired_reason      TEXT,
    created_at          TEXT NOT NULL,
    expired_at          TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   INTEGER NOT NULL UNIQUE REFERENCES signals(id),
    decision    TEXT NOT NULL,
    decided_at  TEXT NOT NULL,
    interface   TEXT NOT NULL,
    reason      TEXT,
    confidence  INTEGER,
    mood        TEXT,
    order_ref   TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id     INTEGER NOT NULL REFERENCES approvals(id),
    order_ref       TEXT NOT NULL UNIQUE,
    ib_order_id     INTEGER,
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    action          TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    limit_price     REAL NOT NULL,
    stop_price      REAL,
    target_price    REAL,
    currency        TEXT NOT NULL,
    status          TEXT NOT NULL,
    submitted_at    TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    fill_price  REAL NOT NULL,
    quantity    INTEGER NOT NULL,
    commission  REAL,
    currency    TEXT NOT NULL,
    filled_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    avg_cost        REAL NOT NULL,
    currency        TEXT NOT NULL,
    stop_price      REAL,
    target_price    REAL,
    open_signal_id  INTEGER REFERENCES signals(id),
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    close_price     REAL,
    realised_pnl_gbp REAL,
    realised_r      REAL
);

CREATE TABLE IF NOT EXISTS position_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL REFERENCES positions(id),
    recommendation  TEXT NOT NULL,
    reasoning       TEXT NOT NULL,
    report_id       INTEGER REFERENCES ai_reports(id),
    reviewed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    equity_gbp      REAL NOT NULL,
    cash_gbp        REAL NOT NULL,
    open_positions  INTEGER NOT NULL,
    portfolio_heat  REAL NOT NULL,
    snapped_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    version     TEXT NOT NULL UNIQUE,
    config      TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS setup_registry (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL UNIQUE,
    hypothesis              TEXT NOT NULL,
    entry_rule              TEXT NOT NULL,
    stop_rule               TEXT NOT NULL,
    target_rule             TEXT NOT NULL,
    expected_holding_days   TEXT NOT NULL,
    version                 TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'experimental',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER NOT NULL UNIQUE REFERENCES signals(id),
    approval_decision TEXT,
    outcome_1h_r    REAL,
    outcome_1d_r    REAL,
    outcome_5d_r    REAL,
    tracked_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_type       TEXT NOT NULL,
    description         TEXT NOT NULL,
    evidence            TEXT NOT NULL,
    sample_size         INTEGER NOT NULL,
    proposed_config     TEXT NOT NULL,
    safety_rule_check   TEXT NOT NULL,
    source_report_id    INTEGER REFERENCES ai_reports(id),
    decision            TEXT,
    decided_at          TEXT,
    decided_by          TEXT,
    created_at          TEXT NOT NULL,
    version_created     TEXT REFERENCES strategy_versions(version)
);

CREATE TABLE IF NOT EXISTS ai_spend (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER REFERENCES ai_reports(id),
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cost_usd        REAL NOT NULL,
    recorded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mismatch_type   TEXT NOT NULL,
    details         TEXT NOT NULL,
    resolved_at     TEXT,
    resolved_by     TEXT,
    recorded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    note        TEXT,
    logged_at   TEXT NOT NULL
);

-- Identity stamp: which mode/broker this database belongs to. Written once
-- on first use; a startup guard refuses to open it under a different mode.
CREATE TABLE IF NOT EXISTS db_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

-- Raw broker trade-update stream (fills, acks, cancels). The broker is the
-- source of truth; this is the local record of everything it tells us.
CREATE TABLE IF NOT EXISTS trade_updates (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event            TEXT NOT NULL,
    order_id         TEXT,
    client_order_id  TEXT,
    symbol           TEXT,
    qty              REAL,
    filled_qty       REAL,
    filled_avg_price REAL,
    status           TEXT,
    side             TEXT,
    received_at      TEXT NOT NULL,
    raw              TEXT
);
"""


def get_connection(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()


def read_db_stamp(conn: sqlite3.Connection) -> dict:
    """Return the database's identity stamp, e.g. {'mode': 'paper',
    'broker': 'alpaca'}. Empty dict if never stamped."""
    try:
        rows = conn.execute(
            "SELECT key, value FROM db_meta WHERE key IN ('mode', 'broker')"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {r["key"]: r["value"] for r in rows}


def stamp_database(conn: sqlite3.Connection, mode: str, broker: str) -> None:
    """Write the identity stamp if not already present (idempotent)."""
    conn.execute("INSERT OR IGNORE INTO db_meta(key, value) VALUES ('mode', ?)", (mode,))
    conn.execute("INSERT OR IGNORE INTO db_meta(key, value) VALUES ('broker', ?)", (broker,))
    conn.commit()
