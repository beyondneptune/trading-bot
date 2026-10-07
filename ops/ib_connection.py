"""IB Gateway / TWS connection wrapper.

LiveIBConnection  — connects to a real IB Gateway using ib_async.
MockIBConnection  — stands in when mock_mode=true; no network I/O.

Both implement BaseIBConnection.  Use make_connection() to get the right
one based on settings.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from config.settings import load_settings
from risk.guards import assert_paper_account, assert_readonly_phase

logger = logging.getLogger(__name__)

_RECONNECT_DELAYS = (5, 10, 30, 60)  # seconds, then repeats last


class BaseIBConnection(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @property
    @abstractmethod
    def account_id(self) -> str: ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    def place_order(self, *args: Any, **kwargs: Any) -> None:
        """Placement proxy — always checks the read-only guard first."""
        settings = load_settings()
        assert_readonly_phase(settings.phase)
        return self._place_order(*args, **kwargs)

    def _place_order(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError


class MockIBConnection(BaseIBConnection):
    """Simulated connection for testing and demo runs (no IB needed)."""

    _FAKE_ACCOUNT = "DU999999"

    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> None:
        logger.info("Mock mode — skipping IB connection (account %s)", self._FAKE_ACCOUNT)
        assert_paper_account(self._FAKE_ACCOUNT)
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("Mock mode — disconnected")

    @property
    def account_id(self) -> str:
        return self._FAKE_ACCOUNT

    @property
    def is_connected(self) -> bool:
        return self._connected


class LiveIBConnection(BaseIBConnection):
    """Real connection to IB Gateway or TWS via ib_async."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        readonly: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._readonly = readonly
        self._ib: Any = None  # ib_async.IB, imported lazily
        self._account_id: str = ""

    async def connect(self) -> None:
        from ib_async import IB  # type: ignore[import]

        self._ib = IB()
        for attempt, delay in enumerate(_RECONNECT_DELAYS, 1):
            try:
                await self._ib.connectAsync(
                    self._host,
                    self._port,
                    clientId=self._client_id,
                    readonly=self._readonly,
                )
                accounts = self._ib.managedAccounts()
                if not accounts:
                    raise RuntimeError("IB returned no managed accounts")
                self._account_id = accounts[0]
                assert_paper_account(self._account_id)
                logger.info(
                    "Connected to IB Gateway %s:%s — account %s (readonly=%s)",
                    self._host,
                    self._port,
                    self._account_id,
                    self._readonly,
                )
                return
            except Exception as exc:
                logger.warning(
                    "IB connect attempt %d failed: %s — retrying in %ds",
                    attempt,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"Could not connect to IB Gateway at {self._host}:{self._port} "
            "after multiple attempts."
        )

    async def disconnect(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            logger.info("Disconnected from IB")

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def is_connected(self) -> bool:
        return bool(self._ib and self._ib.isConnected())


def make_connection() -> BaseIBConnection:
    """Return the appropriate connection based on current settings."""
    settings = load_settings()
    if settings.mock_mode:
        return MockIBConnection()
    return LiveIBConnection(
        host=settings.ib.host,
        port=settings.ib.port,
        client_id=settings.ib.client_id,
        readonly=settings.ib.readonly,
    )
