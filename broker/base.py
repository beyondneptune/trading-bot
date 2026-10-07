"""Broker interface + plain data types for the strategy lab.

The lab depends only on this interface. CLAUDE.md safety rules 1, 2, 9, 10:
  - paper only (account number starts 'PA'),
  - no shorting / no margin / options level 1 (set and read back),
  - kill switch cancels entries, hard stop suspends all trading,
  - the broker is the source of truth.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional


class PaperAccountError(RuntimeError):
    """Raised when the connected account is not a usable paper account."""


class OptionsNotAllowedError(RuntimeError):
    """Raised when any options order is attempted. Alpaca won't let this
    paper account go below options level 3, so instead the broker HARD-BLOCKS
    every options order here (the one place orders leave the system). No
    options trading is permitted until strategy S6 is approved in writing
    (CLAUDE.md rules 2 & 3)."""


def is_option_symbol(symbol: str) -> bool:
    """True for an OCC-style option symbol (e.g. 'SPY231215C00450000'):
    a root followed by 6 date digits, a C/P, then 8 strike digits."""
    import re
    return bool(re.fullmatch(r"[A-Z.]{1,6}\d{6}[CP]\d{8}", (symbol or "").upper()))


@dataclass
class BrokerAccount:
    account_number: str
    status: str
    cash: float
    equity: float
    shorting_enabled: bool
    multiplier: str                 # Alpaca: "1" means no margin
    options_level: Optional[int]
    trade_suspended_by_user: bool

    @property
    def is_paper_number(self) -> bool:
        return self.account_number.upper().startswith("PA")

    @property
    def masked_number(self) -> str:
        n = self.account_number
        return f"{n[:2]}…{n[-4:]}" if len(n) >= 6 else "…"


@dataclass
class BrokerPosition:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    side: str                       # "long" / "short"


@dataclass
class BrokerOrder:
    id: str
    client_order_id: str
    symbol: str
    qty: float
    side: str                       # "buy" / "sell"
    order_type: str
    status: str
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    tif: Optional[str] = None       # "day" / "gtc"

    @property
    def is_protective_stop(self) -> bool:
        """A sell stop is a protective stop (we are long-only), never an entry."""
        return self.side == "sell" and self.order_type in ("stop", "stop_limit")


class Broker(ABC):
    """What the lab needs from any broker. Read-only methods are used in L1;
    order placement arrives later and always carries a slice in client_order_id."""

    # --- connection & identity ---
    @abstractmethod
    def connect(self) -> BrokerAccount: ...

    @property
    @abstractmethod
    def is_paper(self) -> bool: ...

    @abstractmethod
    def get_account(self) -> BrokerAccount: ...

    # --- account configuration (safety rule 2) ---
    @abstractmethod
    def set_safe_account_config(self) -> BrokerAccount:
        """Set no_shorting=True, max_margin_multiplier='1',
        max_options_trading_level=1, then read back and return the account."""

    @abstractmethod
    def set_trade_suspended(self, suspended: bool) -> None:
        """Hard stop: suspend/resume ALL trading at the broker."""

    # --- read-only state (broker is source of truth, rule 10) ---
    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    def get_orders(self, open_only: bool = True) -> list[BrokerOrder]: ...

    # --- controls (kill switch / close everything) ---
    @abstractmethod
    def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns how many were cancelled."""

    @abstractmethod
    def close_all_positions(self, cancel_orders: bool = True) -> int:
        """Liquidate every position. Returns how many were closed."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """Cancel one order by id (used to pull a stop before selling)."""

    @abstractmethod
    def cancel_entry_orders(self) -> int:
        """Cancel open ENTRY orders only (buys); leave protective stops in
        place. Used by the kill switch. Returns how many were cancelled."""

    # --- order submission (the single chokepoint; HARD-BLOCKS options) ---
    @abstractmethod
    def submit_order(
        self, *, symbol: str, qty: float, side: str,
        asset_class: str = "us_equity", **kwargs,
    ) -> BrokerOrder:
        """Submit an order. MUST raise OptionsNotAllowedError for any options
        order (asset_class 'option'/'us_option' or an OCC-style symbol)."""

    # --- trade-update stream ---
    @abstractmethod
    def stream_trade_updates(self, on_update: Callable[[dict], None]) -> None:
        """Subscribe to trade updates; call on_update(event_dict) for each."""


def startup_banner(
    mode: str, broker: str, db_path: str,
    account: Optional[BrokerAccount] = None,
) -> str:
    """One-line-per-item startup banner (F4): mode, broker, database file and
    the MASKED account number — never the full number."""
    lines = [
        "──────── strategy lab ────────",
        f"  mode     : {mode}",
        f"  broker   : {broker}",
        f"  database : {db_path}",
    ]
    if account is not None:
        lines.append(f"  account  : {account.masked_number} ({account.status})")
    lines.append("──────────────────────────────")
    return "\n".join(lines)
