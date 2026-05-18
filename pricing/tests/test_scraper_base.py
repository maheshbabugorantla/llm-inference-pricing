from __future__ import annotations

from decimal import Decimal

import pytest

from pricing.scrapers.base import ScrapedPrice


def test_scraped_price_is_immutable() -> None:
    p = ScrapedPrice(
        provider_slug="runpod",
        gpu_slug_hint="NVIDIA H100 SXM",
        tier="community",
        hourly_usd=Decimal("1.99"),
        region="",
        available=True,
        raw={"x": 1},
    )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        p.hourly_usd = Decimal("2.99")  # type: ignore[misc]


def test_scraped_price_requires_decimal_for_price() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScrapedPrice(
            provider_slug="x",
            gpu_slug_hint="x",
            tier="x",
            hourly_usd="1.99",  # type: ignore[arg-type]  # should be Decimal
            region="",
            available=True,
            raw={},
        )
