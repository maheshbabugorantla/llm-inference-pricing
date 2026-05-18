from __future__ import annotations

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from catalog.models import GPU, BenchmarkPoint, Model, Quantization
from catalog.services.fit import compute_fit
from pricing.tests.factories import PricingSnapshotFactory


@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i3_recommended_quant_always_exists():
    """I3: every Model row has a non-null FK to an existing Quantization."""
    call_command("seed_catalog")
    for model in Model.objects.all():
        assert model.recommended_quant_id is not None
        Quantization.objects.get(pk=model.recommended_quant_id)


@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i8_seeds_idempotent():
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
    assert counts_a == counts_b


@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i2_every_benchmark_point_fits():
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
        assert fits, f"benchmark {bp} fails fit check"


@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i1_hourly_usd_non_negative() -> None:
    """I1: hourly_usd must be >= 0 (DB enforces CheckConstraint)."""
    import decimal

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PricingSnapshotFactory(hourly_usd=decimal.Decimal("-0.0001"))
