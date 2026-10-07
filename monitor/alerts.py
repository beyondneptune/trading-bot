"""Alert type definitions and lifecycle management for the intraday monitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

UTC = timezone.utc


class AlertType(str, Enum):
    PLANNED_ENTRY = "planned_entry"
    BREAKOUT = "breakout"
    DIP_TO_SUPPORT = "dip_to_support"
    BREAKING_NEWS = "breaking_news"
    POSITION_PRESSURE = "position_pressure"


class AlertState(str, Enum):
    PENDING = "pending"       # created, waiting for fast AI check
    LIVE = "live"             # AI approved, surfaced to user
    EXPIRED = "expired"       # timed out before decision
    SENT = "sent"             # signal written to DB; user has it in approval queue
    SUPPRESSED = "suppressed" # duplicate or filter blocked


_EXPIRY_SECONDS: dict[AlertType, int] = {
    AlertType.PLANNED_ENTRY: 900,      # 15 min
    AlertType.BREAKOUT: 300,           # 5 min — breakouts are time-sensitive
    AlertType.DIP_TO_SUPPORT: 900,     # 15 min
    AlertType.BREAKING_NEWS: 1800,     # 30 min
    AlertType.POSITION_PRESSURE: 1800, # 30 min
}


@dataclass
class Alert:
    symbol: str
    exchange: str
    alert_type: AlertType
    trigger_price: float
    note: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    state: AlertState = AlertState.PENDING
    signal_id: Optional[int] = None
    suppressed_reason: Optional[str] = None

    @property
    def expiry_seconds(self) -> int:
        return _EXPIRY_SECONDS[self.alert_type]

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(UTC)
        age = (now - self.created_at).total_seconds()
        return age > self.expiry_seconds

    def expire(self) -> None:
        self.state = AlertState.EXPIRED

    def suppress(self, reason: str) -> None:
        self.state = AlertState.SUPPRESSED
        self.suppressed_reason = reason

    def mark_sent(self, signal_id: int) -> None:
        self.state = AlertState.SENT
        self.signal_id = signal_id

    def mark_live(self) -> None:
        self.state = AlertState.LIVE
