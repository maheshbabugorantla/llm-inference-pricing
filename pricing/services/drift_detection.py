"""Drift detection service for Tier 3 (manually curated) provider pricing.

Fetches current GPU prices from ComputePrices.com and creates PricingDriftAlert
records when observed prices diverge from curated YAML values.

NOTE: fetch_computeprices_table raises NotImplementedError until TOS is verified.
In tests, patch 'pricing.services.drift_detection.fetch_computeprices_table' at the
network boundary.

Curated rate approximation: for all_upfront products, divides upfront_usd by 720
(30-day month x 24h) as a rough hourly equivalent. This understates the effective
committed rate for multi-month terms. The exact rate requires a ReservedCloudDeployment
and compute_reserved_cloud_cost(); that dependency is deferred until the service
proves useful in production.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction

from pricing.models import PricingDriftAlert, ReservedCapacityProduct
from pricing.scrapers.computeprices import fetch_computeprices_table

logger = logging.getLogger("pricing.services.drift_detection")

_NOISE_THRESHOLD = Decimal("0.5")
_WARNING_THRESHOLD = Decimal("5")
_CRITICAL_THRESHOLD = Decimal("20")
_HOURS_PER_MONTH = Decimal("720")


def _curated_hourly_rate(product: ReservedCapacityProduct) -> Decimal:
    """Derive an effective hourly rate from curated product fields.

    Priority:
      1. per_active_hour_usd > 0: use directly.
      2. upfront_usd > 0: divide by 720 as a single-month rough approximation.
      3. Both zero: raise ValueError — cannot compute drift without a curated rate.
    """
    if product.per_active_hour_usd > Decimal("0"):
        return product.per_active_hour_usd
    if product.upfront_usd > Decimal("0"):
        return product.upfront_usd / _HOURS_PER_MONTH
    raise ValueError(
        f"ReservedCapacityProduct '{product.slug}' has per_active_hour_usd=0 and "
        "upfront_usd=0 — cannot derive a curated hourly rate for drift comparison"
    )


def _classify_severity(abs_drift_pct: Decimal) -> str:
    if abs_drift_pct >= _CRITICAL_THRESHOLD:
        return "critical"
    if abs_drift_pct >= _WARNING_THRESHOLD:
        return "warning"
    return "info"


@transaction.atomic
def check_tier3_drift() -> list[PricingDriftAlert]:
    """For each active Tier 3 ReservedCapacityProduct, compare curated price against
    ComputePrices.com and create a PricingDriftAlert when divergence exceeds 0.5%.

    Returns the list of PricingDriftAlert instances created in this run.

    Raises:
        NotImplementedError: propagated from fetch_computeprices_table when TOS is
            not yet verified (expected until explicitly enabled in production).
    """
    alerts_created: list[PricingDriftAlert] = []

    tier3_products = (
        ReservedCapacityProduct.objects.filter(
            cloud_provider__data_source_tier="manual_curation",
            is_active=True,
        )
        .exclude(payment_cadence="capacity_block")
        .select_related("cloud_provider", "gpu")
    )

    for product in tier3_products:
        observed_rows = fetch_computeprices_table(product.gpu.slug)
        # fetch_computeprices_table returns rows already normalized by
        # parse_computeprices_table — r["provider"] is already our slug
        matching = [r for r in observed_rows if r["provider"] == product.cloud_provider.slug]
        if not matching:
            logger.debug(
                "No ComputePrices.com row for gpu=%s provider=%s — skipping",
                product.gpu.slug,
                product.cloud_provider.slug,
            )
            continue

        try:
            observed = Decimal(str(matching[0]["hourly_usd"]))
            if not observed.is_finite():
                raise InvalidOperation("non-finite value")
        except InvalidOperation:
            logger.warning(
                "Non-numeric or non-finite hourly_usd from ComputePrices.com "
                "for gpu=%s provider=%s — skipping",
                product.gpu.slug,
                product.cloud_provider.slug,
            )
            continue

        try:
            curated = _curated_hourly_rate(product)
        except ValueError:
            logger.warning(
                "Cannot derive curated rate for product=%s — skipping drift check",
                product.slug,
            )
            continue

        signed_pct = (observed - curated) / curated * Decimal("100")
        abs_pct = abs(signed_pct).quantize(Decimal("0.001"))

        if abs_pct < _NOISE_THRESHOLD:
            continue

        severity = _classify_severity(abs_pct)
        alert = PricingDriftAlert.objects.create(
            provider=product.cloud_provider,
            gpu=product.gpu,
            tier=f"reserved-{product.slug}",
            curated_usd_per_hour=curated,
            observed_usd_per_hour=observed,
            drift_pct=abs_pct,
            source_url="https://computeprices.com/" + product.gpu.slug,
            severity=severity,
        )
        alerts_created.append(alert)
        logger.info(
            "Drift alert created: product=%s severity=%s drift=%.3f%%",
            product.slug,
            severity,
            abs_pct,
        )

    return alerts_created
