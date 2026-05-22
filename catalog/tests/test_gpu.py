"""GPU business-scenario tests.

Design: go beyond slug uniqueness — prove that GPU rows are usable as the
FK anchor in PricingSnapshot, that hardware specs required for on-prem cost
math (tdp_watts, vram_gb) are non-nullable, and that vendor choices are
enforced so AMD and NVIDIA GPUs are never conflated.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider


@pytest.mark.django_db
def test_h100_sxm_80gb_slug_is_referenceable_from_pricing_snapshot():
    """A PricingSnapshot FK to an H100 GPU must be traversable back to the GPU
    slug — this is the join the cost-cell pipeline executes on every query."""
    provider = Provider.objects.create(
        slug="runpod",
        display_name="RunPod",
        provider_type="cloud",
        data_source_tier="realtime_api",
    )
    gpu = GPUFactory(slug="nvidia-h100-sxm-80", vram_gb=80)
    snap = PricingSnapshot.objects.create(
        provider=provider,
        gpu=gpu,
        tier="community",
        region="us-east-1",
        hourly_usd=Decimal("2.49"),
        available=True,
        scraped_at=timezone.now(),
        raw_payload={},
    )
    assert snap.gpu.slug == "nvidia-h100-sxm-80"
    assert snap.gpu.vram_gb == 80


@pytest.mark.django_db
def test_two_gpus_same_slug_raises_integrity_error():
    """GPU slugs are the join key across the entire pipeline. A duplicate slug
    would silently make two GPU rows compete for the same PricingSnapshot rows."""
    GPUFactory(slug="nvidia-h100-sxm-80")
    with pytest.raises(IntegrityError):
        GPUFactory(slug="nvidia-h100-sxm-80")


@pytest.mark.django_db
def test_gpu_vendor_must_be_nvidia_or_amd():
    """Vendor choices gate fit-checking logic and cost-cell grouping.
    An unexpected vendor string must be rejected so no GPU silently falls
    outside both NVIDIA and AMD processing paths."""
    gpu = GPUFactory(vendor="intel")
    with pytest.raises(ValidationError):
        gpu.full_clean()


@pytest.mark.django_db
def test_gpu_tdp_watts_is_required_for_on_prem_power_cost_math():
    """tdp_watts drives the on-prem power cost calculation. A NULL value would
    cause compute_on_prem_cost() to fail at runtime instead of at insert time."""
    with pytest.raises(IntegrityError):
        GPUFactory(tdp_watts=None)
