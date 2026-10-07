"""AI analyst tool implementations.

The AI never does arithmetic on prices — it calls these functions and
receives structured data.  After the AI responds, `grounding.py` verifies
every price level the AI cited against the tool outputs.

Tools are also exported as Anthropic tool definitions for the API call.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from data.models import Bar, NewsItem
from instruments.units import InstrumentSpec, PriceUnit, to_gbp

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Indicator computations (no IB needed; use cached bars)
# ---------------------------------------------------------------------------

def _closes(bars: list[Bar]) -> list[float]:
    return [b.close for b in sorted(bars, key=lambda b: b.ts)]


def _sma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _atr(bars: list[Bar], period: int = 14) -> Optional[float]:
    sorted_bars = sorted(bars, key=lambda b: b.ts)
    if len(sorted_bars) < period + 1:
        return None
    trs = []
    for i in range(-period, 0):
        b = sorted_bars[i]
        prev_close = sorted_bars[i - 1].close
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs)


def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(len(closes) - period, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _swing_levels(bars: list[Bar], lookback: int = 20) -> dict[str, list[float]]:
    """Find swing highs (resistance) and swing lows (support)."""
    sorted_bars = sorted(bars, key=lambda b: b.ts)[-lookback - 4 :]
    highs, lows = [], []
    for i in range(2, len(sorted_bars) - 2):
        if (sorted_bars[i].high > sorted_bars[i-1].high and
                sorted_bars[i].high > sorted_bars[i-2].high and
                sorted_bars[i].high > sorted_bars[i+1].high and
                sorted_bars[i].high > sorted_bars[i+2].high):
            highs.append(sorted_bars[i].high)
        if (sorted_bars[i].low < sorted_bars[i-1].low and
                sorted_bars[i].low < sorted_bars[i-2].low and
                sorted_bars[i].low < sorted_bars[i+1].low and
                sorted_bars[i].low < sorted_bars[i+2].low):
            lows.append(sorted_bars[i].low)
    return {"resistance": highs[-3:], "support": lows[-3:]}


# ---------------------------------------------------------------------------
# Tool functions — called by the analyst pipeline
# ---------------------------------------------------------------------------

def get_bars(
    bars: list[Bar],
    bar_type: str = "1d",
    limit: int = 60,
) -> dict[str, Any]:
    """Return recent OHLCV bars as a plain dict for the AI."""
    relevant = [b for b in bars if b.bar_type == bar_type]
    relevant = sorted(relevant, key=lambda b: b.ts)[-limit:]
    return {
        "bar_type": bar_type,
        "count": len(relevant),
        "price_unit": relevant[0].price_unit.value if relevant else "unknown",
        "bars": [
            {
                "date": b.ts.date().isoformat(),
                "open": round(b.open, 2),
                "high": round(b.high, 2),
                "low": round(b.low, 2),
                "close": round(b.close, 2),
                "volume": int(b.volume),
            }
            for b in relevant
        ],
    }


def get_indicators(bars: list[Bar]) -> dict[str, Any]:
    """Compute technical indicators from bars."""
    closes = _closes(bars)
    atr = _atr(bars)
    return {
        "current_price": round(closes[-1], 2) if closes else None,
        "sma_20": round(_sma(closes, 20), 2) if _sma(closes, 20) else None,
        "sma_50": round(_sma(closes, 50), 2) if _sma(closes, 50) else None,
        "sma_200": round(_sma(closes, 200), 2) if _sma(closes, 200) else None,
        "rsi_14": round(_rsi(closes), 1) if _rsi(closes) else None,
        "atr_14": round(atr, 2) if atr else None,
        "atr_pct": round(atr / closes[-1] * 100, 2) if (atr and closes) else None,
        "price_vs_52w_high_pct": (
            round((closes[-1] / max(closes[-252:]) - 1) * 100, 1)
            if len(closes) >= 252 else None
        ),
        "momentum_6m_pct": (
            round((closes[-1] / closes[-126] - 1) * 100, 1)
            if len(closes) >= 126 else None
        ),
        "momentum_12m_pct": (
            round((closes[-1] / closes[-252] - 1) * 100, 1)
            if len(closes) >= 252 else None
        ),
    }


def get_support_resistance(bars: list[Bar]) -> dict[str, Any]:
    """Return computed support and resistance levels."""
    levels = _swing_levels(bars)
    closes = _closes(bars)
    current = closes[-1] if closes else 0.0
    return {
        "current_price": round(current, 2),
        "resistance_levels": [round(h, 2) for h in levels["resistance"]],
        "support_levels": [round(l, 2) for l in levels["support"]],
    }


def get_news_summary(news_items: list[NewsItem], symbol: str) -> dict[str, Any]:
    """Return a structured news summary for the AI."""
    now = datetime.now(UTC)
    fresh = [n for n in news_items if (now - n.published_at.replace(tzinfo=UTC) if n.published_at.tzinfo is None else now - n.published_at).total_seconds() < 86400]
    return {
        "symbol": symbol,
        "total_items": len(news_items),
        "fresh_count": len(fresh),
        "items": [
            {
                "headline": n.headline,
                "source": n.source,
                "published_at": n.published_at.isoformat(),
                "materiality": n.materiality,
                "age": "fresh" if n in fresh else "background",
            }
            for n in news_items[:10]
        ],
    }


def get_earnings_info(
    symbol: str,
    exchange: str,
    earnings=None,
    is_etf: bool = False,
) -> dict[str, Any]:
    """Return earnings proximity info for the AI."""
    from data.fundamentals import is_earnings_blackout
    if earnings is None:
        return {"symbol": symbol, "blackout": False, "days_until": None, "etf": is_etf}
    now = datetime.now(UTC)
    report = earnings.report_date
    if report.tzinfo is None:
        report = report.replace(tzinfo=UTC)
    days_until = (report - now).days
    blackout = is_earnings_blackout(symbol, exchange, is_etf, earnings, now)
    return {
        "symbol": symbol,
        "is_etf": is_etf,
        "next_report_date": report.date().isoformat(),
        "days_until_report": max(0, days_until),
        "blackout": blackout,
        "note": (
            "EARNINGS BLACKOUT: no new positions within 3 days of report."
            if blackout else
            f"{days_until} days until next report."
        ),
    }


def get_regime_info(regime) -> dict[str, Any]:
    """Return regime as a structured dict for the AI."""
    return {
        "label": regime.label,
        "multiplier": regime.multiplier,
        "vol_percentile": round(regime.vol_percentile, 1),
        "index_above_200d_sma": regime.index_above_200d_sma,
        "note": regime.note,
    }


# ---------------------------------------------------------------------------
# Anthropic tool definitions
# ---------------------------------------------------------------------------

ANTHROPIC_TOOLS = [
    {
        "name": "get_bars",
        "description": (
            "Get recent OHLCV price bars for an instrument. "
            "Returns dates, open, high, low, close, volume. "
            "bar_type: '1d' for daily, '5m' for 5-minute intraday."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string"},
                "bar_type": {"type": "string", "enum": ["1d", "5m", "1m"]},
                "limit": {"type": "integer", "default": 60},
            },
            "required": ["symbol", "exchange"],
        },
    },
    {
        "name": "get_indicators",
        "description": (
            "Get technical indicators: SMA(20/50/200), RSI(14), ATR(14), "
            "momentum (6m, 12m), price vs 52-week high."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string"},
            },
            "required": ["symbol", "exchange"],
        },
    },
    {
        "name": "get_support_resistance",
        "description": "Get computed support and resistance price levels from swing highs/lows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string"},
            },
            "required": ["symbol", "exchange"],
        },
    },
    {
        "name": "get_news_summary",
        "description": "Get recent news headlines and materiality ratings for a symbol.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string"},
            },
            "required": ["symbol", "exchange"],
        },
    },
    {
        "name": "get_earnings_info",
        "description": (
            "Get upcoming earnings date and whether the symbol is in an "
            "earnings blackout window (3 days before report)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "exchange": {"type": "string"},
            },
            "required": ["symbol", "exchange"],
        },
    },
    {
        "name": "get_regime_info",
        "description": (
            "Get the current market regime: normal/elevated/stressed, "
            "volatility percentile, index trend, and the regime multiplier."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
