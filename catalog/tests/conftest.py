from __future__ import annotations

import pytest

MINIMAL_GPUS_YAML = """\
- slug: nvidia-h100-sxm-80
  display_name: NVIDIA H100 SXM5 80GB
  vendor: nvidia
  architecture: hopper
  vram_gb: 80
  memory_bandwidth_gbs: 3350
  fp16_tflops: 989.0
  fp8_tflops: 1979
  tdp_watts: 700
  interconnect: nvlink
  nvlink_bandwidth_gbs: 900
  supports_fp8_native: true
"""

MINIMAL_QUANTS_YAML = """\
- slug: fp16
  display_name: FP16
  weight_bits: 16
  kv_cache_bits: 16
- slug: fp8-e4m3
  display_name: FP8 (E4M3)
  weight_bits: 8
  kv_cache_bits: 8
  requires_nvidia_arch: [hopper, ada, blackwell]
"""

MINIMAL_MODELS_YAML = """\
- slug: qwen-2-5-coder-32b
  display_name: Qwen2.5-Coder-32B-Instruct
  family: qwen
  architecture: dense
  total_params_b: 32.5
  active_params_b: 32.5
  num_layers: 64
  num_attention_heads: 40
  num_kv_heads: 8
  head_dim: 128
  max_context: 131072
  hf_repo: Qwen/Qwen2.5-Coder-32B-Instruct
  license: apache-2.0
  is_coding_specialist: true
  recommended_quant: fp8-e4m3
  recommended_tp: 1
"""


@pytest.fixture
def tmp_seeds_dir(tmp_path):
    (tmp_path / "gpus.yaml").write_text(MINIMAL_GPUS_YAML)
    (tmp_path / "quantizations.yaml").write_text(MINIMAL_QUANTS_YAML)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "qwen.yaml").write_text(MINIMAL_MODELS_YAML)
    return tmp_path
