"""Alpaca paper-trading adapter. PAPER ONLY — TradingClient is always created
with paper=True and the endpoint is never read from config (CLAUDE.md rule 1).

alpaca-py is imported lazily so importing this module never requires it.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from broker.base import (
    Broker,
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    PaperAccountError,
)

logger = logging.getLogger(__name__)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


class AlpacaBroker(Broker):
    """Read + control operations against an Alpaca PAPER account."""

    def __init__(self) -> None:
        self._client = None  # alpaca TradingClient, created in connect()

    # ------------------------------------------------------------------ connect
    def connect(self) -> BrokerAccount:
        from alpaca.trading.client import TradingClient

        key = os.getenv("ALPACA_API_KEY_ID")
        secret = os.getenv("ALPACA_API_SECRET_KEY")
        if not key or not secret:
            raise PaperAccountError(
                "ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set in .env."
            )
        # paper=True is hard-coded; there is NO live path and no env endpoint.
        self._client = TradingClient(key, secret, paper=True)
        account = self.get_account()
        if not account.is_paper_number:
            raise PaperAccountError(
                f"Connected account {account.masked_number} does not look like "
                "a paper account (expected number starting 'PA'). Refusing to "
                "trade — confirm this is your paper account."
            )
        logger.info("Connected to Alpaca paper account %s", account.masked_number)
        return account

    @property
    def is_paper(self) -> bool:
        return True

    # ------------------------------------------------------------------ account
    def _require(self):
        if self._client is None:
            raise RuntimeError("Broker not connected — call connect() first.")
        return self._client

    def get_account(self) -> BrokerAccount:
        client = self._require()
        a = client.get_account()
        cfg = client.get_account_configurations()
        return BrokerAccount(
            account_number=str(a.account_number),
            status=str(getattr(a, "status", "")),
            cash=_f(a.cash),
            equity=_f(a.equity),
            shorting_enabled=bool(getattr(a, "shorting_enabled", True)),
            multiplier=str(getattr(a, "multiplier", "")),
            options_level=_safe_int(getattr(cfg, "max_options_trading_level", None)),
            trade_suspended_by_user=bool(getattr(a, "trade_suspended_by_user", False)),
        )

    def set_safe_account_config(self) -> BrokerAccount:
        """Rule 2 (amended): set no_shorting=True and max_margin_multiplier='1',
        read them back, and REFUSE (raise) if either did not take.

        Options level is NOT lowered here — Alpaca forbids going below 3 on this
        account. Options trading is instead HARD-BLOCKED in submit_order, and
        no options orders are allowed until S6 is approved in writing.
        """
        client = self._require()
        cfg = client.get_account_configurations()
        cfg.no_shorting = True
        cfg.max_margin_multiplier = "1"
        updated = client.set_account_configurations(cfg)
        if not (bool(updated.no_shorting) is True
                and str(updated.max_margin_multiplier) == "1"):
            raise PaperAccountError(
                "Account configuration did not take: "
                f"no_shorting={updated.no_shorting}, "
                f"max_margin_multiplier={updated.max_margin_multiplier}. "
                "Refusing to trade."
            )
        logger.info("Safe account config applied (no shorting / no margin).")
        return self.get_account()

    def submit_order(self, *, symbol, qty, side, asset_class="us_equity", **kwargs):
        """Single order chokepoint — HARD-BLOCKS options (rules 2 & 3). Equity
        order placement is implemented at the supervised SPY test step."""
        from broker.base import OptionsNotAllowedError, is_option_symbol
        if asset_class in ("option", "us_option") or is_option_symbol(symbol):
            raise OptionsNotAllowedError(
                f"Options order blocked at broker: {symbol}. No options trading "
                "until S6 is approved in writing."
            )
        raise NotImplementedError(
            "Equity order placement is built at the supervised SPY test (L1)."
        )

    def set_trade_suspended(self, suspended: bool) -> None:
        """Hard stop: Alpaca account-config suspend_trade."""
        client = self._require()
        cfg = client.get_account_configurations()
        cfg.suspend_trade = bool(suspended)
        client.set_account_configurations(cfg)
        logger.warning("Alpaca suspend_trade set to %s", suspended)

    # ------------------------------------------------------------- read-only
    def get_positions(self) -> list[BrokerPosition]:
        client = self._require()
        out = []
        for p in client.get_all_positions():
            out.append(BrokerPosition(
                symbol=str(p.symbol), qty=_f(p.qty),
                avg_entry_price=_f(p.avg_entry_price),
                market_value=_f(p.market_value),
                side=str(getattr(p, "side", "long")).lower().replace("positionside.", ""),
            ))
        return out

    def get_orders(self, open_only: bool = True) -> list[BrokerOrder]:
        client = self._require()
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        req = GetOrdersRequest(
            status=QueryOrderStatus.OPEN if open_only else QueryOrderStatus.ALL
        )
        out = []
        for o in client.get_orders(req):
            out.append(BrokerOrder(
                id=str(o.id), client_order_id=str(o.client_order_id),
                symbol=str(o.symbol), qty=_f(o.qty),
                side=str(o.side).lower().replace("orderside.", ""),
                order_type=str(getattr(o, "order_type", getattr(o, "type", ""))).lower(),
                status=str(o.status).lower().replace("orderstatus.", ""),
                limit_price=_f(o.limit_price) if o.limit_price else None,
                stop_price=_f(o.stop_price) if o.stop_price else None,
            ))
        return out

    # ------------------------------------------------------------- controls
    def cancel_all_orders(self) -> int:
        client = self._require()
        resp = client.cancel_orders()
        return len(resp) if resp else 0

    def close_all_positions(self, cancel_orders: bool = True) -> int:
        client = self._require()
        resp = client.close_all_positions(cancel_orders=cancel_orders)
        return len(resp) if resp else 0

    # ------------------------------------------------------------- stream
    def stream_trade_updates(self, on_update: Callable[[dict], None]) -> None:
        """Set up the trade-update stream. NOTE: the returned stream's run()
        blocks; the lab runs it in its own task. Imported lazily."""
        from alpaca.trading.stream import TradingStream
        key = os.getenv("ALPACA_API_KEY_ID")
        secret = os.getenv("ALPACA_API_SECRET_KEY")
        stream = TradingStream(key, secret, paper=True)

        async def _handler(data):
            on_update(data)   # pass the raw TradeUpdate through to the recorder

        stream.subscribe_trade_updates(_handler)
        return stream


def _safe_int(x) -> Optional[int]:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
