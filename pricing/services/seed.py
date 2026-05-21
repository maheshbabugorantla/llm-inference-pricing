from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ProviderYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    provider_type: str
    data_source_tier: str
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
