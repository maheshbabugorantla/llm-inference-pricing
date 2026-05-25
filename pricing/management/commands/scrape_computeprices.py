"""Scrape on-demand GPU prices from ComputePrices.com and persist to DB.

ComputePrices is an aggregator: each API call for a GPU returns prices from
multiple cloud providers.  This command iterates every GPU in GPU_SLUG_MAP,
fetches the on-demand prices, and creates PricingSnapshot rows attributed to
the respective providers (runpod, aws, lambda, etc.).

Only provider slugs and GPU slugs already present in the catalog are written;
everything else is skipped with an info-level log.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalog.models import GPU
from pricing.models import PricingSnapshot, Provider
from pricing.scrapers.computeprices import GPU_SLUG_MAP, fetch_computeprices_gpu_prices

logger = logging.getLogger("pricing.management.scrape_computeprices")


class Command(BaseCommand):
    help = "Fetch on-demand GPU prices from ComputePrices.com and persist to the DB"

    def handle(self, *args: object, **options: object) -> None:
        scraped_at = timezone.now()

        gpus: dict[str, GPU] = {g.slug: g for g in GPU.objects.filter(slug__in=GPU_SLUG_MAP)}
        providers: dict[str, Provider] = {p.slug: p for p in Provider.objects.all()}

        snapshots: list[PricingSnapshot] = []
        errors = 0
        skipped_gpu = 0
        skipped_provider = 0

        for our_slug in sorted(GPU_SLUG_MAP):
            if our_slug not in gpus:
                logger.info("GPU %r not in catalog — skipping", our_slug)
                skipped_gpu += 1
                continue

            gpu = gpus[our_slug]

            try:
                items = fetch_computeprices_gpu_prices(our_slug)
            except Exception as exc:
                logger.error("computeprices fetch failed for %r: %s", our_slug, exc)
                errors += 1
                continue

            for item in items:
                provider_slug = item["provider"]
                if provider_slug not in providers:
                    logger.info("provider %r not in catalog (gpu=%s) — skipping", provider_slug, our_slug)
                    skipped_provider += 1
                    continue

                snapshots.append(
                    PricingSnapshot(
                        provider=providers[provider_slug],
                        gpu=gpu,
                        tier="on_demand",
                        region="",
                        hourly_usd=Decimal(item["hourly_usd"]),
                        available=True,
                        scraped_at=scraped_at,
                        raw_payload={},
                    )
                )

        with transaction.atomic():
            PricingSnapshot.objects.bulk_create(snapshots)

        self.stdout.write(
            self.style.SUCCESS(
                f"computeprices: {len(snapshots)} snapshots created "
                f"({skipped_gpu} GPUs not in catalog, "
                f"{skipped_provider} providers not in catalog, "
                f"{errors} fetch errors)"
            )
        )
