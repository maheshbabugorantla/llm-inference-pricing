from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from pricing.scrapers.base import ScrapedPrice
from pricing.services.pricing_artifacts import (
    artifact_from_scraped_prices,
    read_artifact,
    scraped_prices_from_artifact,
    write_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_price(hourly: str = "2.05", tier: str = "on_demand") -> ScrapedPrice:
    return ScrapedPrice(
        provider_slug="runpod",
        gpu_slug_hint="H100 SXM",
        tier=tier,
        region="",
        hourly_usd=Decimal(hourly),
        available=True,
        raw={"gpu": "H100 SXM"},
    )


def test_round_trip_preserves_decimal_precision(tmp_path: Path) -> None:
    prices = [_make_price("2.123456")]
    artifact = artifact_from_scraped_prices("runpod", "https://example.com", prices)
    path = tmp_path / "runpod.json"
    write_artifact(path, artifact)
    loaded = read_artifact(path)
    assert loaded.prices[0].hourly_usd == "2.123456"
    recovered = scraped_prices_from_artifact(loaded)
    assert recovered[0].hourly_usd == Decimal("2.123456")


def test_read_rejects_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    raw = {
        "schema_version": 99,
        "provider_slug": "runpod",
        "scraped_at": "2026-01-01T00:00:00+00:00",
        "source_url": "https://example.com",
        "scraper_version": "abc123",
        "prices": [],
    }
    path.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        read_artifact(path)


def test_read_rejects_negative_hourly_usd(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    raw = {
        "schema_version": 1,
        "provider_slug": "runpod",
        "scraped_at": "2026-01-01T00:00:00+00:00",
        "source_url": "https://example.com",
        "scraper_version": "abc123",
        "prices": [
            {
                "gpu_slug_hint": "H100 SXM",
                "tier": "on_demand",
                "region": "",
                "hourly_usd": "-1.00",
                "available": True,
                "raw": {},
            }
        ],
    }
    path.write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        read_artifact(path)


def test_artifact_from_scraped_prices_captures_all_prices() -> None:
    prices = [_make_price("2.05"), _make_price("1.99", tier="community")]
    artifact = artifact_from_scraped_prices("runpod", "https://example.com", prices)
    assert len(artifact.prices) == 2
    assert artifact.provider_slug == "runpod"
    assert artifact.source_url == "https://example.com"


def test_write_is_deterministic(tmp_path: Path) -> None:
    prices = [_make_price("2.05"), _make_price("1.99", tier="community")]
    artifact = artifact_from_scraped_prices("runpod", "https://example.com", prices)
    path = tmp_path / "runpod.json"
    write_artifact(path, artifact)
    content1 = path.read_text()
    write_artifact(path, artifact)
    content2 = path.read_text()
    assert content1 == content2
    # sorted keys, indent=2, trailing newline
    assert content1.endswith("\n")
    parsed = json.loads(content1)
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_scraped_at_is_tz_aware(tmp_path: Path) -> None:
    artifact = artifact_from_scraped_prices("runpod", "https://example.com", [])
    assert artifact.scraped_at.tzinfo is not None


def test_scraped_prices_from_artifact_returns_scraped_price_objects(tmp_path: Path) -> None:
    prices = [_make_price("2.05")]
    artifact = artifact_from_scraped_prices("runpod", "https://example.com", prices)
    path = tmp_path / "runpod.json"
    write_artifact(path, artifact)
    loaded = read_artifact(path)
    recovered = scraped_prices_from_artifact(loaded)
    assert len(recovered) == 1
    assert isinstance(recovered[0], ScrapedPrice)
    assert recovered[0].provider_slug == "runpod"
    assert recovered[0].gpu_slug_hint == "H100 SXM"
    assert recovered[0].hourly_usd == Decimal("2.05")
