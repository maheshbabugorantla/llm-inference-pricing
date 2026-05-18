from __future__ import annotations

import json
import subprocess
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, Literal

from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pricing.scrapers.base import ScrapedPrice

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class ScrapedPriceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gpu_slug_hint: str
    tier: str
    region: str
    hourly_usd: str
    available: bool
    raw: dict[str, Any]

    @field_validator("hourly_usd")
    @classmethod
    def non_negative(cls, v: str) -> str:
        if Decimal(v) < 0:
            raise ValueError("hourly_usd must be non-negative")
        return v


class PricingArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Annotated[Literal[1], Field()] = 1
    provider_slug: str
    scraped_at: datetime
    source_url: str
    scraper_version: str
    prices: list[ScrapedPriceRecord]


def _scraper_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def artifact_from_scraped_prices(
    provider_slug: str,
    source_url: str,
    prices: Iterable[ScrapedPrice],
) -> PricingArtifact:
    records = [
        ScrapedPriceRecord(
            gpu_slug_hint=p.gpu_slug_hint,
            tier=p.tier,
            region=p.region,
            hourly_usd=str(p.hourly_usd),
            available=p.available,
            raw=p.raw,
        )
        for p in prices
    ]
    return PricingArtifact(
        provider_slug=provider_slug,
        scraped_at=timezone.now(),
        source_url=source_url,
        scraper_version=_scraper_version(),
        prices=records,
    )


def write_artifact(path: Path, artifact: PricingArtifact) -> None:
    data = json.loads(artifact.model_dump_json())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_artifact(path: Path) -> PricingArtifact:
    return PricingArtifact.model_validate_json(path.read_text(encoding="utf-8"))


def scraped_prices_from_artifact(artifact: PricingArtifact) -> list[ScrapedPrice]:
    return [
        ScrapedPrice(
            provider_slug=artifact.provider_slug,
            gpu_slug_hint=r.gpu_slug_hint,
            tier=r.tier,
            region=r.region,
            hourly_usd=Decimal(r.hourly_usd),
            available=r.available,
            raw=r.raw,
        )
        for r in artifact.prices
    ]
