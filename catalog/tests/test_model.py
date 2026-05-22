"""Model business-scenario tests.

Design: the Model entity drives two critical pipeline decisions — (1) whether
a model fits on a GPU (via compute_fit, which uses total_params_b for MoE),
and (2) which quantization and TP size are production-realistic (recommended_quant,
recommended_tp). Tests here prove those decisions are made correctly.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from catalog.services.fit import compute_fit
from catalog.tests.factories import ModelFactory, QuantizationFactory


@pytest.mark.django_db
def test_moe_model_requires_all_experts_loaded_not_just_active_params():
    """For MoE models ALL expert weights must be resident in VRAM, not just the
    active ones per forward pass. A GPU that could fit a dense 37B model cannot
    fit DeepSeek-V3 (671B total) even though only 37B params are active at once.

    This prevents the 'active params underestimation' bug documented in fit.py.
    """
    # A GPU large enough for 37B active params but not 671B total params
    fits_active, _, _ = compute_fit(
        total_params_b=37.0,  # active only — wrong for MoE
        num_layers=61,
        num_kv_heads=128,
        head_dim=128,
        architecture="dense",
        weight_bits=8,
        kv_cache_bits=8,
        tp_size=1,
        batch_size=1,
        context_length=4096,
        gpu_vram_gb=80,
    )

    fits_total, _, _ = compute_fit(
        total_params_b=671.0,  # all experts — correct for MoE
        num_layers=61,
        num_kv_heads=128,
        head_dim=128,
        architecture="moe",
        weight_bits=8,
        kv_cache_bits=8,
        tp_size=1,
        batch_size=1,
        context_length=4096,
        gpu_vram_gb=80,
    )

    assert fits_active, "37B active params should fit on 80GB GPU"
    assert not fits_total, "671B total MoE params must not fit on a single 80GB GPU"


@pytest.mark.django_db
def test_model_context_length_drives_kv_cache_fit_boundary():
    """KV cache grows linearly with context length. A model that fits at 4k
    context may not fit at 128k context on the same GPU — this boundary drives
    which operating points appear in the benchmark grid."""
    fits_4k, _, _ = compute_fit(
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
        gpu_vram_gb=80,
    )

    fits_128k, _, _ = compute_fit(
        total_params_b=32.5,
        num_layers=64,
        num_kv_heads=8,
        head_dim=128,
        architecture="dense",
        weight_bits=16,
        kv_cache_bits=16,
        tp_size=1,
        batch_size=8,
        context_length=131072,
        gpu_vram_gb=80,
    )

    assert fits_4k, "Qwen-32B fp16 tp=1 should fit on H100 80GB at ctx=4k"
    assert not fits_128k, "Qwen-32B fp16 tp=1 should NOT fit on H100 80GB at ctx=128k batch=8"


@pytest.mark.django_db
def test_recommended_quant_fk_traversal_provides_weight_bits_for_fit():
    """The recommended_quant FK must be traversable to weight_bits so the
    benchmark loader can call compute_fit without a separate Quantization lookup."""
    quant = QuantizationFactory(slug="fp8-e4m3", weight_bits=8, kv_cache_bits=8)
    model = ModelFactory(recommended_quant=quant)
    assert model.recommended_quant.slug == "fp8-e4m3"
    assert model.recommended_quant.weight_bits == 8


@pytest.mark.django_db
def test_model_slug_must_be_unique():
    """Model slugs are the join key between YAML benchmark files and DB rows.
    A duplicate slug would cause benchmark points to be assigned to the wrong model."""
    ModelFactory(slug="deepseek-coder-v3")
    with pytest.raises(IntegrityError):
        ModelFactory(slug="deepseek-coder-v3")


@pytest.mark.django_db
def test_dense_model_rejects_mismatched_active_and_total_params():
    """For dense models total_params_b == active_params_b is a hard invariant.
    A mismatch would cause fit calculations to use wrong memory estimates."""
    bad = ModelFactory(architecture="dense", total_params_b=32.5, active_params_b=10.0)
    with pytest.raises(ValidationError):
        bad.full_clean()


@pytest.mark.django_db
def test_recommended_tp_must_be_power_of_two_from_valid_set():
    """tp_size ∈ {1, 2, 4, 8} matches available NVLink / PCIe topologies.
    An unsupported TP size would generate benchmark entries that can't run."""
    for tp in (1, 2, 4, 8):
        ModelFactory(recommended_tp=tp).full_clean()
    bad = ModelFactory(recommended_tp=3)
    with pytest.raises(ValidationError):
        bad.full_clean()
