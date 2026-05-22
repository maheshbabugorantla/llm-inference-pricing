from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.db import models, transaction
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver


class Provider(models.Model):
    TYPE_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("cloud", "Cloud"),
        ("on_prem", "On-premises"),
    ]
    DATA_SOURCE_TIERS: ClassVar[list[tuple[str, str]]] = [
        ("realtime_api", "Tier 1 — real-time machine-readable API"),
        ("scraped_page", "Tier 2 — HTML/page scraping"),
        ("manual_curation", "Tier 3 — gated; YAML curation + override"),
        ("synthetic", "On-prem / reserved-cloud generator output"),
    ]

    slug = models.SlugField(unique=True, max_length=64)
    display_name = models.CharField(max_length=64)
    provider_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    data_source_tier = models.CharField(max_length=24, choices=DATA_SOURCE_TIERS)
    pricing_url = models.URLField(blank=True)
    has_api = models.BooleanField(default=False)
    api_endpoint = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("provider_type", "slug")

    def __str__(self) -> str:
        return self.display_name


class PricingSnapshot(models.Model):
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT)
    gpu = models.ForeignKey("catalog.GPU", on_delete=models.PROTECT)
    tier = models.CharField(max_length=64)
    region = models.CharField(max_length=64, blank=True)
    hourly_usd = models.DecimalField(max_digits=8, decimal_places=4)
    available = models.BooleanField(default=True)
    scraped_at = models.DateTimeField(db_index=True)
    raw_payload = models.JSONField()

    class Meta:
        ordering = ("-scraped_at",)
        indexes: ClassVar = [
            models.Index(fields=["provider", "gpu", "tier", "-scraped_at"]),
        ]
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(hourly_usd__gte=0),
                name="pricingsnapshot_hourly_usd_nonneg",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider.slug}/{self.gpu.slug}/{self.tier} @ {self.scraped_at:%Y-%m-%d %H:%M}"


class HardwareSKU(models.Model):
    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    vendor = models.CharField(max_length=64)
    num_gpus = models.PositiveSmallIntegerField()
    gpu = models.ForeignKey("catalog.GPU", on_delete=models.PROTECT)
    cpu_model = models.CharField(max_length=128)
    cpu_sockets = models.PositiveSmallIntegerField()
    ram_gb = models.PositiveIntegerField()
    nvme_tb = models.PositiveIntegerField()
    network_gbps = models.PositiveIntegerField()
    host_tdp_watts = models.PositiveIntegerField(help_text="non-GPU power draw at peak")
    reference_msrp_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("vendor", "slug")

    def __str__(self) -> str:
        return self.display_name


class OnPremDeployment(models.Model):
    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    hardware_sku = models.ForeignKey(HardwareSKU, on_delete=models.PROTECT)
    num_nodes = models.PositiveIntegerField(default=1)

    capex_per_node_usd = models.DecimalField(max_digits=10, decimal_places=2)
    salvage_pct = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0.100"))
    depreciation_years = models.PositiveSmallIntegerField(default=4)

    expected_utilization_pct = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0.700"))

    power_usd_per_kwh = models.DecimalField(max_digits=6, decimal_places=4)
    pue = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("1.400"))
    monthly_colo_usd = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    monthly_bandwidth_usd = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))

    sysadmin_annual_burdened_usd = models.DecimalField(max_digits=10, decimal_places=2)
    gpu_count_per_admin = models.PositiveIntegerField(default=128)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("slug",)

    def __str__(self) -> str:
        return self.display_name


@receiver(post_save, sender=OnPremDeployment)
def _regenerate_on_save(sender: type[OnPremDeployment], instance: OnPremDeployment, **kwargs: object) -> None:
    if not kwargs.get("created", False) and not instance.is_active:
        return
    from pricing.generators.on_prem import regenerate_on_prem_snapshots

    transaction.on_commit(regenerate_on_prem_snapshots)
