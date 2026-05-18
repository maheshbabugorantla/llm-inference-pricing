from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider
from pricing.scrapers.base import ScrapedPrice
from pricing.services.scrape_runner import persist_prices


@pytest.mark.django_db
def test_persist_prices_creates_snapshots() -> None:
    Provider.objects.create(
        slug="runpod",
        display_name="RunPod",
        provider_type="cloud",
        data_source_tier="realtime_api",
    )
    GPUFactory(slug="nvidia-h100-sxm-80")

    prices = [
        ScrapedPrice(
            provider_slug="runpod",
            gpu_slug_hint="NVIDIA H100 SXM",
            tier="community",
            hourly_usd=Decimal("1.99"),
            region="",
            available=True,
            raw={"displayName": "NVIDIA H100 SXM"},
        )
    ]
    n = persist_prices(prices, gpu_slug_resolver=lambda hint: "nvidia-h100-sxm-80")
    assert n == 1
    assert PricingSnapshot.objects.count() == 1
    snap = PricingSnapshot.objects.get()
    assert snap.hourly_usd == Decimal("1.99")
    assert snap.tier == "community"
    assert snap.scraped_at.tzinfo is not None


@pytest.mark.django_db
def test_persist_prices_skips_unmapped_gpu() -> None:
    Provider.objects.create(
        slug="runpod",
        display_name="RunPod",
        provider_type="cloud",
        data_source_tier="realtime_api",
    )
    prices = [
        ScrapedPrice(
            provider_slug="runpod",
            gpu_slug_hint="UnknownGPU",
            tier="community",
            hourly_usd=Decimal("1.99"),
            region="",
            available=True,
            raw={},
        )
    ]
    n = persist_prices(prices, gpu_slug_resolver=lambda hint: None)
    assert n == 0


@pytest.mark.django_db
def test_persist_prices_uses_scraped_at_when_provided() -> None:
    Provider.objects.create(
        slug="runpod",
        display_name="RunPod",
        provider_type="cloud",
        data_source_tier="realtime_api",
    )
    GPUFactory(slug="nvidia-h100-sxm-80")

    fixed_dt = dt.datetime(2026, 1, 10, 6, 0, 0, tzinfo=dt.UTC)
    prices = [
        ScrapedPrice(
            provider_slug="runpod",
            gpu_slug_hint="x",
            tier="on_demand",
            hourly_usd=Decimal("1.00"),
            region="",
            available=True,
            raw={},
        )
    ]
    persist_prices(
        prices,
        gpu_slug_resolver=lambda hint: "nvidia-h100-sxm-80",
        scraped_at=fixed_dt,
    )
    snap = PricingSnapshot.objects.get()
    assert snap.scraped_at == fixed_dt


@pytest.mark.django_db
def test_persist_prices_rejects_missing_provider() -> None:
    prices = [
        ScrapedPrice(
            provider_slug="never-seen",
            gpu_slug_hint="x",
            tier="x",
            hourly_usd=Decimal("1"),
            region="",
            available=True,
            raw={},
        )
    ]
    with pytest.raises(Provider.DoesNotExist):
        persist_prices(prices, gpu_slug_resolver=lambda hint: None)
