from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pricing.models import ReservedCloudDeployment


class CostBreakdown(TypedDict):
    useful_active_hours: Decimal
    total_commitment_usd: Decimal
    node_hourly_committed: Decimal
    node_hourly_reservation_marginal: Decimal
    per_gpu_hourly_committed: Decimal
    per_gpu_hourly_reservation_marginal: Decimal
    billable_utilization_pct: Decimal


def _effective_costs(deployment: ReservedCloudDeployment) -> tuple[Decimal, Decimal, Decimal]:
    """Return (upfront, monthly_recurring, per_hour) using overrides if set."""
    p = deployment.product
    upfront = (
        deployment.upfront_override_usd if deployment.upfront_override_usd is not None else p.upfront_usd
    )
    monthly = (
        deployment.monthly_recurring_override_usd
        if deployment.monthly_recurring_override_usd is not None
        else p.monthly_recurring_usd
    )
    per_hour = (
        deployment.per_hour_override_usd
        if deployment.per_hour_override_usd is not None
        else p.per_active_hour_usd
    )
    return upfront, monthly, per_hour


def compute_reserved_cloud_cost(deployment: ReservedCloudDeployment) -> CostBreakdown:
    """PRD §7.4 reserved-cloud cost formula.

    All money in Decimal. Does not query the DB — caller must have loaded the
    deployment with select_related("product") already.
    """
    p = deployment.product
    expected_util = deployment.expected_utilization_pct
    billable_util = max(expected_util, p.minimum_utilization_floor_pct)

    if p.payment_cadence == "capacity_block" and p.block_duration_hours is not None:
        term_hours = Decimal(p.block_duration_hours)
    else:
        term_hours = Decimal(p.term_months) * Decimal(730)

    # PRD §7.4: billing uses the floor-adjusted util; the denominator (useful hours
    # actually received by the operator) uses expected_util only.
    billable_hours = term_hours * billable_util
    useful_hours = term_hours * expected_util
    if useful_hours <= 0:
        raise ValueError("useful_active_hours must be positive")

    upfront, monthly, per_hour = _effective_costs(deployment)

    if p.payment_cadence == "capacity_block":
        total_commitment = p.capacity_block_total_usd
        node_marginal = Decimal("0")
    else:
        total_commitment = upfront + monthly * Decimal(p.term_months) + per_hour * billable_hours
        # PRD §7.4: when expected_util < floor, metered hours are "free at the margin"
        # (they come out of the floor already paid); marginal = 0 in that zone.
        node_marginal = per_hour if expected_util >= p.minimum_utilization_floor_pct else Decimal("0")

    node_committed = total_commitment / useful_hours

    return CostBreakdown(
        useful_active_hours=useful_hours,
        total_commitment_usd=total_commitment.quantize(Decimal("0.01")),
        node_hourly_committed=node_committed.quantize(Decimal("0.0001")),
        node_hourly_reservation_marginal=node_marginal.quantize(Decimal("0.0001")),
        per_gpu_hourly_committed=(node_committed / Decimal(p.gpus_per_node)).quantize(Decimal("0.0001")),
        per_gpu_hourly_reservation_marginal=(node_marginal / Decimal(p.gpus_per_node)).quantize(
            Decimal("0.0001")
        ),
        billable_utilization_pct=billable_util,
    )
