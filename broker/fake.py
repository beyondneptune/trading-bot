"""In-memory fake broker for tests — no network. Lets the lab's connection,
account-config, reconciliation and control logic be tested deterministically.
"""

from __future__ import annotations

from typing import Callable, Optional

from broker.base import Broker, BrokerAccount, BrokerOrder, BrokerPosition


class FakeBroker(Broker):
    def __init__(
        self,
        account_number: str = "PA3XYZ1234",
        shorting_enabled: bool = True,
        multiplier: str = "2",
        options_level: Optional[int] = 3,
        suspended: bool = False,
    ) -> None:
        self._acct = BrokerAccount(
            account_number=account_number, status="ACTIVE",
            cash=100_000.0, equity=100_000.0,
            shorting_enabled=shorting_enabled, multiplier=multiplier,
            options_level=options_level, trade_suspended_by_user=suspended,
        )
        self._positions: list[BrokerPosition] = []
        self._orders: list[BrokerOrder] = []
        self._connected = False
        self.updates_cb: Optional[Callable[[dict], None]] = None

    def connect(self) -> BrokerAccount:
        self._connected = True
        return self._acct

    @property
    def is_paper(self) -> bool:
        return self._acct.is_paper_number

    def get_account(self) -> BrokerAccount:
        return self._acct

    def set_safe_account_config(self) -> BrokerAccount:
        # Only no-short / no-margin are set; options level is left as-is
        # (Alpaca won't lower it; options orders are hard-blocked instead).
        self._acct.shorting_enabled = False
        self._acct.multiplier = "1"
        return self._acct

    def set_trade_suspended(self, suspended: bool) -> None:
        self._acct.trade_suspended_by_user = suspended

    def get_positions(self) -> list[BrokerPosition]:
        return list(self._positions)

    def get_orders(self, open_only: bool = True) -> list[BrokerOrder]:
        if open_only:
            return [o for o in self._orders if o.status in ("new", "accepted", "held")]
        return list(self._orders)

    def cancel_all_orders(self) -> int:
        n = 0
        for o in self._orders:
            if o.status in ("new", "accepted", "held"):
                o.status = "canceled"
                n += 1
        return n

    def cancel_order(self, order_id: str) -> None:
        for o in self._orders:
            if o.id == order_id and o.status in ("new", "accepted", "held"):
                o.status = "canceled"

    def cancel_entry_orders(self) -> int:
        """Cancel open entries (buys) only; leave protective sell-stops."""
        n = 0
        for o in self._orders:
            if o.status in ("new", "accepted", "held") and not o.is_protective_stop:
                if o.side == "buy":
                    o.status = "canceled"
                    n += 1
        return n

    def close_all_positions(self, cancel_orders: bool = True) -> int:
        if cancel_orders:
            self.cancel_all_orders()
        n = len(self._positions)
        self._positions.clear()
        return n

    def _next_id(self) -> str:
        self._oid = getattr(self, "_oid", 0) + 1
        return f"o{self._oid}"

    def submit_order(self, *, symbol, qty, side, asset_class="us_equity",
                     order_type="market", limit_price=None, stop_price=None,
                     order_class=None, tif="gtc", client_order_id=None, **kwargs):
        from broker.base import OptionsNotAllowedError, is_option_symbol
        if asset_class in ("option", "us_option") or is_option_symbol(symbol):
            raise OptionsNotAllowedError(f"Options order blocked: {symbol}")

        entry = BrokerOrder(
            id=self._next_id(), client_order_id=client_order_id or "c",
            symbol=symbol, qty=qty, side=side, order_type=order_type,
            status="new", limit_price=limit_price, stop_price=stop_price, tif=tif,
        )
        self._orders.append(entry)

        if side == "buy" and order_type == "market":
            # simulate an immediate market fill -> open the position
            entry.status = "filled"
            self._add_position(symbol, qty, limit_price or (stop_price or 0) or 100.0, "long")
            if order_class == "oto" and stop_price:
                # OTO: a protective sell-stop becomes active, inheriting the TIF
                self._orders.append(BrokerOrder(
                    id=self._next_id(), client_order_id=(client_order_id or "c") + "-stop",
                    symbol=symbol, qty=qty, side="sell", order_type="stop",
                    status="new", stop_price=stop_price, tif=tif,
                ))
        elif side == "sell" and order_type == "market":
            # closing sell: fill and remove the position
            entry.status = "filled"
            self._positions = [p for p in self._positions if p.symbol != symbol]
        return entry

    def stream_trade_updates(self, on_update: Callable[[dict], None]) -> None:
        self.updates_cb = on_update

    # --- test helpers (not part of the Broker interface) ---
    def _add_position(self, symbol, qty, price, side="long"):
        self._positions.append(BrokerPosition(symbol, qty, price, qty * price, side))

    def _add_order(self, symbol, qty, side="buy", status="new", oid="o1", coid="c1"):
        self._orders.append(BrokerOrder(oid, coid, symbol, qty, side, "limit", status))

    def _emit(self, event: dict):
        if self.updates_cb:
            self.updates_cb(event)
