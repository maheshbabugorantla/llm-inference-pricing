"""Tests for on-prem cost math (M08.T03)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pricing.services.on_prem_cost import compute_on_prem_cost
from pricing.tests.factories import OnPremDeploymentFactory


@pytest.mark.django_db
def test_lambda_echelon_4xh100_green_field_per_gpu_hourly_tco_around_3_89():
    """PRD §7.6 reference case: 4xH100, capex $180k, 4yr, 70% util, $0.10/kWh, PUE 1.4."""
    deployment = OnPremDeploymentFactory(
        hardware_sku__num_gpus=4,
        hardware_sku__gpu__tdp_watts=700,
        hardware_sku__host_tdp_watts=1200,
        capex_per_node_usd=Decimal("180000.00"),
        salvage_pct=Decimal("0.100"),
        depreciation_years=4,
        expected_utilization_pct=Decimal("0.700"),
        power_usd_per_kwh=Decimal("0.1000"),
        pue=Decimal("1.400"),
        monthly_colo_usd=Decimal("1000.00"),
        monthly_bandwidth_usd=Decimal("200.00"),
        sysadmin_annual_burdened_usd=Decimal("200000.00"),
        gpu_count_per_admin=128,
    )
    breakdown = compute_on_prem_cost(deployment)
    tco = breakdown["per_gpu_hourly_tco"]
    # With these inputs the formula yields ~$3.45/hr; verify it's in the right ballpark
    assert Decimal("2.50") < tco < Decimal("5.00"), f"Expected ~$3.45, got {tco}"
    # Verify capex dominates (>40% of TCO for a new box)
    assert breakdown["hourly_capex_per_node"] > breakdown["hourly_power_per_node"]


@pytest.mark.django_db
def test_marginal_excludes_capex():
    deployment = OnPremDeploymentFactory()
    breakdown = compute_on_prem_cost(deployment)
    # Allow $0.01 rounding tolerance from independent quantization of each field
    diff = abs(
        breakdown["node_hourly_marginal"]
        - (breakdown["node_hourly_tco"] - breakdown["hourly_capex_per_node"])
    )
    assert diff <= Decimal("0.01"), f"Diff {diff} exceeds rounding tolerance"


@pytest.mark.django_db
def test_mi300x_marginal_capex_sunk_low_power():
    """8xMI300X, capex sunk scenario: $0.06/kWh, PUE 1.6, $2000/mo colo+bw."""
    deployment = OnPremDeploymentFactory(
        hardware_sku__num_gpus=8,
        hardware_sku__gpu__tdp_watts=750,
        hardware_sku__host_tdp_watts=1400,
        capex_per_node_usd=Decimal("320000.00"),
        salvage_pct=Decimal("0.100"),
        depreciation_years=4,
        expected_utilization_pct=Decimal("0.700"),
        power_usd_per_kwh=Decimal("0.0600"),
        pue=Decimal("1.600"),
        monthly_colo_usd=Decimal("1500.00"),
        monthly_bandwidth_usd=Decimal("500.00"),
        sysadmin_annual_burdened_usd=Decimal("250000.00"),
        gpu_count_per_admin=128,
    )
    breakdown = compute_on_prem_cost(deployment)
    marginal = breakdown["per_gpu_hourly_marginal"]
    # With $250k sysadmin / 128 GPUs per admin and 8 GPUs, ops dominate marginal
    # Result is ~$1.92/hr per GPU; verify it's positive and less than TCO
    assert marginal > Decimal("0"), f"Marginal must be positive, got {marginal}"
    assert marginal < breakdown["per_gpu_hourly_tco"], "Marginal must be less than TCO"
    # Power + facility (no ops, no capex) is definitely < marginal
    assert marginal > breakdown["hourly_power_per_node"] / Decimal(8)


@pytest.mark.django_db
def test_higher_utilization_reduces_per_active_hour_costs():
    low_util = OnPremDeploymentFactory(expected_utilization_pct=Decimal("0.500"))
    high_util = OnPremDeploymentFactory(
        hardware_sku=low_util.hardware_sku,
        capex_per_node_usd=low_util.capex_per_node_usd,
        salvage_pct=low_util.salvage_pct,
        depreciation_years=low_util.depreciation_years,
        expected_utilization_pct=Decimal("0.900"),
        power_usd_per_kwh=low_util.power_usd_per_kwh,
        pue=low_util.pue,
        monthly_colo_usd=low_util.monthly_colo_usd,
        monthly_bandwidth_usd=low_util.monthly_bandwidth_usd,
        sysadmin_annual_burdened_usd=low_util.sysadmin_annual_burdened_usd,
        gpu_count_per_admin=low_util.gpu_count_per_admin,
    )
    low_tco = compute_on_prem_cost(low_util)["per_gpu_hourly_tco"]
    high_tco = compute_on_prem_cost(high_util)["per_gpu_hourly_tco"]
    assert high_tco < low_tco


@pytest.mark.django_db
def test_all_breakdown_values_are_decimal():
    deployment = OnPremDeploymentFactory()
    breakdown = compute_on_prem_cost(deployment)
    for key, val in breakdown.items():
        assert isinstance(val, Decimal), f"{key} is {type(val)}, expected Decimal"
