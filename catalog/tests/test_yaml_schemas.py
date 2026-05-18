from __future__ import annotations

import pytest
from pydantic import ValidationError

from catalog.services.seed import GPUYAML, ModelYAML, QuantizationYAML


def test_gpu_yaml_rejects_invalid_vendor():
    with pytest.raises(ValidationError):
        GPUYAML(
            slug="x",
            display_name="X",
            vendor="intel",
            architecture="x",
            vram_gb=80,
            memory_bandwidth_gbs=3000,
            fp16_tflops=900,
            tdp_watts=700,
            interconnect="pcie",
        )


def test_gpu_yaml_accepts_valid_payload():
    gpu = GPUYAML(
        slug="nvidia-h100-sxm-80",
        display_name="NVIDIA H100 SXM5 80GB",
        vendor="nvidia",
        architecture="hopper",
        vram_gb=80,
        memory_bandwidth_gbs=3350,
        fp16_tflops=989,
        tdp_watts=700,
        interconnect="nvlink",
        supports_fp8_native=True,
    )
    assert gpu.slug == "nvidia-h100-sxm-80"


def test_model_yaml_rejects_dense_with_mismatched_params():
    with pytest.raises(ValidationError):
        ModelYAML(
            slug="m",
            display_name="M",
            family="x",
            architecture="dense",
            total_params_b=32.5,
            active_params_b=10,
            num_layers=64,
            num_attention_heads=40,
            num_kv_heads=8,
            head_dim=128,
            max_context=32768,
            hf_repo="x/x",
            license="apache-2.0",
            is_coding_specialist=True,
            recommended_quant="fp16",
            recommended_tp=1,
        )


def test_quantization_yaml_rejects_invalid_bits():
    with pytest.raises(ValidationError):
        QuantizationYAML(slug="x", display_name="X", weight_bits=7, kv_cache_bits=16)
