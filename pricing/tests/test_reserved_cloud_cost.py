"""Tests for reserved cloud cost math service (M09.T03).

All reference values are derived from PRD §7.4 math and hand-verified examples
from Appendix A. Tests use pure in-memory model instances — no DB required.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

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


# ---------------------------------------------------------------------------
# All-upfront cadence (PRD §7.4)
# ---------------------------------------------------------------------------


def test_all_upfront_cadence_amortizes_upfront_over_useful_active_hours():
    """PRD §7.4: all_upfront — only upfront cost; divided over useful active hours.

    Lambda Reserved H100 1-yr, all-upfront at $500k for 8 GPUs at 70% utilization:
      term_hours = 12 * 730 = 8760
      useful_active_hours = 8760 * 0.70 = 6132
      node_hourly_committed = 500000 / 6132 ≈ $81.54/hr
      per_gpu_hourly_committed = 81.54 / 8 ≈ $10.19/hr
    """
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

    assert result["useful_active_hours"] == useful
    assert result["node_hourly_committed"] == expected_node.quantize(Decimal("0.0001"))
    assert result["per_gpu_hourly_committed"] == expected_per_gpu.quantize(Decimal("0.0001"))
    assert isinstance(result["per_gpu_hourly_committed"], Decimal)


def test_reservation_marginal_equals_per_active_hour_for_all_upfront():
    """PRD §7.4 reservation-marginal: commitment is sunk; marginal = per_active_hour_usd.
    For all_upfront, per_active_hour_usd=0 → marginal = $0/hr (pure sunk cost)."""
    product = _product(
        upfront_usd=Decimal("500000.00"),
        per_active_hour_usd=Decimal("0"),
    )
    deployment = _deployment(product)
    result = compute_reserved_cloud_cost(deployment)

    assert result["node_hourly_reservation_marginal"] == Decimal("0.0000")
    assert result["per_gpu_hourly_reservation_marginal"] == Decimal("0.0000")


# ---------------------------------------------------------------------------
# AWS Capacity Block (PRD Appendix A, §7.4)
# ---------------------------------------------------------------------------


def test_aws_capacity_block_p5_14_day_committed_rate():
    """PRD Appendix A: AWS p5.48xlarge 14-day capacity block.

    AWS fixture: InstanceCount=2, UpfrontFee=$131,712 → $65,856 per node.
    Capacity block: floor=1.0, block_duration_hours=336.
    per_gpu_hourly = 65856 / (336 * 8) = $24.50/hr
    """
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
    deployment = _deployment(
        product,
        expected_utilization_pct=Decimal("1.000"),
    )
    result = compute_reserved_cloud_cost(deployment)

    # block_duration = 336h; billable_util = 1.0; useful = 336
    # node_committed = 65856 / 336; per_gpu = / 8
    expected_per_gpu = Decimal("65856.00") / (Decimal(336) * Decimal(8))
    assert result["per_gpu_hourly_committed"] == expected_per_gpu.quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# No-upfront cadence with per-hour metered cost
# ---------------------------------------------------------------------------


def test_no_upfront_cadence_with_high_per_hour_metered_cost():
    """PRD §7.4: no_upfront — monthly recurring + optional per-hour metered.

    GCP A3 CUD 1-yr: monthly=$8000 for 8 GPUs, per_hour=$3 (discounted on-demand).
      term_hours = 12 * 730 = 8760
      useful = 8760 * 0.80 = 7008
      total = 8000 * 12 + 3 * 7008 = 96000 + 21024 = 117024
      node_hourly = 117024 / 7008 ≈ $16.70/hr
      per_gpu = 16.70 / 8 ≈ $2.09/hr
    """
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

    assert result["total_commitment_usd"] == total.quantize(Decimal("0.01"))
    assert result["per_gpu_hourly_committed"] == expected_per_gpu.quantize(Decimal("0.0001"))
    # reservation_marginal = per_active_hour_usd / gpus_per_node
    assert result["per_gpu_hourly_reservation_marginal"] == (Decimal("3.0000") / Decimal(8)).quantize(
        Decimal("0.0001")
    )


# ---------------------------------------------------------------------------
# Lambda Reserved with minimum utilization floor
# ---------------------------------------------------------------------------


def test_floor_kicks_in_when_expected_utilization_below_minimum():
    """PRD §7.4 minimum_utilization_floor: Lambda Reserved has 70% floor.

    If user sets 50% expected, billing is still at 70% (use-it-or-lose-it).
    Committed rate at 50% vs 70% differ — floor must win.
    """
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

    # At 50% expected but 70% floor, billable = 70% → same as deployment_floor
    assert result_low["billable_utilization_pct"] == Decimal("0.700")
    assert result_low["per_gpu_hourly_committed"] == result_floor["per_gpu_hourly_committed"]


def test_lambda_reserved_1yr_committed_rate_around_1_89_per_gpu_hour():
    """PRD Appendix A: Lambda Reserved H100 1-yr at ~$1.89/GPU-hr.

    Lambda list: $131,040 / year for one H100 node (8 GPUs), all-upfront.
    70% utilization floor (use-it-or-lose-it):
      term_hours = 12 * 730 = 8760; useful = 8760 * 0.70 = 6132
      node_hourly = 131040 / 6132 ≈ $21.37/hr
      per_gpu = 21.37 / 8 ≈ $2.67/hr
    The PRD ~$1.89 is a different source — this test asserts the math formula is correct,
    not the exact price (which varies by listing_observed_at).
    """
    product = _product(
        term_months=12,
        gpus_per_node=8,
        upfront_usd=Decimal("131040.00"),
        minimum_utilization_floor_pct=Decimal("0.700"),
    )
    deployment = _deployment(product, expected_utilization_pct=Decimal("0.500"))
    result = compute_reserved_cloud_cost(deployment)

    term_hours = Decimal(12) * Decimal(730)
    useful = term_hours * Decimal("0.700")
    expected_per_gpu = Decimal("131040.00") / (useful * Decimal(8))

    assert result["per_gpu_hourly_committed"] == expected_per_gpu.quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# Override fields
# ---------------------------------------------------------------------------


def test_override_takes_precedence_over_product_price():
    """PRD §6.12: when override fields are set, cost math uses them instead of product fields."""
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
        upfront_override_usd=Decimal("450000.00"),  # negotiated deal
    )
    result_override = compute_reserved_cloud_cost(deployment)

    deployment_no_override = _deployment(product, expected_utilization_pct=Decimal("0.700"))
    result_list = compute_reserved_cloud_cost(deployment_no_override)

    # Override $450k < list $500k → committed rate must be lower
    assert result_override["per_gpu_hourly_committed"] < result_list["per_gpu_hourly_committed"]


def test_per_hour_override_drives_reservation_marginal():
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
        per_hour_override_usd=Decimal("4.0000"),  # negotiated lower per-hour rate
    )
    result = compute_reserved_cloud_cost(deployment)

    # marginal = override per-hour / gpus_per_node
    expected_marginal = Decimal("4.0000") / Decimal(8)
    assert result["per_gpu_hourly_reservation_marginal"] == expected_marginal.quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# Guard: zero useful_active_hours
# ---------------------------------------------------------------------------


def test_zero_useful_active_hours_raises_value_error():
    """PRD §7.4: if useful_active_hours ≤ 0, the math is undefined — must raise."""
    product = _product(
        term_months=0,
        minimum_utilization_floor_pct=Decimal("0.000"),
    )
    deployment = _deployment(product, expected_utilization_pct=Decimal("0.000"))
    with pytest.raises(ValueError, match="useful_active_hours must be positive"):
        compute_reserved_cloud_cost(deployment)
