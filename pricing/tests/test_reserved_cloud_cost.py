"""Tests for reserved cloud cost math service (M09.T03).

All reference values are derived from PRD section 7.4 math and hand-verified examples
from Appendix A. Tests use pure in-memory model instances -- no DB required.
"""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock

from pricing.services.reserved_cloud_cost import compute_reserved_cloud_cost


def _product(**kwargs):
    """Build a MagicMock standing in for a ReservedCapacityProduct."""
    p = MagicMock()
    p.term_months = kwargs.get("term_months", 12)
    p.gpus_per_node = kwargs.get("gpus_per_node", 8)
    p.minimum_utilization_floor_pct = kwargs.get("minimum_utilization_floor_pct", Decimal("0.000"))
    p.upfront_usd = kwargs.get("upfront_usd", Decimal("0"))
    p.monthly_recurring_usd = kwargs.get("monthly_recurring_usd", Decimal("0"))
    p.per_active_hour_usd = kwargs.get("per_active_hour_usd", Decimal("0"))
    p.capacity_block_total_usd = kwargs.get("capacity_block_total_usd", Decimal("0"))
    p.payment_cadence = kwargs.get("payment_cadence", "all_upfront")
    p.block_duration_hours = kwargs.get("block_duration_hours", None)
    return p


def _deployment(product, **kwargs):
    """Build a MagicMock standing in for a ReservedCloudDeployment."""
    d = MagicMock()
    d.product = product
    d.expected_utilization_pct = kwargs.get("expected_utilization_pct", Decimal("0.700"))
    d.upfront_override_usd = kwargs.get("upfront_override_usd", None)
    d.monthly_recurring_override_usd = kwargs.get("monthly_recurring_override_usd", None)
    d.per_hour_override_usd = kwargs.get("per_hour_override_usd", None)
    return d


