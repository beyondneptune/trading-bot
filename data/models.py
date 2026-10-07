"""Core data models used across the pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

from instruments.units import PriceUnit


class DataQuality(str, Enum):
    LIVE = "live"
    DELAYED = "delayed"
    CACHED = "cached"
    MOCK = "mock"


class Bar(BaseModel):
    symbol: str
    exchange: str
    bar_type: str           # "1d", "5m", "1m", etc.
    ts: datetime            # timezone-aware
    open: float
    high: float
    low: float
    close: float
    volume: float
    price_unit: PriceUnit
    quality: DataQuality = DataQuality.CACHED

    @field_validator("ts")
    @classmethod
    def ts_must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Bar.ts must be timezone-aware")
        return v

    @model_validator(mode="after")
    def high_gte_low(self) -> "Bar":
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) < low ({self.low})")
        return self

    @field_validator("volume")
    @classmethod
    def volume_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("volume must be >= 0")
        return v


class Quote(BaseModel):
    symbol: str
    exchange: str
    bid: float
    ask: float
    last: float
    volume: float
    price_unit: PriceUnit
    quality: DataQuality
    ts: datetime

    @property
    def spread_pct(self) -> float:
        mid = (self.bid + self.ask) / 2
        if mid == 0:
            return 0.0
        return (self.ask - self.bid) / mid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class NewsItem(BaseModel):
    headline: str
    body: Optional[str] = None
    source: Optional[str] = None
    symbols: list[str] = []
    published_at: datetime
    retrieved_at: datetime
    materiality: Optional[str] = None  # "low" | "medium" | "high"


class EarningsEvent(BaseModel):
    symbol: str
    exchange: str
    report_date: datetime
    estimate_eps: Optional[float] = None
    actual_eps: Optional[float] = None
    confirmed: bool = False


class EconomicEvent(BaseModel):
    name: str
    scheduled_at: datetime
    importance: str     # "low" | "medium" | "high"
    currency: str       # affected currency, e.g. "GBP", "USD"
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None
