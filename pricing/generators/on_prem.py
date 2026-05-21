from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from pricing.models import OnPremDeployment, PricingSnapshot, Provider
from pricing.services.on_prem_cost import compute_on_prem_cost


@transaction.atomic
def regenerate_on_prem_snapshots() -> int:
    """Emit two PricingSnapshot rows (tco, marginal) for each active OnPremDeployment."""
    now = timezone.now()
    written = 0

    for d in OnPremDeployment.objects.select_related("hardware_sku__gpu").filter(is_active=True):
        provider, _ = Provider.objects.update_or_create(
            slug=f"on-prem-{d.slug}",
            defaults={
                "display_name": d.display_name,
                "provider_type": "on_prem",
                "data_source_tier": "synthetic",
            },
        )
        breakdown = compute_on_prem_cost(d)
        for tier_name, per_gpu in [
            ("tco", breakdown["per_gpu_hourly_tco"]),
            ("marginal", breakdown["per_gpu_hourly_marginal"]),
        ]:
            PricingSnapshot.objects.create(
                provider=provider,
                gpu=d.hardware_sku.gpu,
                tier=tier_name,
                region="",
                hourly_usd=per_gpu,
                available=True,
                scraped_at=now,
                raw_payload={
                    "deployment_slug": d.slug,
                    "breakdown": {k: str(v) for k, v in breakdown.items()},
                },
            )
            written += 1

    return written
