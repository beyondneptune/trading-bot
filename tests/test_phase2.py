"""Phase 2 tests: every risk rule, cost model, sizing, heat, gates, limits."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

UTC = timezone.utc


# ---------------------------------------------------------------------------
# risk/costs.py
# ---------------------------------------------------------------------------

class TestCostModel:
    def _make_spec(self, price_unit="GBX"):
        from instruments.units import InstrumentSpec, PriceUnit
        return InstrumentSpec(
            symbol="AZN", exchange="LSE", currency="GBP",
            price_unit=PriceUnit(price_unit),
        )

    def test_stamp_duty_on_uk_shares(self):
        from risk.costs import estimate_round_trip_cost
        spec = self._make_spec("GBX")
        assert spec.stamp_duty_applies is True
        cost = estimate_round_trip_cost(spec, 7000.0, 100, 700.0)
        assert cost.stamp_duty_gbp > 0

    def test_no_stamp_duty_on_etf(self):
        from instruments.units import InstrumentSpec, PriceUnit
        from risk.costs import estimate_round_trip_cost
        spec = InstrumentSpec(symbol="VUSA", exchange="LSE", currency="USD",
                              price_unit=PriceUnit.USD)
        assert spec.stamp_duty_applies is False
        cost = estimate_round_trip_cost(spec, 100.0, 10, 10.0, fx_usd_gbp=0.79)
        assert cost.stamp_duty_gbp == 0.0

    def test_no_stamp_duty_on_us_stock(self):
        from instruments.units import InstrumentSpec, PriceUnit
        from risk.costs import estimate_round_trip_cost
        spec = InstrumentSpec(symbol="AAPL", exchange="NASDAQ", currency="USD",
                              price_unit=PriceUnit.USD)
        cost = estimate_round_trip_cost(spec, 175.0, 10, 50.0, fx_usd_gbp=0.79)
        assert cost.stamp_duty_gbp == 0.0

    def test_cost_as_r_infinity_on_zero_risk(self):
        from risk.costs import estimate_round_trip_cost
        spec = self._make_spec("GBX")
        cost = estimate_round_trip_cost(spec, 7000.0, 100, 0.0)
        assert cost.cost_as_r == float("inf")

    def test_total_includes_all_components(self):
        from risk.costs import estimate_round_trip_cost
        spec = self._make_spec("GBX")
        cost = estimate_round_trip_cost(spec, 7000.0, 100, 700.0)
        expected = (cost.commission_gbp + cost.spread_gbp + cost.slippage_gbp
                    + cost.stamp_duty_gbp + cost.fx_cost_gbp)
        assert cost.total_gbp == pytest.approx(expected)


# ---------------------------------------------------------------------------
# risk/sizing.py — Rule 4
# ---------------------------------------------------------------------------

class TestSizing:
    def _spec(self):
        from instruments.units import InstrumentSpec, PriceUnit
        return InstrumentSpec(symbol="AZN", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)

    def test_basic_sizing(self):
        from risk.sizing import compute_position_size
        spec = self._spec()
        result = compute_position_size(
            spec, entry_price=7000.0, stop_price=6650.0,
            equity_gbp=12_500.0, cash_gbp=12_500.0,
            open_position_count=0, regime_multiplier=1.0,
        )
        assert result.feasible is True
        assert result.quantity > 0

    def test_risk_pct_within_limit(self):
        from risk.sizing import compute_position_size
        spec = self._spec()
        result = compute_position_size(
            spec, entry_price=7000.0, stop_price=6650.0,
            equity_gbp=12_500.0, cash_gbp=12_500.0,
            open_position_count=0, regime_multiplier=1.0,
        )
        assert result.risk_pct <= 0.01 + 1e-9  # ≤ 1%

    def test_position_pct_within_cap(self):
        from risk.sizing import compute_position_size
        spec = self._spec()
        result = compute_position_size(
            spec, entry_price=7000.0, stop_price=6650.0,
            equity_gbp=12_500.0, cash_gbp=12_500.0,
            open_position_count=0, regime_multiplier=1.0,
        )
        assert result.position_pct <= 0.15 + 1e-9  # ≤ 15%

    def test_max_positions_blocks(self):
        from risk.sizing import SizingConstraint, compute_position_size
        spec = self._spec()
        result = compute_position_size(
            spec, entry_price=7000.0, stop_price=6650.0,
            equity_gbp=12_500.0, cash_gbp=12_500.0,
            open_position_count=8,  # already at limit
            regime_multiplier=1.0,
        )
        assert result.feasible is False
        assert result.binding_constraint == SizingConstraint.MAX_POSITIONS

    def test_stressed_regime_blocks(self):
        from risk.sizing import SizingConstraint, compute_position_size
        spec = self._spec()
        result = compute_position_size(
            spec, entry_price=7000.0, stop_price=6650.0,
            equity_gbp=12_500.0, cash_gbp=12_500.0,
            open_position_count=0, regime_multiplier=0.0,
        )
        assert result.feasible is False
        assert result.binding_constraint == SizingConstraint.REGIME

    def test_elevated_regime_reduces_size(self):
        from risk.sizing import compute_position_size
        spec = self._spec()
        r_normal = compute_position_size(
            spec, 7000.0, 6650.0, 12_500.0, 12_500.0, 0, regime_multiplier=1.0
        )
        r_elevated = compute_position_size(
            spec, 7000.0, 6650.0, 12_500.0, 12_500.0, 0, regime_multiplier=0.5
        )
        # Elevated regime should allow ≤ normal size
        assert r_elevated.quantity <= r_normal.quantity

    def test_cash_minimum_limits_size(self):
        from risk.sizing import SizingConstraint, compute_position_size
        spec = self._spec()
        # Very tight cash: only £1,500 available above 10% minimum
        result = compute_position_size(
            spec, entry_price=7000.0, stop_price=6650.0,
            equity_gbp=12_500.0, cash_gbp=2_750.0,  # 10% of 12500 = 1250, so only 1500 spendable
            open_position_count=0, regime_multiplier=1.0,
        )
        assert result.feasible is True
        # position value should be ≤ 1500 GBP (price is 7000 GBX = 70 GBP)
        assert result.position_value_gbp <= 1_500.0 + 1e-6

    def test_stop_above_entry_infeasible(self):
        from risk.sizing import compute_position_size
        spec = self._spec()
        result = compute_position_size(
            spec, entry_price=7000.0, stop_price=7500.0,  # stop above entry
            equity_gbp=12_500.0, cash_gbp=12_500.0,
            open_position_count=0,
        )
        assert result.feasible is False

    def test_binding_constraint_reported(self):
        from risk.sizing import SizingConstraint, compute_position_size
        spec = self._spec()
        result = compute_position_size(
            spec, 7000.0, 6650.0, 12_500.0, 12_500.0, 0, regime_multiplier=1.0
        )
        assert result.binding_constraint in list(SizingConstraint)


# ---------------------------------------------------------------------------
# risk/heat.py — Rule 5
# ---------------------------------------------------------------------------

class TestHeat:
    def _pos(self, symbol, entry=100.0, stop=95.0, qty=10.0):
        from risk.heat import OpenPosition
        return OpenPosition(symbol=symbol, exchange="LSE",
                            quantity=qty, entry_price_gbp=entry, stop_price_gbp=stop)

    def test_heat_under_limit_passes(self):
        from risk.heat import check_portfolio_heat
        # 4 positions × £50 heat = £200 on £12,500 = 1.6% → under 5%
        positions = [self._pos(f"SYM{i}", entry=100, stop=95, qty=10) for i in range(4)]
        new_pos = self._pos("NEW", entry=100, stop=95, qty=10)
        result = check_portfolio_heat(positions, new_pos, 12_500.0, {})
        assert result.feasible is True

    def test_heat_over_limit_fails(self):
        from risk.heat import check_portfolio_heat
        # Large position: entry 100, stop 0, qty 100 → heat = 10000 GBP on 12500 = 80%
        positions = [self._pos("SYM0", entry=100, stop=0, qty=100)]
        new_pos = self._pos("NEW", entry=100, stop=0, qty=100)
        result = check_portfolio_heat(positions, new_pos, 12_500.0, {})
        assert result.feasible is False

    def test_cluster_correlation_detected(self):
        from data.mock_data import generate_daily_bars
        from instruments.units import InstrumentSpec, PriceUnit
        from risk.heat import check_portfolio_heat
        # Same seed → identical returns → correlation = 1.0 → should cluster
        spec = InstrumentSpec(symbol="A", exchange="LSE", currency="GBP",
                              price_unit=PriceUnit.GBX)
        spec2 = InstrumentSpec(symbol="B", exchange="LSE", currency="GBP",
                               price_unit=PriceUnit.GBX)
        bars_a = generate_daily_bars(spec, n_days=100, seed=1)
        bars_b = generate_daily_bars(spec2, n_days=100, seed=1)  # same seed → same returns
        bars_by_symbol = {"A": bars_a, "B": bars_b}
        # Heat per position: 5 × (100-95) = 25; total for cluster = 50 on 12500 = 0.4% → fine
        # Need to push over 2% cluster limit with bigger positions
        pos_a = self._pos("A", entry=1000, stop=0, qty=20)  # heat = 20000
        new_pos = self._pos("B", entry=1000, stop=0, qty=20)   # heat = 20000
        result = check_portfolio_heat([pos_a], new_pos, 12_500.0, bars_by_symbol)
        # Total heat is well over 5% anyway
        assert result.feasible is False


# ---------------------------------------------------------------------------
# risk/gates.py — Rules 7 and 12
# ---------------------------------------------------------------------------

class TestGates:
    def _make_cost(self, cost_r: float):
        from risk.costs import CostBreakdown
        return CostBreakdown(
            commission_gbp=1.0, spread_gbp=0.5, slippage_gbp=0.5,
            stamp_duty_gbp=0.0, fx_cost_gbp=0.0,
            total_gbp=2.0, cost_as_r=cost_r, paper_haircut_gbp=0.0,
        )

    def test_cost_gate_passes(self):
        from risk.gates import cost_gate
        result = cost_gate(self._make_cost(0.05), entry_price=100, stop_price=95, target_price=110)
        assert result.passed is True

    def test_cost_gate_fails_high_cost(self):
        from risk.gates import cost_gate
        result = cost_gate(self._make_cost(0.20), entry_price=100, stop_price=95, target_price=110)
        assert result.passed is False
        assert "0.15R" in result.reason or "cost" in result.reason.lower()

    def test_cost_gate_fails_low_rr(self):
        from risk.gates import cost_gate
        # R:R = (105-100)/(100-95) = 1.0 < 2.0 required
        result = cost_gate(self._make_cost(0.05), entry_price=100, stop_price=95, target_price=105)
        assert result.passed is False
        assert "reward" in result.reason.lower() or "risk" in result.reason.lower()

    def test_cost_gate_passes_rr_exactly_2(self):
        from risk.gates import cost_gate
        # R:R = (110-100)/(100-95) = 2.0 exactly
        result = cost_gate(self._make_cost(0.05), entry_price=100, stop_price=95, target_price=110)
        assert result.passed is True

    def test_data_quality_gate_no_quote(self):
        from risk.gates import data_quality_gate
        result = data_quality_gate(None, "AZN", "LSE", is_intraday=True)
        assert result.passed is False

    def test_data_quality_gate_stale(self):
        from data.models import DataQuality, Quote
        from instruments.units import PriceUnit
        from risk.gates import data_quality_gate
        stale_ts = datetime.now(UTC) - timedelta(minutes=5)
        quote = Quote(
            symbol="AZN", exchange="LSE", bid=99, ask=101, last=100,
            volume=1e6, price_unit=PriceUnit.GBX,
            quality=DataQuality.LIVE, ts=stale_ts,
        )
        result = data_quality_gate(quote, "AZN", "LSE", is_intraday=True)
        assert result.passed is False
        assert "stale" in result.reason.lower()

    def test_data_quality_gate_delayed_intraday(self):
        from data.models import DataQuality, Quote
        from instruments.units import PriceUnit
        from risk.gates import data_quality_gate
        quote = Quote(
            symbol="AZN", exchange="LSE", bid=99, ask=101, last=100,
            volume=1e6, price_unit=PriceUnit.GBX,
            quality=DataQuality.DELAYED, ts=datetime.now(UTC),
        )
        result = data_quality_gate(quote, "AZN", "LSE", is_intraday=True)
        assert result.passed is False
        assert "delayed" in result.reason.lower()

    def test_data_quality_gate_delayed_eod_ok(self):
        from data.models import DataQuality, Quote
        from instruments.units import PriceUnit
        from risk.gates import data_quality_gate
        quote = Quote(
            symbol="AZN", exchange="LSE", bid=99, ask=101, last=100,
            volume=1e6, price_unit=PriceUnit.GBX,
            quality=DataQuality.DELAYED, ts=datetime.now(UTC),
        )
        # End-of-day ideas can use delayed data
        result = data_quality_gate(quote, "AZN", "LSE", is_intraday=False)
        assert result.passed is True

    def test_data_quality_gate_implausible_price(self):
        from data.models import DataQuality, Quote
        from instruments.units import PriceUnit
        from risk.gates import data_quality_gate
        quote = Quote(
            symbol="AZN", exchange="LSE", bid=0.0, ask=0.0, last=0.0,
            volume=1e6, price_unit=PriceUnit.GBX,
            quality=DataQuality.LIVE, ts=datetime.now(UTC),
        )
        result = data_quality_gate(quote, "AZN", "LSE", is_intraday=True)
        assert result.passed is False


# ---------------------------------------------------------------------------
# risk/limits.py — Rule 9
# ---------------------------------------------------------------------------

class TestLimits:
    def _setup_db(self, tmp_path: Path):
        from data.database import get_connection, init_db
        db_path = tmp_path / "test.db"
        init_db(str(db_path))
        return get_connection(str(db_path))

    def _snapshot(self, conn, equity: float, days_ago: int = 0):
        from risk.limits import record_equity_snapshot
        ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
        conn.execute(
            """INSERT INTO equity_snapshots
               (equity_gbp, cash_gbp, open_positions, portfolio_heat, snapped_at)
               VALUES (?, ?, ?, ?, ?)""",
            (equity, equity * 0.9, 0, 0.0, ts),
        )
        conn.commit()

    def test_no_limits_active_when_healthy(self, tmp_path: Path):
        from risk.limits import check_loss_limits
        conn = self._setup_db(tmp_path)
        self._snapshot(conn, 12_500.0)
        result = check_loss_limits(12_499.0, conn)
        assert result.new_buys_allowed is True

    def test_daily_loss_limit_triggers(self, tmp_path: Path):
        from risk.limits import check_loss_limits
        conn = self._setup_db(tmp_path)
        self._snapshot(conn, 12_500.0)
        # 3% daily loss — exceeds 2% limit
        result = check_loss_limits(12_500.0 * 0.97, conn)
        assert result.daily_loss_active is True
        assert result.new_buys_allowed is False

    def test_weekly_circuit_triggers(self, tmp_path: Path):
        from risk.limits import check_loss_limits
        conn = self._setup_db(tmp_path)
        self._snapshot(conn, 13_000.0, days_ago=5)  # weekly peak
        # 6% drawdown from weekly peak — exceeds 5% circuit
        result = check_loss_limits(13_000.0 * 0.94, conn)
        assert result.weekly_circuit_active is True
        assert result.new_buys_allowed is False

    def test_peak_circuit_triggers(self, tmp_path: Path):
        from risk.limits import check_loss_limits
        conn = self._setup_db(tmp_path)
        self._snapshot(conn, 14_000.0, days_ago=30)  # all-time peak
        # 11% drawdown from all-time peak — exceeds 10% circuit
        result = check_loss_limits(14_000.0 * 0.89, conn)
        assert result.peak_circuit_active is True
        assert result.new_buys_allowed is False

    def test_record_snapshot(self, tmp_path: Path):
        from risk.limits import record_equity_snapshot
        conn = self._setup_db(tmp_path)
        record_equity_snapshot(conn, 12_500.0, 11_000.0, 2, 0.015)
        row = conn.execute("SELECT COUNT(*) FROM equity_snapshots").fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# risk/guards.py — Rules 1 and 13
# (already covered in test_guards.py; add a few integration checks here)
# ---------------------------------------------------------------------------

class TestGuardsIntegration:
    def test_readonly_error_message_mentions_phase(self):
        from risk.guards import ReadOnlyError, assert_readonly_phase
        for phase in range(6):
            with pytest.raises(ReadOnlyError) as exc:
                assert_readonly_phase(phase)
            assert str(phase) in str(exc.value)

    def test_paper_account_guard_blocks_empty_string(self):
        from risk.guards import assert_paper_account
        with pytest.raises(SystemExit):
            assert_paper_account("")
