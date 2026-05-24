from __future__ import annotations

import decimal

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.models import GPU, BenchmarkPoint, Model, Quantization
from catalog.services.fit import compute_fit
from pricing.tests.factories import PricingSnapshotFactory


class InvariantSmokeTest(TestCase):
    def test_invariant_i3_recommended_quant_always_exists(self) -> None:
        """I3: every Model row has a non-null FK to an existing Quantization."""
        call_command("seed_catalog")
        for model in Model.objects.all():
            self.assertIsNotNone(model.recommended_quant_id)
            Quantization.objects.get(pk=model.recommended_quant_id)

    def test_invariant_i8_seeds_idempotent(self) -> None:
        """I8: running seed_catalog twice produces identical counts."""
        call_command("seed_catalog")
        counts_a = {
            "gpus": GPU.objects.count(),
            "models": Model.objects.count(),
            "quants": Quantization.objects.count(),
        }
        call_command("seed_catalog")
        counts_b = {
            "gpus": GPU.objects.count(),
            "models": Model.objects.count(),
            "quants": Quantization.objects.count(),
        }
        self.assertEqual(counts_a, counts_b)

    def test_invariant_i2_every_benchmark_point_fits(self) -> None:
        """I2: every seeded BenchmarkPoint is physically feasible on its GPU."""
        call_command("seed_catalog")
        for bp in BenchmarkPoint.objects.select_related("model", "gpu", "quantization"):
            fits, _, _ = compute_fit(
                total_params_b=bp.model.total_params_b,
                num_layers=bp.model.num_layers,
                num_kv_heads=bp.model.num_kv_heads,
                head_dim=bp.model.head_dim,
                architecture=bp.model.architecture,
                weight_bits=bp.quantization.weight_bits,
                kv_cache_bits=bp.quantization.kv_cache_bits,
                tp_size=bp.tp_size,
                batch_size=bp.batch_size,
                context_length=bp.context_length,
                gpu_vram_gb=bp.gpu.vram_gb,
            )
            self.assertTrue(fits, f"benchmark {bp} fails fit check")

    def test_invariant_i1_hourly_usd_non_negative(self) -> None:
        """I1: hourly_usd must be >= 0 (DB enforces CheckConstraint)."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PricingSnapshotFactory(hourly_usd=decimal.Decimal("-0.0001"))
