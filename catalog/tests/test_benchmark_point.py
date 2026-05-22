"""BenchmarkPoint business-scenario tests.

Design: prove that the BenchmarkPoint model correctly captures real inference
benchmarks, enforces the uniqueness invariant (I6), and that FK traversal to
GPU VRAM works end-to-end so the cost-cell pipeline can read hardware specs.
"""

from __future__ import annotations

import pytest
from django.db.utils import IntegrityError
from pydantic import ValidationError as PydanticValidationError

from catalog.services.fit import compute_fit
from catalog.services.seed import BenchmarkPointYAML
from catalog.tests.factories import BenchmarkPointFactory, GPUFactory, ModelFactory, QuantizationFactory


@pytest.mark.django_db
def test_qwen32b_fp8_tp1_on_h100_batch8_fits_and_benchmark_recorded():
    """Qwen2.5-Coder-32B at fp8, tp=1 fits on an 80 GB H100 at batch=8/ctx=4k
    and a BenchmarkPoint recording that run is queryable by its compat-tuple.

    PRD §4.1 reference case — this is the lowest-cost production-realistic setup.
    """
    gpu = GPUFactory(slug="nvidia-h100-sxm-80", vram_gb=80)
    quant = QuantizationFactory(slug="fp8-e4m3", weight_bits=8, kv_cache_bits=8)
    model = ModelFactory(
        slug="qwen-2-5-coder-32b",
        architecture="dense",
        total_params_b=32.5,
        active_params_b=32.5,
        num_layers=64,
        num_kv_heads=8,
        head_dim=128,
        recommended_quant=quant,
        recommended_tp=1,
    )

    fits, _, _ = compute_fit(
        total_params_b=model.total_params_b,
        num_layers=model.num_layers,
        num_kv_heads=model.num_kv_heads,
        head_dim=model.head_dim,
        architecture=model.architecture,
        weight_bits=quant.weight_bits,
        kv_cache_bits=quant.kv_cache_bits,
        tp_size=1,
        batch_size=8,
        context_length=4096,
        gpu_vram_gb=gpu.vram_gb,
    )
    assert fits, "Qwen-32B fp8 tp=1 should fit on H100 80GB at batch=8/ctx=4k"

    point = BenchmarkPointFactory(
        model=model,
        gpu=gpu,
        quantization=quant,
        tp_size=1,
        batch_size=8,
        context_length=4096,
        decode_tps_aggregate=920.0,
    )

    fetched = type(point).objects.get(
        model=model, gpu=gpu, quantization=quant, tp_size=1, batch_size=8, context_length=4096
    )
    assert fetched.decode_tps_aggregate == 920.0


@pytest.mark.django_db
def test_benchmark_point_unique_together_blocks_duplicate_operating_point():
    """Invariant I6: two BenchmarkPoint rows cannot share the same
    (model, gpu, quantization, tp_size, batch_size, context_length) tuple."""
    bp = BenchmarkPointFactory()
    with pytest.raises(IntegrityError):
        BenchmarkPointFactory(
            model=bp.model,
            gpu=bp.gpu,
            quantization=bp.quantization,
            tp_size=bp.tp_size,
            batch_size=bp.batch_size,
            context_length=bp.context_length,
        )


@pytest.mark.django_db
def test_benchmark_point_links_to_correct_gpu_vram():
    """The FK chain BenchmarkPoint → GPU exposes vram_gb so the cost pipeline
    can read hardware specs without a separate GPU lookup."""
    gpu = GPUFactory(vram_gb=80)
    point = BenchmarkPointFactory(gpu=gpu)
    assert point.gpu.vram_gb == 80


def test_aggregate_decode_tps_must_be_positive():
    """Zero or negative decode TPS is rejected at the YAML ingestion boundary,
    preventing benchmark records with physically impossible throughput from
    entering the DB and corrupting cost-cell calculations."""
    with pytest.raises(PydanticValidationError, match="throughput must be positive"):
        BenchmarkPointYAML(
            model="qwen-2-5-coder-32b",
            gpu="nvidia-h100-sxm-80",
            quantization="fp8-e4m3",
            tp_size=1,
            batch_size=8,
            context_length=4096,
            prefill_tps_aggregate=1000.0,
            decode_tps_aggregate=0.0,
        )
