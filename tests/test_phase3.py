"""Phase 3 tests: AI tools, grounding, schema validation, cost tracking."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

UTC = timezone.utc


# ---------------------------------------------------------------------------
# analyst/tools.py
# ---------------------------------------------------------------------------

class TestIndicators:
    def _make_bars(self, n=260, seed=42):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        return generate_daily_bars(spec, n_days=n, seed=seed)

    def test_get_bars_returns_dict(self):
        from analyst.tools import get_bars
        bars = self._make_bars()
        result = get_bars(bars, bar_type="1d", limit=20)
        assert result["count"] == 20
        assert len(result["bars"]) == 20
        for b in result["bars"]:
            assert "date" in b and "close" in b

    def test_get_indicators_has_sma200(self):
        from analyst.tools import get_indicators
        bars = self._make_bars(260)
        result = get_indicators(bars)
        assert result["sma_200"] is not None

    def test_get_indicators_insufficient_data(self):
        from analyst.tools import get_indicators
        bars = self._make_bars(10)
        result = get_indicators(bars)
        assert result["sma_200"] is None

    def test_support_resistance_returns_levels(self):
        from analyst.tools import get_support_resistance
        bars = self._make_bars(60)
        result = get_support_resistance(bars)
        assert "support_levels" in result
        assert "resistance_levels" in result

    def test_get_news_summary(self):
        from analyst.tools import get_news_summary
        from data.mock_data import generate_news
        news = generate_news("AZN", n=5)
        result = get_news_summary(news, "AZN")
        assert result["total_items"] == 5
        assert result["symbol"] == "AZN"

    def test_get_earnings_info_blackout(self):
        from analyst.tools import get_earnings_info
        from data.models import EarningsEvent
        now = datetime.now(UTC)
        earnings = EarningsEvent(
            symbol="AZN", exchange="LSE",
            report_date=now.replace(tzinfo=None) if now.tzinfo else now,
        )
        # Replace with aware datetime
        from datetime import timedelta
        earnings = EarningsEvent(
            symbol="AZN", exchange="LSE",
            report_date=datetime.now(UTC) + timedelta(days=2),
        )
        result = get_earnings_info("AZN", "LSE", earnings, is_etf=False)
        assert result["blackout"] is True

    def test_get_regime_info(self):
        from analyst.tools import get_regime_info
        from data.mock_data import generate_daily_bars
        from data.market_context import compute_regime
        from instruments.units import InstrumentSpec, PriceUnit
        spec = InstrumentSpec(symbol="VWRL", exchange="LSE", currency="USD",
                              price_unit=PriceUnit.USD)
        bars = generate_daily_bars(spec, n_days=300)
        regime = compute_regime(bars)
        result = get_regime_info(regime)
        assert result["label"] in ("normal", "elevated", "stressed")
        assert "multiplier" in result


# ---------------------------------------------------------------------------
# analyst/grounding.py
# ---------------------------------------------------------------------------

class TestGrounding:
    def _make_tool_data(self, current=100.0, support=95.0, resistance=105.0):
        return (
            {"support_levels": [support], "resistance_levels": [resistance], "current_price": current},
            {"sma_20": current, "sma_50": current, "sma_200": current},
            {"bars": [{"high": resistance, "low": support, "close": current}]},
        )

    def test_grounding_passes_on_tool_levels(self):
        from analyst.grounding import verify_price_levels
        sr, ind, bars = self._make_tool_data(100.0, 95.0, 110.0)
        # Entry at current price, stop at support, target at resistance
        result = verify_price_levels(
            entry_price=100.0, stop_price=95.0, target_price=110.0,
            support_resistance=sr, indicators=ind, bars_data=bars,
        )
        assert result.passed is True

    def test_grounding_fails_on_invented_level(self):
        from analyst.grounding import verify_price_levels
        sr, ind, bars = self._make_tool_data(100.0, 95.0, 105.0)
        # Entry 150 — far from any reference level
        result = verify_price_levels(
            entry_price=150.0, stop_price=95.0, target_price=105.0,
            support_resistance=sr, indicators=ind, bars_data=bars,
        )
        assert result.passed is False

    def test_grounding_identifies_failed_levels(self):
        from analyst.grounding import verify_price_levels
        sr, ind, bars = self._make_tool_data(100.0, 95.0, 105.0)
        result = verify_price_levels(
            entry_price=150.0, stop_price=95.0, target_price=105.0,
            support_resistance=sr, indicators=ind, bars_data=bars,
        )
        failed = [c["level_name"] for c in result.checked_levels if not c["passed"]]
        assert "entry" in failed

    def test_grounding_within_tolerance(self):
        from analyst.grounding import TOLERANCE_PCT, verify_price_levels
        sr, ind, bars = self._make_tool_data(100.0, 95.0, 105.0)
        # 1% off — within 3% tolerance
        result = verify_price_levels(
            entry_price=101.0, stop_price=95.0, target_price=105.0,
            support_resistance=sr, indicators=ind, bars_data=bars,
        )
        assert result.passed is True


# ---------------------------------------------------------------------------
# analyst/schemas.py
# ---------------------------------------------------------------------------

class TestSchemas:
    def _valid_idea(self, **overrides):
        data = {
            "action": "BUY",
            "instrument": "AZN.LSE",
            "setup_type": "dip_to_support",
            "expected_holding_days": 5,
            "thesis": "AZN has pulled back to its 50-day SMA on light volume. "
                      "Momentum remains positive on the 12-month timeframe. "
                      "Risks include broad market weakness and upcoming earnings.",
            "technical_picture": "Price at 50-day SMA, RSI 45.",
            "catalysts": "Sector rotation, FDA pipeline positive.",
            "key_risks": "Earnings miss risk, macro headwinds.",
            "stop_level": 6800.0,
            "target_level": 7500.0,
            "stop_reasoning": "Below recent swing low.",
            "target_reasoning": "Prior resistance level.",
            "confidence": "medium",
            "sources": [],
            "strongest_dissent": "Bear case: market leadership rotating away from pharma.",
        }
        data.update(overrides)
        return data

    def test_valid_idea_parses(self):
        from analyst.schemas import TradeIdea
        idea = TradeIdea(**self._valid_idea())
        assert idea.action.value == "BUY"

    def test_no_trade_valid(self):
        from analyst.schemas import TradeIdea
        idea = TradeIdea(**self._valid_idea(action="NO_TRADE"))
        assert idea.action.value == "NO_TRADE"

    def test_target_below_stop_rejected(self):
        from analyst.schemas import TradeIdea
        with pytest.raises(Exception):
            TradeIdea(**self._valid_idea(stop_level=7500.0, target_level=6800.0))

    def test_thesis_too_short_rejected(self):
        from analyst.schemas import TradeIdea
        with pytest.raises(Exception):
            TradeIdea(**self._valid_idea(thesis="Too short."))

    def test_invalid_action_rejected(self):
        from analyst.schemas import TradeIdea
        with pytest.raises(Exception):
            TradeIdea(**self._valid_idea(action="MAYBE"))

    def test_invalid_confidence_rejected(self):
        from analyst.schemas import TradeIdea
        with pytest.raises(Exception):
            TradeIdea(**self._valid_idea(confidence="extreme"))


# ---------------------------------------------------------------------------
# analyst/spend.py
# ---------------------------------------------------------------------------

class TestSpend:
    def _setup_db(self, tmp_path: Path):
        from data.database import get_connection, init_db
        db_path = tmp_path / "test.db"
        init_db(str(db_path))
        return get_connection(str(db_path))

    def test_tokens_to_usd(self):
        from analyst.spend import tokens_to_usd
        cost = tokens_to_usd("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.8 + 4.0)  # $4.80 per million input+output

    def test_record_spend(self, tmp_path: Path):
        from analyst.spend import record_spend
        conn = self._setup_db(tmp_path)
        cost = record_spend(conn, "claude-haiku-4-5-20251001", 10_000, 5_000)
        assert cost > 0
        row = conn.execute("SELECT COUNT(*) FROM ai_spend").fetchone()
        assert row[0] == 1

    def test_monthly_spend_accumulates(self, tmp_path: Path):
        from analyst.spend import monthly_spend_usd, record_spend
        conn = self._setup_db(tmp_path)
        record_spend(conn, "claude-haiku-4-5-20251001", 10_000, 5_000)
        record_spend(conn, "claude-haiku-4-5-20251001", 10_000, 5_000)
        spend = monthly_spend_usd(conn)
        assert spend > 0

    def test_budget_ok_when_within_cap(self, tmp_path: Path):
        from analyst.spend import check_budget
        conn = self._setup_db(tmp_path)
        ok, spend = check_budget(conn)
        assert ok is True
        assert spend == 0.0

    def test_budget_exceeded(self, tmp_path: Path):
        from analyst.spend import check_budget, record_spend
        conn = self._setup_db(tmp_path)
        # Record enough spend to exceed the default £50 cap
        # £50 / 0.79 ≈ $63.3 — record a large spend
        conn.execute(
            """INSERT INTO ai_spend (model, input_tokens, output_tokens, cost_usd, recorded_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("claude-sonnet-4-6", 0, 0, 70.0,
             datetime.now(UTC).isoformat()),
        )
        conn.commit()
        ok, spend = check_budget(conn)
        assert ok is False


