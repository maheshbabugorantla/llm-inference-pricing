from __future__ import annotations

from rest_framework import serializers

from catalog.models import GPU, Model, Quantization


class GPUSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    class Meta:
        model = GPU
        fields = (
            "slug",
            "vendor",
            "display_name",
            "vram_gb",
            "memory_bandwidth_gbs",
            "fp16_tflops",
            "fp8_tflops",
            "tdp_watts",
        )


class ModelSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    class Meta:
        model = Model
        fields = (
            "slug",
            "display_name",
            "family",
            "total_params_b",
            "active_params_b",
            "architecture",
            "max_context",
        )


class QuantizationSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    class Meta:
        model = Quantization
        fields = ("slug", "display_name", "weight_bits", "kv_cache_bits")
