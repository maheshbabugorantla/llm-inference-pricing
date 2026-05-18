from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ScrapedPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_slug: str
    gpu_slug_hint: str
    tier: str
    region: str
    hourly_usd: Annotated[Decimal, Field(strict=True)]
    available: bool
    raw: dict  # type: ignore[type-arg]
