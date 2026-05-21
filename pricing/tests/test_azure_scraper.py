"""Tests for Azure GPU pricing scraper (M05.5b.T04)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pricing.scrapers.azure import map_azure_gpu, parse_azure_prices
from pricing.scrapers.base import ParserDriftError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def nd_series_items() -> list[dict]:
    return json.loads((FIXTURES / "azure_prices_nd_series.json").read_text())


def test_azure_parse_h100_on_demand_yields_per_gpu_hourly(nd_series_items):
    prices = parse_azure_prices(nd_series_items)
    h100 = next(p for p in prices if p.gpu_slug_hint == "nvidia-h100-sxm-80" and p.tier == "on_demand")
    # $98.32 / 8 GPUs
    assert h100.hourly_usd == Decimal("98.32") / Decimal(8)
    assert h100.provider_slug == "azure"
    assert h100.region == "eastus"


def test_azure_parse_reserved_1yr_tier(nd_series_items):
    prices = parse_azure_prices(nd_series_items)
    h100_reserved = next(
        p for p in prices if p.gpu_slug_hint == "nvidia-h100-sxm-80" and p.tier == "reserved-1yr"
    )
    assert h100_reserved.hourly_usd == Decimal("72.11") / Decimal(8)


def test_azure_parse_reserved_3yr_tier(nd_series_items):
    prices = parse_azure_prices(nd_series_items)
    h100_3yr = next(
        p for p in prices if p.gpu_slug_hint == "nvidia-h100-sxm-80" and p.tier == "reserved-3yr"
    )
    assert h100_3yr.hourly_usd == Decimal("60.45") / Decimal(8)


def test_azure_parse_t4_single_gpu_no_division(nd_series_items):
    prices = parse_azure_prices(nd_series_items)
    t4 = next(p for p in prices if p.gpu_slug_hint == "nvidia-t4")
    # NC4as_T4_v3 has 1 GPU — no division
    assert t4.hourly_usd == Decimal("0.526")


def test_azure_parse_returns_decimals(nd_series_items):
    prices = parse_azure_prices(nd_series_items)
    for p in prices:
        assert isinstance(p.hourly_usd, Decimal)


def test_azure_parse_unknown_sku_raises_parser_drift_error():
    unknown_items = [
        {
            "armSkuName": "Standard_Unknown_GPU_v99",
            "armRegionName": "eastus",
            "retailPrice": 1.0,
            "reservationTerm": "",
            "priceType": "Consumption",
        }
    ]
    with pytest.raises(ParserDriftError):
        parse_azure_prices(unknown_items)


def test_azure_parse_zero_price_skipped():
    items = [
        {
            "armSkuName": "Standard_ND96isr_H100_v5",
            "armRegionName": "eastus",
            "retailPrice": 0.0,
            "reservationTerm": "",
            "priceType": "Consumption",
        }
    ]
    with pytest.raises(ParserDriftError):
        parse_azure_prices(items)


def test_azure_map_gpu_known_sku():
    assert map_azure_gpu("Standard_ND96isr_H100_v5") == "nvidia-h100-sxm-80"


def test_azure_map_gpu_unknown_sku_returns_none():
    assert map_azure_gpu("Standard_D4s_v3") is None
