"""Tests for instruments/units.py — the pence vs pounds safety layer."""

import pytest
from instruments.units import (
    InstrumentSpec,
    PriceUnit,
    find_instrument,
    load_watchlist,
    to_display_price,
    to_gbp,
)


class TestToGbp:
    def test_gbx_to_gbp(self):
        assert to_gbp(100.0, PriceUnit.GBX) == pytest.approx(1.0)

    def test_gbx_zero(self):
        assert to_gbp(0.0, PriceUnit.GBX) == 0.0

    def test_gbp_passthrough(self):
        assert to_gbp(50.0, PriceUnit.GBP) == 50.0

    def test_usd_to_gbp(self):
        assert to_gbp(100.0, PriceUnit.USD, fx_usd_gbp=0.79) == pytest.approx(79.0)

    def test_pence_share_100x_difference(self):
        """A 7 000p share is £70, not £7 000 — the classic unit bug."""
        share_price_pence = 7_000.0
        gbp = to_gbp(share_price_pence, PriceUnit.GBX)
        assert gbp == pytest.approx(70.0)
        assert gbp != pytest.approx(7_000.0)

    def test_etf_gbp_unchanged(self):
        etf_price = 104.60
        assert to_gbp(etf_price, PriceUnit.GBP) == pytest.approx(104.60)


class TestToDisplayPrice:
    def test_gbp_to_gbx(self):
        assert to_display_price(1.0, PriceUnit.GBX) == pytest.approx(100.0)

    def test_gbp_passthrough(self):
        assert to_display_price(50.0, PriceUnit.GBP) == pytest.approx(50.0)

    def test_usd_passthrough(self):
        assert to_display_price(200.0, PriceUnit.USD) == pytest.approx(200.0)

    def test_roundtrip_gbx(self):
        raw = 7_432.5
        assert to_display_price(to_gbp(raw, PriceUnit.GBX), PriceUnit.GBX) == pytest.approx(raw)


class TestInstrumentSpec:
    def test_lse_share_is_uk_share(self):
        spec = InstrumentSpec(
            symbol="AZN", exchange="LSE", currency="GBP",
            price_unit=PriceUnit.GBX, sector="healthcare",
        )
        assert spec.is_uk_share is True
        assert spec.stamp_duty_applies is True

    def test_lse_etf_not_uk_share(self):
        spec = InstrumentSpec(
            symbol="VUSA", exchange="LSE", currency="USD",
            price_unit=PriceUnit.USD, sector="equity_us",
        )
        assert spec.is_uk_share is False
        assert spec.stamp_duty_applies is False

    def test_us_stock_not_uk_share(self):
        spec = InstrumentSpec(
            symbol="AAPL", exchange="NASDAQ", currency="USD",
            price_unit=PriceUnit.USD, sector="technology",
        )
        assert spec.is_uk_share is False

    def test_symbol_upper(self):
        spec = InstrumentSpec(
            symbol="azn", exchange="lse", currency="GBP",
            price_unit=PriceUnit.GBX,
        )
        assert spec.symbol == "AZN"
        assert spec.exchange == "LSE"


class TestLoadWatchlist:
    def test_loads_without_error(self):
        specs = load_watchlist()
        assert len(specs) > 0

    def test_all_lse_shares_are_gbx(self):
        specs = load_watchlist()
        for s in specs:
            if s.exchange == "LSE" and s.is_uk_share:
                assert s.price_unit == PriceUnit.GBX, (
                    f"{s.symbol} on LSE marked as UK share but unit={s.price_unit}"
                )

    def test_find_instrument(self):
        specs = load_watchlist()
        azn = find_instrument(specs, "AZN", "LSE")
        assert azn is not None
        assert azn.price_unit == PriceUnit.GBX

    def test_find_instrument_missing(self):
        specs = load_watchlist()
        result = find_instrument(specs, "NOTEXIST", "LSE")
        assert result is None
