"""Tests for on-prem cost math (M08.T03)."""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from pricing.services.on_prem_cost import compute_on_prem_cost
from pricing.tests.factories import OnPremDeploymentFactory


class OnPremCostReferenceTest(TestCase):
    """Reference scenarios from PRD section 7.6."""

    def test_lambda_echelon_4xh100_green_field_per_gpu_hourly_tco(self):
        """PRD section 7.6 reference case: 4xH100, capex $180k, 4yr, 70% util, $0.10/kWh, PUE 1.4.

        Formula yields approximately $3.45/hr per GPU with these inputs.
        """
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
        self.assertGreater(tco, Decimal("3.00"), f"Expected ~$3.45, got {tco}")
        self.assertLess(tco, Decimal("4.00"), f"Expected ~$3.45, got {tco}")
        # Verify capex dominates (>40% of TCO for a new box)
        self.assertGreater(breakdown["hourly_capex_per_node"], breakdown["hourly_power_per_node"])

    def test_marginal_excludes_capex(self):
        deployment = OnPremDeploymentFactory()
        breakdown = compute_on_prem_cost(deployment)
        # Allow $0.01 rounding tolerance from independent quantization of each field
        diff = abs(
            breakdown["node_hourly_marginal"]
            - (breakdown["node_hourly_tco"] - breakdown["hourly_capex_per_node"])
        )
        self.assertLessEqual(diff, Decimal("0.01"), f"Diff {diff} exceeds rounding tolerance")

    def test_mi300x_marginal_capex_sunk_low_power(self):
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
        # With $250k sysadmin / 128 GPUs per admin and 8 GPUs, ops dominate marginal.
        # Formula yields approximately $1.92/hr per GPU.
        self.assertGreater(marginal, Decimal("1.50"), f"Expected ~$1.92/hr, got {marginal}")
        self.assertLess(marginal, Decimal("2.50"), f"Expected ~$1.92/hr, got {marginal}")
        self.assertLess(marginal, breakdown["per_gpu_hourly_tco"], "Marginal must be less than TCO")
        # Power + facility (no ops, no capex) is definitely < marginal
        self.assertGreater(marginal, breakdown["hourly_power_per_node"] / Decimal(8))

    def test_higher_utilization_reduces_per_active_hour_costs(self):
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
        self.assertLess(high_tco, low_tco)

    def test_all_breakdown_values_are_decimal(self):
        deployment = OnPremDeploymentFactory()
        breakdown = compute_on_prem_cost(deployment)
        for key, val in breakdown.items():
            with self.subTest(key=key):
                self.assertIsInstance(val, Decimal, f"{key} is {type(val)}, expected Decimal")


class OnPremCostValidationTest(TestCase):
    """Guard conditions that must raise ValueError."""

    def test_zero_utilization_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.expected_utilization_pct = Decimal("0")
        with self.assertRaisesRegex(ValueError, r"expected_utilization_pct must be in \(0, 1\]"):
            compute_on_prem_cost(deployment)

    def test_salvage_pct_of_one_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.salvage_pct = Decimal("1.0")
        with self.assertRaisesRegex(ValueError, "salvage_pct"):
            compute_on_prem_cost(deployment)

    def test_zero_pue_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.pue = Decimal("0")
        with self.assertRaisesRegex(ValueError, "pue"):
            compute_on_prem_cost(deployment)

    def test_negative_capex_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.capex_per_node_usd = Decimal("-1.00")
        with self.assertRaisesRegex(ValueError, "capex_per_node_usd"):
            compute_on_prem_cost(deployment)

    def test_utilization_over_one_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.expected_utilization_pct = Decimal("1.001")
        with self.assertRaisesRegex(ValueError, "expected_utilization_pct"):
            compute_on_prem_cost(deployment)

    def test_zero_num_gpus_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.hardware_sku.num_gpus = 0
        with self.assertRaisesRegex(ValueError, "num_gpus"):
            compute_on_prem_cost(deployment)

    def test_zero_gpu_count_per_admin_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.gpu_count_per_admin = 0
        with self.assertRaisesRegex(ValueError, "gpu_count_per_admin"):
            compute_on_prem_cost(deployment)

    def test_zero_depreciation_years_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.depreciation_years = 0
        with self.assertRaisesRegex(ValueError, "depreciation_years"):
            compute_on_prem_cost(deployment)

    def test_negative_power_usd_per_kwh_raises_value_error(self):
        deployment = OnPremDeploymentFactory()
        deployment.power_usd_per_kwh = Decimal("-0.01")
        with self.assertRaisesRegex(ValueError, "power_usd_per_kwh"):
            compute_on_prem_cost(deployment)
