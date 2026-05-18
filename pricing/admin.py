from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import admin

from pricing.models import PricingSnapshot, Provider

if TYPE_CHECKING:
    from django.http import HttpRequest


class _ReadOnlyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Provider)
class ProviderAdmin(_ReadOnlyAdmin):
    list_display = ("slug", "display_name", "provider_type", "data_source_tier", "is_active")
    search_fields = ("slug", "display_name")
    list_filter = ("provider_type", "data_source_tier", "is_active")


@admin.register(PricingSnapshot)
class PricingSnapshotAdmin(_ReadOnlyAdmin):
    list_display = ("scraped_at", "provider", "gpu", "tier", "hourly_usd", "available")
    list_filter = ("provider", "gpu", "tier", "available")
    search_fields = ("provider__slug", "gpu__slug")
