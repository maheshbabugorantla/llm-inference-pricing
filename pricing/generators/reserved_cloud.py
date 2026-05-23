from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from pricing.models import PricingSnapshot, ReservedCloudDeployment
from pricing.services.reserved_cloud_cost import compute_reserved_cloud_cost


@transaction.atomic
def regenerate_reserved_cloud_snapshots() -> int:
    """For each active ReservedCloudDeployment, emit two PricingSnapshot rows.

    Tiers: 'reserved-{slug}' (committed rate) and 'reserved-marginal-{slug}'.
    The existing cloud Provider is reused per ADR-010 — no synthetic provider created.
    """
    now = timezone.now()
    written = 0

    for d in ReservedCloudDeployment.objects.select_related(
        "product__gpu",
        "cloud_provider",
    ).filter(is_active=True):
        breakdown = compute_reserved_cloud_cost(d)
        for tier_suffix, per_gpu in [
            ("reserved", breakdown["per_gpu_hourly_committed"]),
            ("reserved-marginal", breakdown["per_gpu_hourly_reservation_marginal"]),
        ]:
            PricingSnapshot.objects.create(
                provider=d.cloud_provider,
                gpu=d.product.gpu,
                tier=f"{tier_suffix}-{d.slug}",
                region=d.region,
                hourly_usd=per_gpu,
                available=True,
                scraped_at=now,
                raw_payload={
                    "deployment_slug": d.slug,
                    "product_slug": d.product.slug,
                    "breakdown": {k: str(v) for k, v in breakdown.items()},
                },
            )
            written += 1

    return written
