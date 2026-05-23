from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProviderYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    provider_type: Literal["cloud", "on_prem"]
    data_source_tier: Literal["realtime_api", "scraped_page", "manual_curation", "synthetic"]
    pricing_url: str = ""
    has_api: bool = False
    api_endpoint: str = ""
    is_active: bool = True


class HardwareSKUYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    vendor: str
    num_gpus: int
    gpu_slug: str
    cpu_model: str
    cpu_sockets: int
    ram_gb: int
    nvme_tb: int
    network_gbps: int
    host_tdp_watts: int
    reference_msrp_usd: Decimal | None = None
    notes: str = ""


class ReservedCapacityProductYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    cloud_provider_slug: str
    gpu_slug: str
    gpus_per_node: int
    payment_cadence: Literal["all_upfront", "partial_upfront", "no_upfront", "capacity_block"]
    term_months: int
    upfront_usd: Decimal = Decimal("0")
    monthly_recurring_usd: Decimal = Decimal("0")
    per_active_hour_usd: Decimal = Decimal("0")
    capacity_block_total_usd: Decimal = Decimal("0")
    minimum_utilization_floor_pct: Decimal = Decimal("0.000")
    on_demand_reference_usd_per_hour: Decimal | None = None
    listing_observed_at: str
    notes: str = ""
    is_active: bool = True


class ReservedCloudDeploymentYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    product_slug: str
    cloud_provider_slug: str
    region: str = ""
    num_nodes: int = 1
    expected_utilization_pct: Decimal = Decimal("0.700")
    upfront_override_usd: Decimal | None = None
    monthly_recurring_override_usd: Decimal | None = None
    per_hour_override_usd: Decimal | None = None
    is_active: bool = True
    notes: str = ""


class OnPremDeploymentYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    hardware_sku_slug: str
    num_nodes: int = 1
    capex_per_node_usd: Decimal
    salvage_pct: Decimal = Decimal("0.100")
    depreciation_years: int = 4
    expected_utilization_pct: Decimal = Decimal("0.700")
    power_usd_per_kwh: Decimal
    pue: Decimal = Decimal("1.400")
    monthly_colo_usd: Decimal = Decimal("0")
    monthly_bandwidth_usd: Decimal = Decimal("0")
    sysadmin_annual_burdened_usd: Decimal
    gpu_count_per_admin: int = 128
    is_active: bool = True
    notes: str = ""
