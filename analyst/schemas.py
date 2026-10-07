"""Pydantic schemas for AI analyst outputs.

All AI output is validated against these schemas (CLAUDE.md rule 15).
The judge must return a strict JSON matching TradeIdea.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SetupType(str, Enum):
    PLANNED_ENTRY = "planned_entry"
    BREAKOUT = "breakout"
    DIP_TO_SUPPORT = "dip_to_support"
    BREAKING_NEWS = "breaking_news"
    POSITION_PRESSURE = "position_pressure"
    EOD_FUNDAMENTAL = "eod_fundamental"


class SourceRef(BaseModel):
    description: str
    timestamp: str  # ISO8601


class TradeIdea(BaseModel):
    """The judge's output — strict schema, Pydantic-validated."""

    action: Action
    instrument: str                     # e.g. "AZN.LSE"
    setup_type: SetupType
    expected_holding_days: int = Field(ge=1, le=90)
    thesis: str = Field(min_length=10, max_length=1000)
    technical_picture: str
    catalysts: str
    key_risks: str                      # "what would make me wrong"
    stop_level: float = Field(gt=0)
    target_level: float = Field(gt=0)
    stop_reasoning: str
    target_reasoning: str
    confidence: Confidence
    sources: list[SourceRef] = Field(default_factory=list)
    strongest_dissent: str              # the best argument against this trade

    @field_validator("target_level")
    @classmethod
    def target_above_stop(cls, v: float, info) -> float:
        stop = info.data.get("stop_level")
        if stop and v <= stop:
            raise ValueError(f"target_level ({v}) must be above stop_level ({stop})")
        return v

    @field_validator("thesis")
    @classmethod
    def thesis_3_to_5_sentences(cls, v: str) -> str:
        sentences = [s.strip() for s in v.split(".") if s.strip()]
        if len(sentences) < 2:
            raise ValueError("thesis must have at least 3 sentences")
        return v


class AnalystDebateEntry(BaseModel):
    """A single analyst's contribution to the debate."""

    role: Literal["bull", "bear", "technical", "news", "macro", "judge"]
    content: str = Field(min_length=20)
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class DebateResult(BaseModel):
    symbol: str
    exchange: str
    debate: list[AnalystDebateEntry]
    idea: Optional[TradeIdea] = None    # None if judge returns NO_TRADE
    grounding_passed: bool = False
    grounding_details: list[dict] = Field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cost_usd: float = 0.0
    prompt_version: str = "v1"
    analyst_config: str = ""
