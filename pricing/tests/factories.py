from __future__ import annotations

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider


class ProviderFactory(DjangoModelFactory):
    class Meta:
        model = Provider

    slug = factory.Sequence(lambda n: f"provider-{n}")
    display_name = "Test Provider"
    provider_type = "cloud"
    data_source_tier = "realtime_api"


class PricingSnapshotFactory(DjangoModelFactory):
    class Meta:
        model = PricingSnapshot

    provider = factory.SubFactory(ProviderFactory)
    gpu = factory.SubFactory(GPUFactory)
    tier = "on_demand"
    region = ""
    hourly_usd = factory.LazyFunction(lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("2.4900"))
    available = True
    scraped_at = factory.LazyFunction(timezone.now)
    raw_payload = factory.LazyFunction(dict)
