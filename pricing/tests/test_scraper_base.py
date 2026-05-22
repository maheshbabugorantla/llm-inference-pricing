"""ScrapedPrice contract tests.

Design: ScrapedPrice is the transport object every scraper returns and
persist_prices consumes. These tests prove the contract: raw payloads are
preserved exactly, optional fields default correctly, and the type is
immutable so a scraper cannot mutate a price after it leaves the parser.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from pricing.scrapers.base import ScrapedPrice


def test_scraped_price_preserves_raw_payload_exactly():
    """The raw payload must reach persist_prices unchanged — it is stored as
    raw_payload in PricingSnapshot for audit and drift-detection purposes.
    Any mutation or truncation here would corrupt the audit trail."""
    raw = {"instanceType": "p5.48xlarge", "pricePerUnit": "98.32", "region": "us-east-1"}
    price = ScrapedPrice(
        provider_slug="aws",
        gpu_slug_hint="nvidia-h100-sxm-80",
        tier="on_demand",
        hourly_usd=Decimal("12.29"),
        region="us-east-1",
        raw=raw,
    )
    assert price.raw == raw


def test_scraped_price_with_empty_region_is_valid():
    """Lambda Labs and Nebius don't expose per-region pricing — their scrapers
    emit region="" and that must be accepted, not rejected as missing."""
    price = ScrapedPrice(
        provider_slug="lambda",
        gpu_slug_hint="H100 SXM",
        tier="on_demand",
        hourly_usd=Decimal("2.49"),
        region="",
        raw={},
    )
    assert price.region == ""


def test_scraped_price_available_defaults_to_true():
    """Scrapers that don't explicitly signal unavailability should default to
    available=True so prices are not silently excluded from cost cells."""
    price = ScrapedPrice(
        provider_slug="runpod",
        gpu_slug_hint="H100 SXM",
        tier="community",
        hourly_usd=Decimal("1.99"),
        region="",
        raw={},
    )
    assert price.available is True


def test_scraped_price_is_immutable_after_construction():
    """ScrapedPrice is frozen so scrapers cannot accidentally mutate a price
    after it leaves the parser — preventing a class of data-corruption bugs."""
    price = ScrapedPrice(
        provider_slug="runpod",
        gpu_slug_hint="H100 SXM",
        tier="community",
        hourly_usd=Decimal("1.99"),
        region="",
        available=True,
        raw={"x": 1},
    )
    with pytest.raises(ValidationError):
        price.hourly_usd = Decimal("2.99")  # type: ignore[misc]


def test_scraped_price_rejects_string_price_not_decimal():
    """hourly_usd must be Decimal, not a string. Accepting strings would allow
    float-precision errors to propagate silently into cost-cell calculations."""
    with pytest.raises(ValidationError):
        ScrapedPrice(
            provider_slug="runpod",
            gpu_slug_hint="H100 SXM",
            tier="community",
            hourly_usd="1.99",  # type: ignore[arg-type]
            region="",
            raw={},
        )
