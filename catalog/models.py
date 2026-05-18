from __future__ import annotations

from typing import ClassVar

from django.core.exceptions import ValidationError
from django.db import models

VALID_QUANT_BITS: frozenset[float] = frozenset({4, 8, 16})
VALID_TP_SIZES: frozenset[int] = frozenset({1, 2, 4, 8})


class Quantization(models.Model):
    slug = models.SlugField(unique=True, max_length=64)
    display_name = models.CharField(max_length=64)
    weight_bits = models.FloatField()
    kv_cache_bits = models.FloatField()
    requires_nvidia_arch = models.JSONField(default=list, blank=True)
    requires_amd_arch = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("weight_bits",)

    def __str__(self) -> str:
        return self.display_name

    def clean(self) -> None:
        if self.weight_bits not in VALID_QUANT_BITS:
            raise ValidationError({"weight_bits": f"must be one of {VALID_QUANT_BITS}"})
        if self.kv_cache_bits not in VALID_QUANT_BITS:
            raise ValidationError({"kv_cache_bits": f"must be one of {VALID_QUANT_BITS}"})


class GPU(models.Model):
    VENDOR_CHOICES: ClassVar[list[tuple[str, str]]] = [("nvidia", "NVIDIA"), ("amd", "AMD")]

    slug = models.SlugField(unique=True, max_length=64)
    display_name = models.CharField(max_length=128)
    vendor = models.CharField(max_length=16, choices=VENDOR_CHOICES)
    architecture = models.CharField(max_length=32)
    vram_gb = models.PositiveIntegerField()
    memory_bandwidth_gbs = models.PositiveIntegerField()
    fp16_tflops = models.FloatField()
    fp8_tflops = models.FloatField(null=True, blank=True)
    int8_tops = models.FloatField(null=True, blank=True)
    tdp_watts = models.PositiveIntegerField()
    interconnect = models.CharField(max_length=32)
    nvlink_bandwidth_gbs = models.PositiveIntegerField(null=True, blank=True)
    supports_fp8_native = models.BooleanField(default=False)
    supports_fp4_native = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("vendor", "slug")

    def __str__(self) -> str:
        return self.display_name


class Model(models.Model):
    """Placeholder; expanded with full fields in M01.T04."""

    class Meta:
        ordering = ("pk",)

    def __str__(self) -> str:
        return str(self.pk)
