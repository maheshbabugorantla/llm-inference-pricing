from __future__ import annotations

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
    """Placeholder; expanded with full fields in M01.T03."""

    class Meta:
        ordering = ("pk",)

    def __str__(self) -> str:
        return str(self.pk)


class Model(models.Model):
    """Placeholder; expanded with full fields in M01.T04."""

    class Meta:
        ordering = ("pk",)

    def __str__(self) -> str:
        return str(self.pk)
