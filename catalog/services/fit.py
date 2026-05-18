from __future__ import annotations

from typing import TypedDict


class FitBreakdown(TypedDict):
    weight_bytes: int
    kv_cache_bytes: int
    activations_bytes: int
    per_gpu_bytes: int


_OVERHEAD = 1.15


def weight_bytes(total_params_b: float, weight_bits: float) -> int:
    """Total weight memory across all GPUs, before TP sharding.

    For MoE, total_params_b is the FULL count — all experts must be loaded.
    Using active_params_b here is a common bug that underestimates memory.
    """
    return int(total_params_b * 1e9 * (weight_bits / 8))


def kv_cache_bytes_per_token(num_layers: int, num_kv_heads: int, head_dim: int, kv_cache_bits: float) -> int:
    """Per-token KV cache: 2 (K+V) x layers x kv_heads x head_dim x bytes/element."""
    return int(2 * num_layers * num_kv_heads * head_dim * (kv_cache_bits / 8))


def _activations_bytes(batch_size: int) -> int:
    return int((1.0 + 0.05 * batch_size) * 1e9)


def per_gpu_total_bytes(
    *,
    total_params_b: float,
    num_layers: int,
    num_kv_heads: int,
    head_dim: int,
    weight_bits: float,
    kv_cache_bits: float,
    tp_size: int,
    batch_size: int,
    context_length: int,
) -> tuple[int, FitBreakdown]:
    """Per-GPU memory after TP sharding. Returns (bytes, breakdown)."""
    wb = weight_bytes(total_params_b, weight_bits)
    kv_per_token = kv_cache_bytes_per_token(num_layers, num_kv_heads, head_dim, kv_cache_bits)
    kv_total = kv_per_token * batch_size * context_length
    acts = _activations_bytes(batch_size)

    # Weights are a known fixed size; overhead covers dynamic memory fragmentation.
    # KV cache and activations shard with TP; overhead applies to that dynamic portion only.
    per_gpu = int(wb / tp_size + (kv_total / tp_size + acts) * _OVERHEAD)

    return per_gpu, {
        "weight_bytes": wb,
        "kv_cache_bytes": kv_total,
        "activations_bytes": acts,
        "per_gpu_bytes": per_gpu,
    }


def compute_fit(
    *,
    total_params_b: float,
    num_layers: int,
    num_kv_heads: int,
    head_dim: int,
    architecture: str,
    weight_bits: float,
    kv_cache_bits: float,
    tp_size: int,
    batch_size: int,
    context_length: int,
    gpu_vram_gb: int,
) -> tuple[bool, int, FitBreakdown]:
    """Returns (fits, per_gpu_bytes, breakdown)."""
    per_gpu, breakdown = per_gpu_total_bytes(
        total_params_b=total_params_b,
        num_layers=num_layers,
        num_kv_heads=num_kv_heads,
        head_dim=head_dim,
        weight_bits=weight_bits,
        kv_cache_bits=kv_cache_bits,
        tp_size=tp_size,
        batch_size=batch_size,
        context_length=context_length,
    )
    fits = per_gpu <= gpu_vram_gb * 10**9
    return fits, per_gpu, breakdown
