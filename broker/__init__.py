"""Broker abstraction for the strategy lab.

The lab talks to a broker only through the `Broker` interface (base.py), so it
never depends on Alpaca directly. `make_broker(settings)` returns the right
implementation; tests use `FakeBroker`.
"""

from broker.base import (
    Broker,
    BrokerAccount,
    BrokerOrder,
    BrokerPosition,
    PaperAccountError,
    startup_banner,
)


def make_broker(settings=None):
    """Return the broker implementation selected by settings.trading_backend.

    'alpaca' -> AlpacaBroker (paper). Anything else raises (no live path).
    """
    from config.settings import load_settings
    settings = settings or load_settings()
    backend = settings.trading_backend
    if backend == "alpaca":
        from broker.alpaca import AlpacaBroker
        return AlpacaBroker()
    raise ValueError(
        f"No broker for trading_backend={backend!r}. The lab trades via "
        "'alpaca' (paper). IB is switched off; set trading_backend: alpaca."
    )


__all__ = [
    "Broker", "BrokerAccount", "BrokerOrder", "BrokerPosition",
    "PaperAccountError", "startup_banner", "make_broker",
]
