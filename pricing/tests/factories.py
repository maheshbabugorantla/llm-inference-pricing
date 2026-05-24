from __future__ import annotations

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from catalog.tests.factories import GPUFactory
from pricing.models import HardwareSKU, OnPremDeployment, PricingDriftAlert, PricingSnapshot, Provider


class ProviderFactory(DjangoModelFactory):
    class Meta:
        model = Provider

    slug = factory.Sequence(lambda n: f"provider-{n}")
    display_name = "Test Provider"
    provider_type = "cloud"
    data_source_tier = "realtime_api"


class HardwareSKUFactory(DjangoModelFactory):
    class Meta:
        model = HardwareSKU

    slug = factory.Sequence(lambda n: f"test-sku-{n}")
    display_name = "Test SKU"
    vendor = "supermicro"
    num_gpus = 4
    gpu = factory.SubFactory(GPUFactory)
    cpu_model = "Intel Xeon Platinum 8480+"
    cpu_sockets = 2
    ram_gb = 512
    nvme_tb = 10
    network_gbps = 200
    host_tdp_watts = 800
    reference_msrp_usd = None
    notes = ""


class OnPremDeploymentFactory(DjangoModelFactory):
    class Meta:
        model = OnPremDeployment

    slug = factory.Sequence(lambda n: f"test-deployment-{n}")
    display_name = "Test Deployment"
    hardware_sku = factory.SubFactory(HardwareSKUFactory)
    num_nodes = 1
    capex_per_node_usd = factory.LazyFunction(
        lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("180000.00")
    )
    salvage_pct = factory.LazyFunction(lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("0.100"))
    depreciation_years = 4
    expected_utilization_pct = factory.LazyFunction(
        lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("0.700")
    )
    power_usd_per_kwh = factory.LazyFunction(
        lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("0.1000")
    )
    pue = factory.LazyFunction(lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("1.400"))
    monthly_colo_usd = factory.LazyFunction(
        lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("1000.00")
    )
    monthly_bandwidth_usd = factory.LazyFunction(
        lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("200.00")
    )
    sysadmin_annual_burdened_usd = factory.LazyFunction(
        lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("200000.00")
    )
    gpu_count_per_admin = 128
    is_active = True
    notes = ""


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


class PricingDriftAlertFactory(DjangoModelFactory):
    class Meta:
        model = PricingDriftAlert

    provider = factory.SubFactory(ProviderFactory)
    gpu = factory.SubFactory(GPUFactory)
    tier = "on_demand"
    curated_usd_per_hour = factory.LazyFunction(
        lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("2.0000")
    )
    observed_usd_per_hour = factory.LazyFunction(
        lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("2.1000")
    )
    drift_pct = factory.LazyFunction(lambda: __import__("decimal", fromlist=["Decimal"]).Decimal("5.000"))
    source_url = factory.Sequence(lambda n: f"https://example.com/pricing/{n}")
    severity = "warning"
    acknowledged_at = None
    notes = ""
