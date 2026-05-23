"""Service-level validation for ReservedCapacityProduct payment cadence rules.

Extracted from the model so business logic lives in services/, not models.py.
Called by ReservedCapacityProduct.clean() and can be invoked directly in tests
or the seed command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

if TYPE_CHECKING:
    from pricing.models import ReservedCapacityProduct


def validate_payment_cadence(product: ReservedCapacityProduct) -> None:
    """Enforce per-cadence field constraints.

    Raises ValidationError if the product's payment_cadence fields are
    inconsistent (e.g. all_upfront with a non-zero monthly_recurring_usd).
    """
    cadence = product.payment_cadence

    if cadence == "all_upfront":
        if (
            product.monthly_recurring_usd != 0
            or product.per_active_hour_usd != 0
            or product.capacity_block_total_usd != 0
        ):
            raise ValidationError(
                "all_upfront requires monthly_recurring_usd=0, per_active_hour_usd=0, "
                "and capacity_block_total_usd=0"
            )
        if product.upfront_usd <= 0:
            raise ValidationError("all_upfront requires upfront_usd > 0")

    elif cadence == "partial_upfront":
        if product.capacity_block_total_usd != 0:
            raise ValidationError("partial_upfront requires capacity_block_total_usd=0")
        if product.upfront_usd <= 0:
            raise ValidationError("partial_upfront requires upfront_usd > 0")
        if product.monthly_recurring_usd <= 0:
            raise ValidationError("partial_upfront requires monthly_recurring_usd > 0")

    elif cadence == "no_upfront":
        if product.upfront_usd != 0 or product.capacity_block_total_usd != 0:
            raise ValidationError(
                "no_upfront requires upfront_usd=0 and capacity_block_total_usd=0"
            )
        if product.monthly_recurring_usd <= 0:
            raise ValidationError("no_upfront requires monthly_recurring_usd > 0")

    elif cadence == "capacity_block":
        if (
            product.upfront_usd != 0
            or product.monthly_recurring_usd != 0
            or product.per_active_hour_usd != 0
        ):
            raise ValidationError(
                "capacity_block requires upfront_usd=0, monthly_recurring_usd=0, "
                "and per_active_hour_usd=0"
            )
        if product.capacity_block_total_usd <= 0:
            raise ValidationError("capacity_block requires capacity_block_total_usd > 0")
        if not product.block_duration_hours:
            raise ValidationError("capacity_block requires block_duration_hours > 0")
