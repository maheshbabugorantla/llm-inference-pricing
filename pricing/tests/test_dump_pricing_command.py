from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.core.management import call_command

from pricing.models import PricingSnapshot
from pricing.scrapers import ScraperEntry
from pricing.scrapers.base import ScrapedPrice

if TYPE_CHECKING:
    from pathlib import Path


def _fake_price(slug: str = "runpod") -> ScrapedPrice:
    return ScrapedPrice(
        provider_slug=slug,
        gpu_slug_hint="H100 SXM",
        tier="on_demand",
        region="",
        hourly_usd=Decimal("2.50"),
        available=True,
        raw={"gpu": "H100 SXM", "price": "2.50"},
    )


def _entry(slug: str, prices: list[ScrapedPrice] | None = None) -> ScraperEntry:
    p = prices if prices is not None else [_fake_price(slug)]
    return ScraperEntry(
        scrape_fn=lambda: p,
        gpu_slug_resolver=lambda x: x,
        source_url=f"https://{slug}.example.com",
    )


def test_dump_pricing_writes_per_provider_json(tmp_path: Path) -> None:
    with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": _entry("runpod")}):
        call_command("dump_pricing", "--provider", "runpod", "--out", str(tmp_path))

    out_file = tmp_path / "runpod.json"
    assert out_file.exists()
    data = json.loads(out_file.read_text())
    assert data["provider_slug"] == "runpod"
    assert data["schema_version"] == 1
    assert len(data["prices"]) == 1
    assert data["prices"][0]["hourly_usd"] == "2.50"


def test_dump_pricing_all_writes_all_providers(tmp_path: Path) -> None:
    fake_scrapers = {
        "runpod": _entry("runpod"),
        "lambda": _entry("lambda"),
        "vast": _entry("vast"),
        "nebius": _entry("nebius"),
    }
    with patch.dict("pricing.scrapers.SCRAPERS", fake_scrapers, clear=True):
        call_command("dump_pricing", "--provider", "all", "--out", str(tmp_path))

    for slug in ("runpod", "lambda", "vast", "nebius"):
        assert (tmp_path / f"{slug}.json").exists()


def test_dump_pricing_does_not_overwrite_on_scraper_exception(tmp_path: Path) -> None:
    prior_content = '{"schema_version": 1, "provider_slug": "runpod", "prices": []}\n'
    (tmp_path / "runpod.json").write_text(prior_content)

    broken = ScraperEntry(
        scrape_fn=lambda: (_ for _ in ()).throw(RuntimeError("network down")),
        gpu_slug_resolver=lambda x: x,
        source_url="https://runpod.example.com",
    )
    with (
        patch.dict("pricing.scrapers.SCRAPERS", {"runpod": broken}),
        pytest.raises(SystemExit),
    ):
        call_command("dump_pricing", "--provider", "runpod", "--out", str(tmp_path))

    assert (tmp_path / "runpod.json").read_text() == prior_content


def test_dump_pricing_returns_nonzero_on_partial_failure(tmp_path: Path) -> None:
    broken = ScraperEntry(
        scrape_fn=lambda: (_ for _ in ()).throw(RuntimeError("bang")),
        gpu_slug_resolver=lambda x: x,
        source_url="https://runpod.example.com",
    )
    with (
        patch.dict("pricing.scrapers.SCRAPERS", {"runpod": broken}),
        pytest.raises(SystemExit) as exc_info,
    ):
        call_command("dump_pricing", "--provider", "runpod", "--out", str(tmp_path))

    assert exc_info.value.code != 0


@pytest.mark.django_db
def test_dump_pricing_does_not_touch_database(tmp_path: Path) -> None:
    with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": _entry("runpod")}):
        call_command("dump_pricing", "--provider", "runpod", "--out", str(tmp_path))

    assert PricingSnapshot.objects.count() == 0
