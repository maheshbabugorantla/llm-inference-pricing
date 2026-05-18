from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from catalog.models import GPU
from pricing.models import PricingSnapshot, Provider

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime

    from pricing.scrapers.base import ScrapedPrice

logger = logging.getLogger("pricing.scrape_runner")


@transaction.atomic
def persist_prices(
    prices: Iterable[ScrapedPrice],
    *,
    gpu_slug_resolver: Callable[[str], str | None],
    scraped_at: datetime | None = None,
) -> int:
    """Persist a batch of scraped prices as PricingSnapshot rows.

    Returns the number of rows written. Unmapped GPUs are skipped (info log).
    Missing Provider raises Provider.DoesNotExist (configuration error).
    """
    now = scraped_at if scraped_at is not None else timezone.now()
    providers_cache: dict[str, Provider] = {}
    gpus_cache: dict[str, GPU] = {}
    written = 0

    for price in prices:
        if price.provider_slug not in providers_cache:
            providers_cache[price.provider_slug] = Provider.objects.get(slug=price.provider_slug)
        provider = providers_cache[price.provider_slug]

        gpu_slug = gpu_slug_resolver(price.gpu_slug_hint)
        if gpu_slug is None:
            logger.info("dropping price for unmapped gpu hint: %s", price.gpu_slug_hint)
            continue

        if gpu_slug not in gpus_cache:
            try:
                gpus_cache[gpu_slug] = GPU.objects.get(slug=gpu_slug)
            except GPU.DoesNotExist:
                logger.error("gpu mapping returned unknown slug: %s", gpu_slug)
                continue
        gpu = gpus_cache[gpu_slug]

        PricingSnapshot.objects.create(
            provider=provider,
            gpu=gpu,
            tier=price.tier,
            region=price.region,
            hourly_usd=price.hourly_usd,
            available=price.available,
            scraped_at=now,
            raw_payload=price.raw,
        )
        written += 1

    logger.info(
        "persisted %d snapshots for providers=%s",
        written,
        sorted(providers_cache.keys()),
    )
    return written
