"""Intraday detectors: functions that scan price data and return Alerts.

Each detector is a pure function that takes current market state and returns
an Alert (or None if the condition is not met).

Five alert types:
  1. planned_entry  — price entering a pre-planned entry zone
  2. breakout       — price closing above resistance
  3. dip_to_support — price touching support after an up-move
  4. breaking_news  — a material news item has arrived for this symbol
  5. position_pressure — open position within N% of stop or target
"""

from __future__ import annotations

from typing import Optional, Sequence

from data.models import Bar, NewsItem
from monitor.alerts import Alert, AlertType


# ---------------------------------------------------------------------------
# 1. Planned entry
# ---------------------------------------------------------------------------

def detect_planned_entry(
    symbol: str,
    exchange: str,
    current_price: float,
    entry_zone_low: float,
    entry_zone_high: float,
) -> Optional[Alert]:
    """Fires when price enters [entry_zone_low, entry_zone_high]."""
    if entry_zone_low <= current_price <= entry_zone_high:
        return Alert(
            symbol=symbol,
            exchange=exchange,
            alert_type=AlertType.PLANNED_ENTRY,
            trigger_price=current_price,
            note=(
                f"Price {current_price:.4g} entered planned entry zone "
                f"[{entry_zone_low:.4g}, {entry_zone_high:.4g}]"
            ),
        )
    return None


# ---------------------------------------------------------------------------
# 2. Breakout
# ---------------------------------------------------------------------------

def detect_breakout(
    symbol: str,
    exchange: str,
    bars: Sequence[Bar],
    lookback: int = 20,
    min_breakout_pct: float = 0.005,
) -> Optional[Alert]:
    """Fires when the latest close is above the highest high of the last
    `lookback` bars (excluding current bar) by at least min_breakout_pct."""
    if len(bars) < lookback + 1:
        return None
    recent = bars[-(lookback + 1):-1]
    resistance = max(b.high for b in recent)
    current_close = bars[-1].close
    if current_close > resistance * (1 + min_breakout_pct):
        return Alert(
            symbol=symbol,
            exchange=exchange,
            alert_type=AlertType.BREAKOUT,
            trigger_price=current_close,
            note=(
                f"Close {current_close:.4g} broke above {lookback}-bar resistance "
                f"{resistance:.4g} (+{(current_close / resistance - 1):.1%})"
            ),
        )
    return None


# ---------------------------------------------------------------------------
# 3. Dip to support
# ---------------------------------------------------------------------------

def detect_dip_to_support(
    symbol: str,
    exchange: str,
    bars: Sequence[Bar],
    lookback: int = 20,
    tolerance_pct: float = 0.01,
) -> Optional[Alert]:
    """Fires when the latest low touches the lowest low of the last
    `lookback` bars (excluding current) within tolerance_pct."""
    if len(bars) < lookback + 1:
        return None
    recent = bars[-(lookback + 1):-1]
    support = min(b.low for b in recent)
    current_low = bars[-1].low
    current_close = bars[-1].close
    # Price dipped to support but closed above it (potential bounce)
    touched = current_low <= support * (1 + tolerance_pct)
    bounced = current_close > support * (1 + tolerance_pct)
    if touched and bounced:
        return Alert(
            symbol=symbol,
            exchange=exchange,
            alert_type=AlertType.DIP_TO_SUPPORT,
            trigger_price=current_low,
            note=(
                f"Low {current_low:.4g} touched {lookback}-bar support "
                f"{support:.4g} and closed above ({current_close:.4g})"
            ),
        )
    return None


# ---------------------------------------------------------------------------
# 4. Breaking news
# ---------------------------------------------------------------------------

def detect_breaking_news(
    symbol: str,
    exchange: str,
    news_items: Sequence[NewsItem],
    max_age_minutes: int = 15,
) -> Optional[Alert]:
    """Fires on a fresh, material news item for this symbol."""
    from datetime import datetime, timezone, timedelta
    UTC = timezone.utc
    cutoff = datetime.now(UTC) - timedelta(minutes=max_age_minutes)

    for item in news_items:
        if symbol.upper() not in [s.upper() for s in item.symbols]:
            continue
        if item.published_at < cutoff:
            continue
        if item.materiality not in ("medium", "high"):
            continue
        return Alert(
            symbol=symbol,
            exchange=exchange,
            alert_type=AlertType.BREAKING_NEWS,
            trigger_price=0.0,
            note=f"Material news: {item.headline[:120]}",
        )
    return None


# ---------------------------------------------------------------------------
# 5. Position pressure
# ---------------------------------------------------------------------------

def detect_position_pressure(
    symbol: str,
    exchange: str,
    current_price: float,
    stop_price: float,
    target_price: float,
    proximity_pct: float = 0.02,
) -> Optional[Alert]:
    """Fires when an open position is within proximity_pct of its stop or target."""
    stop_dist = abs(current_price - stop_price) / current_price
    target_dist = abs(current_price - target_price) / current_price

    if stop_dist <= proximity_pct:
        return Alert(
            symbol=symbol,
            exchange=exchange,
            alert_type=AlertType.POSITION_PRESSURE,
            trigger_price=current_price,
            note=(
                f"Price {current_price:.4g} is within {stop_dist:.1%} of stop "
                f"{stop_price:.4g}"
            ),
        )
    if target_dist <= proximity_pct:
        return Alert(
            symbol=symbol,
            exchange=exchange,
            alert_type=AlertType.POSITION_PRESSURE,
            trigger_price=current_price,
            note=(
                f"Price {current_price:.4g} is within {target_dist:.1%} of target "
                f"{target_price:.4g}"
            ),
        )
    return None
