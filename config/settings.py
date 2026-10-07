from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path


def require_python_311() -> None:
    """Refuse to start on Python older than 3.11 (the lab requires 3.11).

    Call at every entry point: the bot, the API, and the scripts.
    """
    if sys.version_info < (3, 11):
        raise RuntimeError(
            f"Python 3.11+ is required; this is "
            f"{sys.version_info.major}.{sys.version_info.minor}. "
            "Use the .venv311 environment."
        )

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

_CONFIG_DIR = Path(__file__).parent


class IBConfig(BaseModel):
    host: str = "127.0.0.1"
    tws_port: int = 7497
    gateway_port: int = 4002
    client_id: int = 1
    readonly: bool = True

    @property
    def port(self) -> int:
        return int(os.getenv("IB_GATEWAY_PORT", self.gateway_port))


class DatabaseConfig(BaseModel):
    path: str = "trading_bot.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/trading_bot.log"


class Settings(BaseModel):
    env: str = "paper"
    mock_mode: bool = True
    phase: int = 0
    autonomous: bool = False   # full-auto paper trading (auto-approve + execute)
    # Which broker the trading path uses: "none" disables all live order routing
    # (L0 default — IB code kept but switched off), "ib" = Interactive Brokers,
    # "alpaca" = Alpaca paper (from L1).
    trading_backend: str = "none"
    ib: IBConfig = IBConfig()
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()


class RegimeConfig(BaseModel):
    elevated_vol_percentile: int = 60
    stressed_vol_percentile: int = 80


class CostsConfig(BaseModel):
    commission_per_trade: float = 1.00
    uk_stamp_duty: float = 0.005
    default_spread_estimate: float = 0.001
    slippage_per_side: float = 0.001
    paper_slippage_haircut: float = 0.001


class LiquidityConfig(BaseModel):
    min_avg_volume: int = 100_000
    min_price: float = 0.50
    max_spread_pct: float = 0.003


class RiskLimits(BaseModel):
    max_risk_per_trade: float = 0.01
    max_position_size: float = 0.15
    max_sector_exposure: float = 0.30
    min_cash_ratio: float = 0.10
    max_positions: int = 8
    max_portfolio_heat: float = 0.05
    max_cluster_heat: float = 0.02
    cluster_correlation_threshold: float = 0.80
    max_cost_per_r: float = 0.15
    min_reward_risk: float = 2.0
    daily_loss_limit: float = 0.02
    weekly_drawdown_circuit: float = 0.05
    peak_drawdown_circuit: float = 0.10
    max_slippage: float = 0.003
    monthly_ai_budget_gbp: float = 50.0
    regime: RegimeConfig = RegimeConfig()
    costs: CostsConfig = CostsConfig()
    liquidity: LiquidityConfig = LiquidityConfig()


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    path = _CONFIG_DIR / "settings.yaml"
    data: dict = {}
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    # env overrides
    ib_data = data.get("ib", {})
    if os.getenv("IB_HOST"):
        ib_data["host"] = os.getenv("IB_HOST")
    if os.getenv("IB_GATEWAY_PORT"):
        ib_data["gateway_port"] = int(os.getenv("IB_GATEWAY_PORT"))
    if os.getenv("IB_CLIENT_ID"):
        ib_data["client_id"] = int(os.getenv("IB_CLIENT_ID"))
    if ib_data:
        data["ib"] = ib_data
    # Top-level env overrides — let a live/autonomous run be launched without
    # editing settings.yaml (which keeps safe mock defaults for tests/dashboard).
    if os.getenv("TRADING_MOCK") is not None:
        data["mock_mode"] = os.getenv("TRADING_MOCK").lower() in ("1", "true", "yes")
    if os.getenv("TRADING_AUTONOMOUS") is not None:
        data["autonomous"] = os.getenv("TRADING_AUTONOMOUS").lower() in ("1", "true", "yes")
    if os.getenv("TRADING_DB"):
        db = data.get("database", {})
        db["path"] = os.getenv("TRADING_DB")
        data["database"] = db
    if os.getenv("TRADING_BACKEND"):
        data["trading_backend"] = os.getenv("TRADING_BACKEND")
    return Settings(**data)


def assert_db_for_mode(settings: Settings) -> None:
    """Startup guard (CLAUDE.md: one database per mode).

    Mock mode must use a *mock* database; non-mock (paper/live) must NOT use
    the mock database — so recorded paper state can never be polluted by
    synthetic data and vice versa. Test/tmp databases are exempt.
    """
    path = settings.database.path.lower()
    if "test" in path or "tmp" in path or path == ":memory:":
        return
    if settings.mock_mode and "mock" not in path:
        raise RuntimeError(
            f"Mock mode requires a database whose name contains 'mock'; "
            f"got '{settings.database.path}'. Refusing to start."
        )
    if (not settings.mock_mode) and "mock" in path:
        raise RuntimeError(
            f"Non-mock (paper/live) mode must not use the mock database "
            f"'{settings.database.path}'. Refusing to start."
        )


def mode_of(settings: Settings) -> str:
    """The data mode this process runs in: 'mock' or 'paper'."""
    return "mock" if settings.mock_mode else "paper"


def guard_database(settings: Settings) -> dict:
    """Full startup database guard (F2): the filename check PLUS an identity
    stamp stored *inside* the database. Initialises the DB, then either stamps
    it (first use) or verifies its stamp matches this process's mode/broker,
    refusing to start on a mismatch. Returns the stamp.
    """
    from data.database import (
        get_connection, init_db, read_db_stamp, stamp_database,
    )
    assert_db_for_mode(settings)          # fast filename sanity check first
    path = settings.database.path
    mode = mode_of(settings)
    broker = settings.trading_backend
    init_db(path)
    conn = get_connection(path)
    try:
        stamp = read_db_stamp(conn)
        if stamp:
            if stamp.get("mode") != mode:
                raise RuntimeError(
                    f"Database '{path}' is stamped mode='{stamp.get('mode')}' "
                    f"but this process runs mode='{mode}'. Refusing to start."
                )
            # A read-only process (broker 'none') may open any matching-mode DB;
            # a trading process must match the stamped broker.
            if broker != "none" and stamp.get("broker") not in (broker, "none"):
                raise RuntimeError(
                    f"Database '{path}' is stamped broker='{stamp.get('broker')}' "
                    f"but trading_backend='{broker}'. Refusing to start."
                )
            return stamp
        stamp_database(conn, mode, broker)
        return {"mode": mode, "broker": broker}
    finally:
        conn.close()


@lru_cache(maxsize=1)
def load_risk_limits() -> RiskLimits:
    path = _CONFIG_DIR / "risk_limits.yaml"
    data: dict = {}
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    # Aggressive paper-trading profile (opt-in via env) — more trades, more
    # risk. Defaults (and the test suite) are untouched unless this is set.
    if os.getenv("AGGRESSIVE_RISK", "").lower() in ("1", "true", "yes"):
        data["max_risk_per_trade"] = 0.03   # 3% risk/trade (was 1%)
        data["max_position_size"] = 0.30    # 30% position cap (was 15%)
        data["max_positions"] = 20          # up to 20 open (was 8)
        data["max_portfolio_heat"] = 0.25   # 25% total heat (was 5%)
        data["max_cost_per_r"] = 0.60       # tolerate costlier trades (was 0.15)
        data["min_reward_risk"] = 1.5       # accept 1.5:1 R:R (was 2:1)
    return RiskLimits(**data)
