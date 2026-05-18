from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.core.management import call_command

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider
from pricing.scrapers import ScraperEntry

if TYPE_CHECKING:
    from pathlib import Path


def _make_artifact_file(path: Path, slug: str, prices: list[dict] | None = None) -> None:
    data = {
        "schema_version": 1,
        "provider_slug": slug,
        "scraped_at": "2026-01-15T12:00:00+00:00",
        "source_url": f"https://{slug}.example.com",
        "scraper_version": "abc1234",
        "prices": prices
        or [
            {
                "gpu_slug_hint": "H100 SXM",
                "tier": "on_demand",
                "region": "",
                "hourly_usd": "2.50",
                "available": True,
                "raw": {},
            }
        ],
    }
    path.write_text(json.dumps(data, indent=2) + "\n")


def _runpod_provider() -> Provider:
    return Provider.objects.create(
        slug="runpod",
        display_name="RunPod",
        provider_type="cloud",
        data_source_tier="realtime_api",
    )


@pytest.mark.django_db
def test_load_pricing_persists_snapshots_from_artifact_file(tmp_path: Path) -> None:
    _runpod_provider()
    GPUFactory(slug="nvidia-h100-sxm-80")
    _make_artifact_file(tmp_path / "runpod.json", "runpod")

    entry = ScraperEntry(
        scrape_fn=lambda: [],
        gpu_slug_resolver=lambda x: "nvidia-h100-sxm-80",
        source_url="https://api.runpod.io/graphql",
    )
    with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": entry}):
        call_command("load_pricing", "--provider", "runpod", "--from", str(tmp_path))

    assert PricingSnapshot.objects.count() == 1
    snap = PricingSnapshot.objects.get()
    assert snap.hourly_usd == Decimal("2.50")


@pytest.mark.django_db
def test_load_pricing_uses_artifact_scraped_at_not_now(tmp_path: Path) -> None:
    _runpod_provider()
    GPUFactory(slug="nvidia-h100-sxm-80")
    _make_artifact_file(tmp_path / "runpod.json", "runpod")

    entry = ScraperEntry(
        scrape_fn=lambda: [],
        gpu_slug_resolver=lambda x: "nvidia-h100-sxm-80",
        source_url="https://api.runpod.io/graphql",
    )
    with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": entry}):
        call_command("load_pricing", "--provider", "runpod", "--from", str(tmp_path))

    snap = PricingSnapshot.objects.get()
    expected_dt = dt.datetime(2026, 1, 15, 12, 0, 0, tzinfo=dt.UTC)
    assert snap.scraped_at == expected_dt


def test_load_pricing_skips_provider_with_missing_artifact_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = ScraperEntry(
        scrape_fn=lambda: [],
        gpu_slug_resolver=lambda x: x,
        source_url="https://api.runpod.io/graphql",
    )
    with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": entry}):
        call_command("load_pricing", "--provider", "runpod", "--from", str(tmp_path))
    # No exception raised, command skips gracefully


@pytest.mark.django_db
def test_load_pricing_all_loops_over_every_artifact_under_data_pricing(
    tmp_path: Path,
) -> None:
    Provider.objects.create(
        slug="runpod",
        display_name="RunPod",
        provider_type="cloud",
        data_source_tier="realtime_api",
    )
    Provider.objects.create(
        slug="vast",
        display_name="Vast",
        provider_type="cloud",
        data_source_tier="realtime_api",
    )
    GPUFactory(slug="nvidia-h100-sxm-80")
    _make_artifact_file(tmp_path / "runpod.json", "runpod")
    _make_artifact_file(tmp_path / "vast.json", "vast")

    fake_scrapers = {
        "runpod": ScraperEntry(
            scrape_fn=lambda: [],
            gpu_slug_resolver=lambda x: "nvidia-h100-sxm-80",
            source_url="https://api.runpod.io/graphql",
        ),
        "vast": ScraperEntry(
            scrape_fn=lambda: [],
            gpu_slug_resolver=lambda x: "nvidia-h100-sxm-80",
            source_url="https://console.vast.ai/api/v0/bundles/",
        ),
    }
    with patch.dict("pricing.scrapers.SCRAPERS", fake_scrapers, clear=True):
        call_command("load_pricing", "--provider", "all", "--from", str(tmp_path))

    assert PricingSnapshot.objects.count() == 2
