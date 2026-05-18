from __future__ import annotations

import pytest

from catalog.services.fit import (
    compute_fit,
    kv_cache_bytes_per_token,
    weight_bytes,
)


def _qwen_25_coder_32b():
    """32.5B params, 64 layers, 8 KV heads, head_dim=128, dense."""
    return dict(
        total_params_b=32.5,
        num_layers=64,
        num_kv_heads=8,
        head_dim=128,
        architecture="dense",
    )


def _llama_70b():
    return dict(
        total_params_b=70,
        num_layers=80,
        num_kv_heads=8,
        head_dim=128,
        architecture="dense",
    )


def _deepseek_v3_moe():
    """671B total, 37B active — weight memory uses TOTAL (all experts loaded)."""
    return dict(
        total_params_b=671,
        num_layers=61,
        num_kv_heads=128,
        head_dim=128,
        architecture="moe",
    )


def test_weight_bytes_qwen32b_fp16_is_65gb():
    """32.5B params x 2 bytes = 65 GB."""
    assert weight_bytes(total_params_b=32.5, weight_bits=16) == 65 * 10**9


def test_weight_bytes_qwen32b_fp8_is_32_5gb():
    assert weight_bytes(total_params_b=32.5, weight_bits=8) == int(32.5 * 10**9)


def test_weight_bytes_moe_uses_total_not_active():
    """DeepSeek-V3 weight memory uses 671B (total), not 37B (active)."""
    fp16 = weight_bytes(total_params_b=671, weight_bits=16)
    assert fp16 == 1342 * 10**9


def test_kv_cache_bytes_qwen32b_fp16():
    """2 x 64 layers x 8 kv_heads x 128 head_dim x 2 bytes = 262144 bytes/token."""
    kv = kv_cache_bytes_per_token(num_layers=64, num_kv_heads=8, head_dim=128, kv_cache_bits=16)
    assert kv == 262144


def test_kv_cache_bytes_scales_with_kv_quant():
    fp16 = kv_cache_bytes_per_token(64, 8, 128, kv_cache_bits=16)
    fp8 = kv_cache_bytes_per_token(64, 8, 128, kv_cache_bits=8)
    int4 = kv_cache_bytes_per_token(64, 8, 128, kv_cache_bits=4)
    assert fp8 == fp16 // 2
    assert int4 == fp16 // 4


def test_qwen32b_fp8_fits_on_single_h100_at_batch_8_ctx_32k():
    """Worked example from PRD Appendix A: fits."""
    fits, per_gpu, _ = compute_fit(
        **_qwen_25_coder_32b(),
        weight_bits=8,
        kv_cache_bits=8,
        tp_size=1,
        batch_size=8,
        context_length=32768,
        gpu_vram_gb=80,
    )
    assert fits is True
    assert per_gpu < 80 * 10**9


def test_qwen32b_fp8_does_not_fit_on_single_h100_at_batch_32_ctx_32k():
    """Worked example from PRD Appendix A: 137GB KV cache alone exceeds 80GB VRAM."""
    fits, per_gpu, _breakdown = compute_fit(
        **_qwen_25_coder_32b(),
        weight_bits=8,
        kv_cache_bits=8,
        tp_size=1,
        batch_size=32,
        context_length=32768,
        gpu_vram_gb=80,
    )
    assert fits is False
    assert per_gpu > 80 * 10**9


def test_llama70b_fp16_requires_tp_at_least_2_on_h100():
    """70B x 2 bytes = 140 GB; single 80 GB H100 can't hold weights alone."""
    fits_tp1, _, _ = compute_fit(
        **_llama_70b(),
        weight_bits=16,
        kv_cache_bits=16,
        tp_size=1,
        batch_size=1,
        context_length=4096,
        gpu_vram_gb=80,
    )
    fits_tp2, _, _ = compute_fit(
        **_llama_70b(),
        weight_bits=16,
        kv_cache_bits=16,
        tp_size=2,
        batch_size=1,
        context_length=4096,
        gpu_vram_gb=80,
    )
    assert fits_tp1 is False
    assert fits_tp2 is True


def test_deepseek_v3_moe_uses_total_params_for_weight_memory():
    """If we incorrectly used active_params (37B), it would fit too easily.
    With correct total (671B), 8x 80GB H100s can't hold even the weights."""
    fits_tp8, _, _ = compute_fit(
        **_deepseek_v3_moe(),
        weight_bits=8,
        kv_cache_bits=8,
        tp_size=8,
        batch_size=1,
        context_length=4096,
        gpu_vram_gb=80,
    )
    fits_tp8_active_bug, _, _ = compute_fit(
        total_params_b=37,  # WRONG: using active param count
        num_layers=61,
        num_kv_heads=128,
        head_dim=128,
        architecture="moe",
        weight_bits=8,
        kv_cache_bits=8,
        tp_size=8,
        batch_size=1,
        context_length=4096,
        gpu_vram_gb=80,
    )
    assert fits_tp8 != fits_tp8_active_bug


@pytest.mark.parametrize("tp_size", [1, 2, 4, 8])
def test_compute_fit_returns_breakdown_dict(tp_size):
    _fits, _per_gpu, breakdown = compute_fit(
        **_qwen_25_coder_32b(),
        weight_bits=16,
        kv_cache_bits=16,
        tp_size=tp_size,
        batch_size=1,
        context_length=4096,
        gpu_vram_gb=80,
    )
    assert {"weight_bytes", "kv_cache_bytes", "activations_bytes", "per_gpu_bytes"} <= set(breakdown)
