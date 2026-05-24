from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.utils import timezone

from pricing.models import (
    HardwareSKU,
    OnPremDeployment,
    PricingDriftAlert,
    PricingSnapshot,
    Provider,
    ReservedCapacityProduct,
    ReservedCloudDeployment,
)

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


@admin.register(HardwareSKU)
class HardwareSKUAdmin(_ReadOnlyAdmin):
    list_display = (
        "slug",
        "vendor",
        "display_name",
        "gpu",
        "num_gpus",
        "host_tdp_watts",
        "reference_msrp_usd",
    )
    search_fields = ("slug", "vendor", "display_name")
    list_filter = ("vendor", "gpu")


class _StaleListingFilter(admin.SimpleListFilter):
    """Highlights ReservedCapacityProduct rows whose listing_observed_at is > 90 days ago."""

    title = "listing staleness"
    parameter_name = "stale"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [("stale", "Stale (> 90 days)"), ("fresh", "Fresh (≤ 90 days)")]

    def queryset(self, request: HttpRequest, queryset: Any) -> Any:
        threshold = (timezone.now() - datetime.timedelta(days=90)).date()
        if self.value() == "stale":
            return queryset.filter(listing_observed_at__lt=threshold)
        if self.value() == "fresh":
            return queryset.filter(listing_observed_at__gte=threshold)
        return queryset


@admin.register(ReservedCapacityProduct)
class ReservedCapacityProductAdmin(_ReadOnlyAdmin):
    list_display = (
        "slug",
        "display_name",
        "cloud_provider",
        "payment_cadence",
        "term_months",
        "listing_observed_at",
        "is_active",
    )
    search_fields = ("slug", "display_name")
    list_filter = ("cloud_provider", "payment_cadence", "is_active", _StaleListingFilter)


@admin.register(ReservedCloudDeployment)
class ReservedCloudDeploymentAdmin(_ReadOnlyAdmin):
    list_display = (
        "slug",
        "display_name",
        "product",
        "cloud_provider",
        "expected_utilization_pct",
        "is_active",
    )
    search_fields = ("slug", "display_name")
    list_filter = ("cloud_provider", "is_active")


@admin.register(OnPremDeployment)
class OnPremDeploymentAdmin(_ReadOnlyAdmin):
    list_display = (
        "slug",
        "display_name",
        "hardware_sku",
        "capex_per_node_usd",
        "expected_utilization_pct",
        "power_usd_per_kwh",
        "is_active",
    )
    search_fields = ("slug", "display_name")
    list_filter = ("hardware_sku", "is_active")


class _UnacknowledgedFilter(admin.SimpleListFilter):
    title = "acknowledgement"
    parameter_name = "unacknowledged"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [("yes", "Unacknowledged"), ("no", "Acknowledged")]

    def queryset(self, request: HttpRequest, queryset: Any) -> Any:
        if self.value() == "yes":
            return queryset.filter(acknowledged_at__isnull=True)
        if self.value() == "no":
            return queryset.filter(acknowledged_at__isnull=False)
        return queryset


@admin.action(description="Mark selected alerts as acknowledged")
def _mark_drift_alerts_acknowledged(
    modeladmin: admin.ModelAdmin,  # type: ignore[type-arg]
    request: HttpRequest,
    queryset: Any,
) -> None:
    now = timezone.now()
    queryset.filter(acknowledged_at__isnull=True).update(acknowledged_at=now, updated_at=now)


@admin.register(PricingDriftAlert)
class PricingDriftAlertAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "detected_at",
        "provider",
        "gpu",
        "tier",
        "curated_usd_per_hour",
        "observed_usd_per_hour",
        "drift_pct",
        "severity",
        "acknowledged_at",
    )
    list_filter = ("severity", _UnacknowledgedFilter, "provider")
    actions = (_mark_drift_alerts_acknowledged,)
