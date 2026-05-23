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
    Stale snapshots for active deployments are retired (available=False) before new
    rows are created, preventing duplicate available rows on repeated calls.
    """
    now = timezone.now()
    written = 0

    active_qs = ReservedCloudDeployment.objects.filter(is_active=True, product__is_active=True)
    active_slugs = list(active_qs.values_list("slug", flat=True))
    stale_tiers = [f"reserved-{s}" for s in active_slugs] + [f"reserved-marginal-{s}" for s in active_slugs]
    if stale_tiers:
        PricingSnapshot.objects.filter(tier__in=stale_tiers).update(available=False)

    for d in active_qs.select_related(
        "product__gpu",
        "product__cloud_provider",
    ):
        breakdown = compute_reserved_cloud_cost(d)
        for tier_suffix, per_gpu in [
            ("reserved", breakdown["per_gpu_hourly_committed"]),
            ("reserved-marginal", breakdown["per_gpu_hourly_reservation_marginal"]),
        ]:
            PricingSnapshot.objects.create(
                provider=d.product.cloud_provider,
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
