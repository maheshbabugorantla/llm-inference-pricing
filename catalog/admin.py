from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import admin

from catalog.models import GPU, Model, Quantization

if TYPE_CHECKING:
    from django.http import HttpRequest


class _ReadOnlyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(GPU)
class GPUAdmin(_ReadOnlyAdmin):
    list_display = ("slug", "display_name", "vendor", "vram_gb", "is_active")
    search_fields = ("slug", "display_name")
    list_filter = ("vendor", "architecture")


@admin.register(Model)
class ModelAdmin(_ReadOnlyAdmin):
    list_display = ("slug", "display_name", "family", "architecture", "total_params_b")
    search_fields = ("slug", "display_name")
    list_filter = ("family", "architecture", "is_coding_specialist")


@admin.register(Quantization)
class QuantizationAdmin(_ReadOnlyAdmin):
    list_display = ("slug", "display_name", "weight_bits", "kv_cache_bits")
