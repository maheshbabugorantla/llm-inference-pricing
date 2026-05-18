from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from catalog.models import GPU, Model, Quantization


class QuantizationFactory(DjangoModelFactory):
    class Meta:
        model = Quantization

    slug = factory.Sequence(lambda n: f"fp16-{n}")
    display_name = "FP16"
    weight_bits = 16
    kv_cache_bits = 16


class GPUFactory(DjangoModelFactory):
    class Meta:
        model = GPU

    slug = factory.Sequence(lambda n: f"nvidia-test-gpu-{n}")
    display_name = "Test GPU"
    vendor = "nvidia"
    architecture = "hopper"
    vram_gb = 80
    memory_bandwidth_gbs = 3350
    fp16_tflops = 989
    tdp_watts = 700
    interconnect = "nvlink"


class ModelFactory(DjangoModelFactory):
    class Meta:
        model = Model

    slug = factory.Sequence(lambda n: f"test-model-{n}")
    display_name = "Test Model"
    family = "test"
    architecture = "dense"
    total_params_b = 32.5
    active_params_b = 32.5
    num_layers = 64
    num_attention_heads = 40
    num_kv_heads = 8
    head_dim = 128
    max_context = 32768
    hf_repo = "test/test"
    license = "apache-2.0"
    is_coding_specialist = True
    recommended_quant = factory.SubFactory(QuantizationFactory)
    recommended_tp = 1
