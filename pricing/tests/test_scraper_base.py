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


def test_scraped_price_zero_hourly_usd_accepted_for_spot_market():
    """Spot prices can transiently hit $0 when market capacity floods supply.
    A zero price must be accepted — rejecting it would create a gap in the
    historical record that breaks cost-trend charting."""
    price = ScrapedPrice(
        provider_slug="runpod",
        gpu_slug_hint="H100 SXM",
        tier="community-spot",
        hourly_usd=Decimal("0"),
        region="",
        raw={},
    )
    assert price.hourly_usd == Decimal("0")


def test_scraped_price_available_false_marks_gpu_as_out_of_stock():
    """available=False must be preserved exactly — scraper signals that capacity
    is sold out for this tier. Forcing True would show the GPU as available in
    the cost grid when it isn't, misleading cost comparisons."""
    price = ScrapedPrice(
        provider_slug="runpod",
        gpu_slug_hint="H100 SXM",
        tier="secure",
        hourly_usd=Decimal("3.29"),
        region="",
        available=False,
        raw={"available": False},
    )
    assert price.available is False


def test_two_scraped_prices_same_gpu_different_tiers_are_independent():
    """A single GPU on RunPod has multiple tiers (community, secure, spot).
    Each must be a separate, independent ScrapedPrice object — sharing state
    between them would make one scraper mutation corrupt another tier's price."""
    community = ScrapedPrice(
        provider_slug="runpod",
        gpu_slug_hint="H100 SXM",
        tier="community",
        hourly_usd=Decimal("1.99"),
        region="",
        raw={"tier": "community"},
    )
    secure = ScrapedPrice(
        provider_slug="runpod",
        gpu_slug_hint="H100 SXM",
        tier="secure",
        hourly_usd=Decimal("3.29"),
        region="",
        raw={"tier": "secure"},
    )
    assert community.tier != secure.tier
    assert community.hourly_usd != secure.hourly_usd
    assert community.raw != secure.raw
