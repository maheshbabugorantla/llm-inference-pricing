"""Tests for AWS GPU pricing scraper (M05.5b.T02)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pricing.scrapers.aws import (
    _INSTANCE_GPU_MAP,
    map_aws_gpu,
    parse_aws_capacity_blocks,
    parse_aws_prices,
)
from pricing.scrapers.base import ParserDriftError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def p5_products() -> list[dict]:
    return json.loads((FIXTURES / "aws_pricing_p5.json").read_text())


@pytest.fixture()
def capacity_blocks() -> list[dict]:
    return json.loads((FIXTURES / "aws_capacity_blocks_p5.json").read_text())


def test_aws_parse_p5_yields_per_gpu_hourly_after_dividing_by_8(p5_products):
    prices = parse_aws_prices(p5_products)
    p5 = next(p for p in prices if p.gpu_slug_hint == "nvidia-h100-sxm-80")
    # p5.48xlarge $98.3232 / 8 GPUs = $12.2904 per GPU
    assert p5.hourly_usd == Decimal("98.3232") / Decimal(8)
    assert p5.tier == "on_demand"
    assert p5.provider_slug == "aws"


def test_aws_parse_p4d_yields_per_gpu_hourly_after_dividing_by_8(p5_products):
    prices = parse_aws_prices(p5_products)
    p4d = next(p for p in prices if "a100-sxm-40" in p.gpu_slug_hint)
    # p4d.24xlarge $32.7726 / 8 GPUs
    assert p4d.hourly_usd == Decimal("32.7726") / Decimal(8)


def test_aws_parse_g5_yields_per_gpu_hourly_after_dividing_by_8(p5_products):
    prices = parse_aws_prices(p5_products)
    g5 = next(p for p in prices if "a10g" in p.gpu_slug_hint)
    # g5.48xlarge $16.2880 / 8 GPUs
    assert g5.hourly_usd == Decimal("16.2880") / Decimal(8)


def test_aws_capacity_block_amortizes_upfront_over_duration(capacity_blocks):
    prices = parse_aws_capacity_blocks(capacity_blocks)
    assert len(prices) == 2
    # First offering: $131712 upfront / (2 instances * 336 hours) = $196/instance-hr / 8 GPUs
    cb = next(p for p in prices if "336" in str(p.raw.get("duration_hours", "")))
    expected_per_instance = Decimal("131712.00") / (Decimal(2) * Decimal(336))
    expected_per_gpu = expected_per_instance / Decimal(8)
    assert cb.hourly_usd == expected_per_gpu
    assert cb.tier.startswith("capacity-block-")
    assert "14d" in cb.tier  # 336 hours = 14 days


def test_aws_capacity_block_7d_tier_name(capacity_blocks):
    prices = parse_aws_capacity_blocks(capacity_blocks)
    # Second offering: 168 hours = 7 days
    cb = next(p for p in prices if "168" in str(p.raw.get("duration_hours", "")))
    assert cb.tier == "capacity-block-7d"


def test_aws_parse_handles_missing_instance_type_gracefully():
    unknown_product = [
        {
            "attributes": {"instanceType": "m5.xlarge", "regionCode": "us-east-1"},
            "terms": {"OnDemand": {"k": {"priceDimensions": {"kk": {"pricePerUnit": {"USD": "0.192"}}}}}},
        }
    ]
    with pytest.raises(ParserDriftError):
        parse_aws_prices(unknown_product)


def test_aws_parser_returns_decimals(p5_products):
    prices = parse_aws_prices(p5_products)
    for p in prices:
        assert isinstance(p.hourly_usd, Decimal)


def test_aws_unmapped_instance_type_is_logged_not_dropped_silently(caplog, p5_products):
    unknown = [
        {
            "attributes": {"instanceType": "p99.huge", "regionCode": "us-east-1"},
            "terms": {"OnDemand": {"k": {"priceDimensions": {"kk": {"pricePerUnit": {"USD": "999"}}}}}},
        }
    ] + p5_products
    import logging

    with caplog.at_level(logging.INFO, logger="pricing.scrapers.aws"):
        prices = parse_aws_prices(unknown)
    assert any("p99.huge" in r.message for r in caplog.records)
    assert len(prices) == len(parse_aws_prices(p5_products))


def test_aws_empty_capacity_blocks_returns_empty_list():
    assert parse_aws_capacity_blocks([]) == []