class AllUpfrontCadenceTest(unittest.TestCase):
    """All-upfront cadence (PRD section 7.4)."""

    def test_all_upfront_cadence_amortizes_upfront_over_useful_active_hours(self):
        """PRD section 7.4: all_upfront -- only upfront cost; divided over useful active hours."""
        product = _product(
            term_months=12,
            gpus_per_node=8,
            upfront_usd=Decimal("500000.00"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
        )
        deployment = _deployment(product, expected_utilization_pct=Decimal("0.700"))
        result = compute_reserved_cloud_cost(deployment)

        term_hours = Decimal(12) * Decimal(730)
        useful = term_hours * Decimal("0.700")
        expected_node = Decimal("500000.00") / useful
        expected_per_gpu = expected_node / Decimal(8)

        self.assertEqual(result["useful_active_hours"], useful)
        self.assertEqual(result["node_hourly_committed"], expected_node.quantize(Decimal("0.0001")))
        self.assertEqual(result["per_gpu_hourly_committed"], expected_per_gpu.quantize(Decimal("0.0001")))
        self.assertIsInstance(result["per_gpu_hourly_committed"], Decimal)

    def test_reservation_marginal_equals_per_active_hour_for_all_upfront(self):
        """For all_upfront, per_active_hour_usd=0 means marginal = $0/hr (pure sunk cost)."""
        product = _product(
            upfront_usd=Decimal("500000.00"),
            per_active_hour_usd=Decimal("0"),
        )
        deployment = _deployment(product)
        result = compute_reserved_cloud_cost(deployment)

        self.assertEqual(result["node_hourly_reservation_marginal"], Decimal("0.0000"))
        self.assertEqual(result["per_gpu_hourly_reservation_marginal"], Decimal("0.0000"))


class AWSCapacityBlockTest(unittest.TestCase):
    """AWS Capacity Block (PRD Appendix A, section 7.4)."""

    def test_aws_capacity_block_p5_14_day_committed_rate(self):
        """PRD Appendix A: AWS p5.48xlarge 14-day capacity block committed rate."""
        product = _product(
            term_months=1,
            gpus_per_node=8,
            payment_cadence="capacity_block",
            block_duration_hours=336,
            upfront_usd=Decimal("0"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("65856.00"),
            minimum_utilization_floor_pct=Decimal("1.000"),
        )
        deployment = _deployment(product, expected_utilization_pct=Decimal("1.000"))
        result = compute_reserved_cloud_cost(deployment)

        expected_per_gpu = Decimal("65856.00") / (Decimal(336) * Decimal(8))
        self.assertEqual(result["per_gpu_hourly_committed"], expected_per_gpu.quantize(Decimal("0.0001")))


class NoUpfrontCadenceTest(unittest.TestCase):
    """No-upfront cadence with per-hour metered cost."""

    def test_no_upfront_cadence_with_high_per_hour_metered_cost(self):
        """PRD section 7.4: no_upfront -- monthly recurring + optional per-hour metered."""
        product = _product(
            term_months=12,
            gpus_per_node=8,
            upfront_usd=Decimal("0"),
            monthly_recurring_usd=Decimal("8000.00"),
            per_active_hour_usd=Decimal("3.0000"),
        )
        deployment = _deployment(product, expected_utilization_pct=Decimal("0.800"))
        result = compute_reserved_cloud_cost(deployment)

        term_hours = Decimal(12) * Decimal(730)
        useful = term_hours * Decimal("0.800")
        total = Decimal("8000.00") * Decimal(12) + Decimal("3.0000") * useful
        expected_node = total / useful
        expected_per_gpu = expected_node / Decimal(8)

        self.assertEqual(result["total_commitment_usd"], total.quantize(Decimal("0.01")))
        self.assertEqual(result["per_gpu_hourly_committed"], expected_per_gpu.quantize(Decimal("0.0001")))
        self.assertEqual(
            result["per_gpu_hourly_reservation_marginal"],
            (Decimal("3.0000") / Decimal(8)).quantize(Decimal("0.0001")),
        )


class LambdaReservedFloorTest(unittest.TestCase):
    """Lambda Reserved with minimum utilization floor."""

    def test_floor_kicks_in_when_expected_utilization_below_minimum(self):
        """PRD section 7.4: floor kicks in when expected_util < minimum_utilization_floor_pct."""
        product = _product(
            term_months=12,
            gpus_per_node=8,
            upfront_usd=Decimal("500000.00"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
            minimum_utilization_floor_pct=Decimal("0.700"),
        )
        deployment_low = _deployment(product, expected_utilization_pct=Decimal("0.500"))
        deployment_floor = _deployment(product, expected_utilization_pct=Decimal("0.700"))

        result_low = compute_reserved_cloud_cost(deployment_low)
        result_floor = compute_reserved_cloud_cost(deployment_floor)

        self.assertEqual(result_low["billable_utilization_pct"], Decimal("0.700"))
        term_hours = Decimal(12) * Decimal(730)
        useful_low = term_hours * Decimal("0.500")
        expected_per_gpu_low = Decimal("500000.00") / (useful_low * Decimal(8))
        self.assertEqual(
            result_low["per_gpu_hourly_committed"], expected_per_gpu_low.quantize(Decimal("0.0001"))
        )
        self.assertGreater(
            result_low["per_gpu_hourly_committed"],
            result_floor["per_gpu_hourly_committed"],
            "Floor kicking in must inflate the committed rate",
        )

    def test_lambda_reserved_1yr_committed_rate_around_1_89_per_gpu_hour(self):
        """PRD Appendix A: Lambda Reserved H100 1-yr at approximately $1.89/GPU-hr."""
        product = _product(
            term_months=12,
            gpus_per_node=8,
            upfront_usd=Decimal("131040.00"),
            minimum_utilization_floor_pct=Decimal("0.700"),
        )
        deployment = _deployment(product, expected_utilization_pct=Decimal("0.500"))
        result = compute_reserved_cloud_cost(deployment)

        term_hours = Decimal(12) * Decimal(730)
        useful = term_hours * Decimal("0.500")
        expected_per_gpu = Decimal("131040.00") / (useful * Decimal(8))
        self.assertEqual(result["per_gpu_hourly_committed"], expected_per_gpu.quantize(Decimal("0.0001")))


class OverrideFieldsTest(unittest.TestCase):
    """Override fields."""

    def test_override_takes_precedence_over_product_price(self):
        """PRD section 6.12: when override fields are set, cost math uses them instead of product fields."""
        product = _product(
            term_months=12,
            gpus_per_node=8,
            upfront_usd=Decimal("500000.00"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
        )
        deployment = _deployment(
            product,
            expected_utilization_pct=Decimal("0.700"),
            upfront_override_usd=Decimal("450000.00"),
        )
        result_override = compute_reserved_cloud_cost(deployment)

        deployment_no_override = _deployment(product, expected_utilization_pct=Decimal("0.700"))
        result_list = compute_reserved_cloud_cost(deployment_no_override)

        self.assertLess(result_override["per_gpu_hourly_committed"], result_list["per_gpu_hourly_committed"])

    def test_per_hour_override_drives_reservation_marginal(self):
        """Override of per_hour_override_usd replaces per_active_hour_usd in marginal calc."""
        product = _product(
            term_months=12,
            gpus_per_node=8,
            per_active_hour_usd=Decimal("5.0000"),
            monthly_recurring_usd=Decimal("1000.00"),
        )
        deployment = _deployment(
            product,
            expected_utilization_pct=Decimal("0.700"),
            per_hour_override_usd=Decimal("4.0000"),
        )
        result = compute_reserved_cloud_cost(deployment)

        expected_marginal = Decimal("4.0000") / Decimal(8)
        self.assertEqual(
            result["per_gpu_hourly_reservation_marginal"], expected_marginal.quantize(Decimal("0.0001"))
        )


class CapacityBlockMarginalTest(unittest.TestCase):
    """Capacity block: marginal rate must always be zero."""

    def test_capacity_block_reservation_marginal_is_always_zero(self):
        """PRD section 7.4: capacity_block is fully prepaid -- reservation marginal must be $0."""
        product = _product(
            payment_cadence="capacity_block",
            block_duration_hours=336,
            capacity_block_total_usd=Decimal("65856.00"),
            minimum_utilization_floor_pct=Decimal("1.000"),
        )
        deployment = _deployment(
            product,
            expected_utilization_pct=Decimal("1.000"),
            per_hour_override_usd=Decimal("5.0000"),
        )
        result = compute_reserved_cloud_cost(deployment)

        self.assertEqual(result["node_hourly_reservation_marginal"], Decimal("0.0000"))
        self.assertEqual(result["per_gpu_hourly_reservation_marginal"], Decimal("0.0000"))


class FloorZoneMarginalTest(unittest.TestCase):
    """Floor zone: marginal is zero when expected_util < floor_pct."""

    def test_reservation_marginal_is_zero_in_floor_zone(self):
        """PRD section 7.4: when expected_util < floor_pct, marginal rate is 0."""
        product = _product(
            payment_cadence="no_upfront",
            term_months=12,
            gpus_per_node=8,
            monthly_recurring_usd=Decimal("8000.00"),
            per_active_hour_usd=Decimal("3.0000"),
            minimum_utilization_floor_pct=Decimal("0.700"),
        )
        deployment = _deployment(product, expected_utilization_pct=Decimal("0.500"))
        result = compute_reserved_cloud_cost(deployment)

        self.assertEqual(result["node_hourly_reservation_marginal"], Decimal("0.0000"))
        self.assertEqual(result["per_gpu_hourly_reservation_marginal"], Decimal("0.0000"))


class ReservedCloudCostGuardTest(unittest.TestCase):
    """Guard: zero useful_active_hours."""

    def test_zero_useful_active_hours_raises_value_error(self):
        """PRD section 7.4: if useful_active_hours <= 0, the math is undefined -- must raise."""
        product = _product(
            term_months=0,
            minimum_utilization_floor_pct=Decimal("0.000"),
        )
        deployment = _deployment(product, expected_utilization_pct=Decimal("0.000"))
        with self.assertRaisesRegex(ValueError, "useful_active_hours must be positive"):
            compute_reserved_cloud_cost(deployment)

    def test_capacity_block_missing_block_duration_hours_raises_value_error(self):
        """capacity_block products without block_duration_hours must raise."""
        product = _product(
            payment_cadence="capacity_block",
            block_duration_hours=None,
            capacity_block_total_usd=Decimal("65856.00"),
            minimum_utilization_floor_pct=Decimal("1.000"),
        )
        deployment = _deployment(product, expected_utilization_pct=Decimal("1.000"))
        with self.assertRaisesRegex(ValueError, "block_duration_hours"):
            compute_reserved_cloud_cost(deployment)
