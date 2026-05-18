from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.db.models import Q


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