# ---------------------------------------------------------------------------
# analyst/pipeline.py — mock mode
# ---------------------------------------------------------------------------

class TestPipeline:
    def test_run_debate_mock_mode(self, tmp_path: Path):
        import asyncio
        from data.database import get_connection, init_db
        from data.mock_data import generate_daily_bars, generate_news
        from data.market_context import compute_regime
        from instruments.units import InstrumentSpec, PriceUnit
        from analyst.pipeline import run_debate

        db_path = tmp_path / "test.db"
        init_db(str(db_path))
        conn = get_connection(str(db_path))

        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        bars = generate_daily_bars(spec, n_days=300)
        news = generate_news("AZN", n=3)
        regime = compute_regime(bars)

        result = asyncio.get_event_loop().run_until_complete(
            run_debate("AZN", "LSE", bars, news, None, regime, False, conn)
        )
        assert result.symbol == "AZN"
        # In mock mode, always NO_TRADE
        if result.idea is not None:
            assert result.idea.action.value == "NO_TRADE"
        conn.close()

    def test_debate_records_cost(self, tmp_path: Path):
        import asyncio
        from data.database import get_connection, init_db
        from data.mock_data import generate_daily_bars, generate_news
        from data.market_context import compute_regime
        from instruments.units import InstrumentSpec, PriceUnit
        from analyst.pipeline import run_debate
        from analyst.spend import monthly_spend_usd

        db_path = tmp_path / "test.db"
        init_db(str(db_path))
        conn = get_connection(str(db_path))

        spec = InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        bars = generate_daily_bars(spec, n_days=100)
        news = generate_news("AZN")
        regime = compute_regime(bars)

        asyncio.get_event_loop().run_until_complete(
            run_debate("AZN", "LSE", bars, news, None, regime, False, conn)
        )
        spend = monthly_spend_usd(conn)
        assert spend > 0  # mock mode still records stub token counts
        conn.close()
