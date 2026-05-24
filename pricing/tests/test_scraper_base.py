"""ScrapedPrice contract tests."""

from __future__ import annotations

import unittest
from decimal import Decimal

from pydantic import ValidationError

from pricing.scrapers.base import ScrapedPrice


class ScrapedPriceContractTest(unittest.TestCase):
    def test_scraped_price_preserves_raw_payload_exactly(self):
        raw = {"instanceType": "p5.48xlarge", "pricePerUnit": "98.32", "region": "us-east-1"}
        price = ScrapedPrice(
            provider_slug="aws",
            gpu_slug_hint="nvidia-h100-sxm-80",
            tier="on_demand",
            hourly_usd=Decimal("12.29"),
            region="us-east-1",
            raw=raw,
        )
        self.assertEqual(price.raw, raw)

    def test_scraped_price_with_empty_region_is_valid(self):
        price = ScrapedPrice(
            provider_slug="lambda",
            gpu_slug_hint="H100 SXM",
            tier="on_demand",
            hourly_usd=Decimal("2.49"),
            region="",
            raw={},
        )
        self.assertEqual(price.region, "")

    def test_scraped_price_available_defaults_to_true(self):
        price = ScrapedPrice(
            provider_slug="runpod",
            gpu_slug_hint="H100 SXM",
            tier="community",
            hourly_usd=Decimal("1.99"),
            region="",
            raw={},
        )
        self.assertTrue(price.available)

    def test_scraped_price_is_immutable_after_construction(self):
        price = ScrapedPrice(
            provider_slug="runpod",
            gpu_slug_hint="H100 SXM",
            tier="community",
            hourly_usd=Decimal("1.99"),
            region="",
            available=True,
            raw={"x": 1},
        )
        with self.assertRaises(ValidationError):
            price.hourly_usd = Decimal("2.99")  # type: ignore[misc]

    def test_scraped_price_rejects_string_price_not_decimal(self):
        with self.assertRaises(ValidationError):
            ScrapedPrice(
                provider_slug="runpod",
                gpu_slug_hint="H100 SXM",
                tier="community",
                hourly_usd="1.99",  # type: ignore[arg-type]
                region="",
                raw={},
            )

    def test_scraped_price_zero_hourly_usd_accepted_for_spot_market(self):
        price = ScrapedPrice(
            provider_slug="runpod",
            gpu_slug_hint="H100 SXM",
            tier="community-spot",
            hourly_usd=Decimal("0"),
            region="",
            raw={},
        )
        self.assertEqual(price.hourly_usd, Decimal("0"))

    def test_scraped_price_available_false_marks_gpu_as_out_of_stock(self):
        price = ScrapedPrice(
            provider_slug="runpod",
            gpu_slug_hint="H100 SXM",
            tier="secure",
            hourly_usd=Decimal("3.29"),
            region="",
            available=False,
            raw={"available": False},
        )
        self.assertFalse(price.available)

    def test_two_scraped_prices_same_gpu_different_tiers_are_independent(self):
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
        self.assertNotEqual(community.tier, secure.tier)
        self.assertNotEqual(community.hourly_usd, secure.hourly_usd)
        self.assertNotEqual(community.raw, secure.raw)
