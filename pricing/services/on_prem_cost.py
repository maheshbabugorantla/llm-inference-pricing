from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pricing.models import OnPremDeployment


class CostBreakdown(TypedDict):
    hourly_capex_per_node: Decimal
    hourly_power_per_node: Decimal
    hourly_colo_per_node: Decimal
    hourly_bandwidth_per_node: Decimal
    hourly_ops_per_node: Decimal
    node_hourly_tco: Decimal
    node_hourly_marginal: Decimal
    per_gpu_hourly_tco: Decimal
    per_gpu_hourly_marginal: Decimal


def compute_on_prem_cost(deployment: OnPremDeployment) -> CostBreakdown:
    """Pure function. Assumes deployment.hardware_sku and hardware_sku.gpu are prefetched."""
    if not (Decimal(0) < deployment.expected_utilization_pct <= Decimal(1)):
        raise ValueError("expected_utilization_pct must be in (0, 1]")
    if deployment.depreciation_years <= 0:
        raise ValueError("depreciation_years must be > 0")
    if not (Decimal(0) <= deployment.salvage_pct < Decimal(1)):
        raise ValueError("salvage_pct must be in [0, 1)")
    if deployment.pue <= 0:
        raise ValueError("pue must be > 0")
    if deployment.capex_per_node_usd < 0:
        raise ValueError("capex_per_node_usd must be >= 0")
    if deployment.power_usd_per_kwh < 0:
        raise ValueError("power_usd_per_kwh must be >= 0")

    sku = deployment.hardware_sku
    gpu = sku.gpu

    if sku.num_gpus <= 0:
        raise ValueError("hardware_sku.num_gpus must be > 0")
    if deployment.gpu_count_per_admin <= 0:
        raise ValueError("gpu_count_per_admin must be > 0")

    depreciable = deployment.capex_per_node_usd * (Decimal(1) - deployment.salvage_pct)
    active_hours_per_year = Decimal(8760) * deployment.expected_utilization_pct
    hourly_capex = depreciable / Decimal(deployment.depreciation_years) / active_hours_per_year

    node_watts = Decimal(gpu.tdp_watts) * Decimal(sku.num_gpus) + Decimal(sku.host_tdp_watts)
    power_kw_with_pue = node_watts * deployment.pue / Decimal(1000)
    hourly_power = power_kw_with_pue * deployment.power_usd_per_kwh

    hourly_colo = deployment.monthly_colo_usd / (Decimal(730) * deployment.expected_utilization_pct)
    hourly_bw = deployment.monthly_bandwidth_usd / (Decimal(730) * deployment.expected_utilization_pct)

    hourly_ops = (
        deployment.sysadmin_annual_burdened_usd
        / Decimal(2080)
        / Decimal(deployment.gpu_count_per_admin)
        / deployment.expected_utilization_pct
        * Decimal(sku.num_gpus)
    )

    node_tco = hourly_capex + hourly_power + hourly_colo + hourly_bw + hourly_ops
    node_marginal = hourly_power + hourly_colo + hourly_bw + hourly_ops

    return {
        "hourly_capex_per_node": hourly_capex.quantize(Decimal("0.01")),
        "hourly_power_per_node": hourly_power.quantize(Decimal("0.01")),
        "hourly_colo_per_node": hourly_colo.quantize(Decimal("0.01")),
        "hourly_bandwidth_per_node": hourly_bw.quantize(Decimal("0.01")),
        "hourly_ops_per_node": hourly_ops.quantize(Decimal("0.01")),
        "node_hourly_tco": node_tco.quantize(Decimal("0.01")),
        "node_hourly_marginal": node_marginal.quantize(Decimal("0.01")),
        "per_gpu_hourly_tco": (node_tco / Decimal(sku.num_gpus)).quantize(Decimal("0.0001")),
        "per_gpu_hourly_marginal": (node_marginal / Decimal(sku.num_gpus)).quantize(Decimal("0.0001")),
    }
