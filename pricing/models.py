from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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


class ReservedCapacityProduct(models.Model):
    PAYMENT_CADENCE_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("all_upfront", "All Upfront"),
        ("partial_upfront", "Partial Upfront"),
        ("no_upfront", "No Upfront"),
        ("capacity_block", "Capacity Block"),
    ]

    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    cloud_provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        limit_choices_to={"provider_type": "cloud"},
    )
    gpu = models.ForeignKey("catalog.GPU", on_delete=models.PROTECT)
    gpus_per_node = models.PositiveSmallIntegerField()

    payment_cadence = models.CharField(max_length=16, choices=PAYMENT_CADENCE_CHOICES)
    term_months = models.PositiveSmallIntegerField()

    upfront_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    monthly_recurring_usd = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    per_active_hour_usd = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0"))
    capacity_block_total_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    block_duration_hours = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Actual block duration in hours for capacity_block cadence (e.g. 336 for 14d). "
        "Ignored for other cadences.",
    )

    minimum_utilization_floor_pct = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    on_demand_reference_usd_per_hour = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="display-only; never used in math",
    )

    listing_observed_at = models.DateField(help_text="when this product's pricing was last verified")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("cloud_provider__slug", "term_months", "slug")

    def __str__(self) -> str:
        return self.display_name

    def clean(self) -> None:
        if self.payment_cadence == "all_upfront":
            if (
                self.monthly_recurring_usd != 0
                or self.per_active_hour_usd != 0
                or self.capacity_block_total_usd != 0
            ):
                raise ValidationError(
                    "all_upfront requires monthly_recurring_usd=0, per_active_hour_usd=0, "
                    "and capacity_block_total_usd=0"
                )
            if self.upfront_usd <= 0:
                raise ValidationError("all_upfront requires upfront_usd > 0")
        elif self.payment_cadence == "partial_upfront":
            if self.capacity_block_total_usd != 0:
                raise ValidationError("partial_upfront requires capacity_block_total_usd=0")
            if self.upfront_usd <= 0:
                raise ValidationError("partial_upfront requires upfront_usd > 0")
            if self.monthly_recurring_usd <= 0:
                raise ValidationError("partial_upfront requires monthly_recurring_usd > 0")
        elif self.payment_cadence == "no_upfront":
            if self.upfront_usd != 0 or self.capacity_block_total_usd != 0:
                raise ValidationError("no_upfront requires upfront_usd=0 and capacity_block_total_usd=0")
            if self.monthly_recurring_usd <= 0:
                raise ValidationError("no_upfront requires monthly_recurring_usd > 0")
        elif self.payment_cadence == "capacity_block":
            if self.upfront_usd != 0 or self.monthly_recurring_usd != 0 or self.per_active_hour_usd != 0:
                raise ValidationError(
                    "capacity_block requires upfront_usd=0, monthly_recurring_usd=0, "
                    "and per_active_hour_usd=0"
                )
            if self.capacity_block_total_usd <= 0:
                raise ValidationError("capacity_block requires capacity_block_total_usd > 0")
            if not self.block_duration_hours:
                raise ValidationError("capacity_block requires block_duration_hours > 0")


class ReservedCloudDeployment(models.Model):
    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    product = models.ForeignKey(ReservedCapacityProduct, on_delete=models.PROTECT)
    cloud_provider = models.ForeignKey(Provider, on_delete=models.PROTECT)
    region = models.CharField(max_length=64, blank=True)
    num_nodes = models.PositiveIntegerField(default=1)

    expected_utilization_pct = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=Decimal("0.700"),
        validators=[MinValueValidator(Decimal("0.001")), MaxValueValidator(Decimal("1.000"))],
    )

    upfront_override_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    monthly_recurring_override_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    per_hour_override_usd = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)

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


@receiver(post_save, sender=ReservedCloudDeployment)
def _regenerate_reserved_on_save(
    sender: type[ReservedCloudDeployment],
    instance: ReservedCloudDeployment,
    **kwargs: object,
) -> None:
    if not kwargs.get("created", False) and not instance.is_active:
        return
    from pricing.generators.reserved_cloud import regenerate_reserved_cloud_snapshots

    transaction.on_commit(regenerate_reserved_cloud_snapshots)
