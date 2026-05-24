"""GPU business-scenario tests.

Design: go beyond slug uniqueness — prove that GPU rows are usable as the
FK anchor in PricingSnapshot, that hardware specs required for on-prem cost
math (tdp_watts, vram_gb) are non-nullable, and that vendor choices are
enforced so AMD and NVIDIA GPUs are never conflated.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from catalog.models import GPU
from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider


class GPUTest(TestCase):
    def test_h100_sxm_80gb_slug_is_referenceable_from_pricing_snapshot(self):
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
        self.assertEqual(snap.gpu.slug, "nvidia-h100-sxm-80")
        self.assertEqual(snap.gpu.vram_gb, 80)

    def test_two_gpus_same_slug_raises_integrity_error(self):
        """GPU slugs are the join key across the entire pipeline. A duplicate slug
        would silently make two GPU rows compete for the same PricingSnapshot rows."""
        GPUFactory(slug="nvidia-h100-sxm-80")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GPUFactory(slug="nvidia-h100-sxm-80")

    def test_gpu_vendor_must_be_nvidia_or_amd(self):
        """Vendor choices gate fit-checking logic and cost-cell grouping.
        An unexpected vendor string must be rejected so no GPU silently falls
        outside both NVIDIA and AMD processing paths."""
        gpu = GPUFactory(vendor="intel")
        with self.assertRaises(ValidationError):
            gpu.full_clean()

    def test_gpu_tdp_watts_is_required_for_on_prem_power_cost_math(self):
        """tdp_watts drives the on-prem power cost calculation. A NULL value would
        cause compute_on_prem_cost() to fail at runtime instead of at insert time."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GPUFactory(tdp_watts=None)

    def test_qwen32b_fp16_fits_a100_80gb_but_not_a100_40gb(self):
        """fp16 Qwen-32B needs ~76.5 GB VRAM at tp=1, batch=8, ctx=4k — which fits
        an A100-80GB but not an A100-40GB. A wrong vram_gb value on the GPU row
        would generate cost cells for runs that cannot physically execute."""
        from catalog.services.fit import compute_fit

        shared_kwargs = dict(
            total_params_b=32.5,
            num_layers=64,
            num_kv_heads=8,
            head_dim=128,
            architecture="dense",
            weight_bits=16,
            kv_cache_bits=16,
            tp_size=1,
            batch_size=8,
            context_length=4096,
        )
        fits_80, _, _ = compute_fit(**shared_kwargs, gpu_vram_gb=80)
        fits_40, _, _ = compute_fit(**shared_kwargs, gpu_vram_gb=40)

        gpu_80 = GPUFactory(slug="nvidia-a100-sxm-80", vram_gb=80)
        gpu_40 = GPUFactory(slug="nvidia-a100-sxm-40", vram_gb=40)

        self.assertTrue(fits_80, "Qwen-32B fp16 should fit the 80 GB A100")
        self.assertFalse(fits_40, "Qwen-32B fp16 must not fit the 40 GB A100")
        self.assertEqual(gpu_80.vram_gb, 80)
        self.assertEqual(gpu_40.vram_gb, 40)

    def test_inactive_gpu_historical_snapshots_remain_queryable(self):
        """When a GPU is retired (is_active=False) its historical PricingSnapshot rows
        must remain queryable — operators need the audit trail for cost trend analysis
        even after a GPU is sunset from active pricing."""
        provider = Provider.objects.create(
            slug="runpod",
            display_name="RunPod",
            provider_type="cloud",
            data_source_tier="realtime_api",
        )
        gpu = GPUFactory(slug="nvidia-v100-16gb", is_active=True)
        snap = PricingSnapshot.objects.create(
            provider=provider,
            gpu=gpu,
            tier="on_demand",
            region="us-east-1",
            hourly_usd=Decimal("0.89"),
            available=True,
            scraped_at=timezone.now(),
            raw_payload={},
        )
        gpu.is_active = False
        gpu.save()

        self.assertTrue(PricingSnapshot.objects.filter(pk=snap.pk).exists())
        self.assertFalse(PricingSnapshot.objects.get(pk=snap.pk).gpu.is_active)

    def test_gpu_meta_ordering_is_vendor_then_slug_for_deterministic_pipeline_queries(self):
        """GPU.Meta.ordering = ('vendor', 'slug') ensures the benchmark grid always
        processes GPUs in stable order — without it, queryset iteration order is
        non-deterministic across Postgres restarts and can cause test flakiness."""
        GPUFactory(slug="nvidia-h100-sxm-80", vendor="nvidia")
        GPUFactory(slug="amd-mi300x", vendor="amd")
        GPUFactory(slug="nvidia-a100-sxm-80", vendor="nvidia")

        slugs = list(GPU.objects.values_list("slug", flat=True))
        amd_idx = slugs.index("amd-mi300x")
        nvidia_h100_idx = slugs.index("nvidia-h100-sxm-80")
        nvidia_a100_idx = slugs.index("nvidia-a100-sxm-80")

        self.assertLess(amd_idx, nvidia_h100_idx, "AMD must sort before NVIDIA (vendor='amd' < 'nvidia')")
        self.assertLess(nvidia_a100_idx, nvidia_h100_idx, "a100 must sort before h100 within nvidia")
