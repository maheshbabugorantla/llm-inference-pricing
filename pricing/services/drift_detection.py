"""Drift detection service for Tier 3 (manually curated) provider pricing.

Fetches current GPU prices from the ComputePrices.com REST API and creates
PricingDriftAlert records when observed prices diverge from curated YAML values.

In tests, patch 'pricing.services.drift_detection.fetch_computeprices_gpu_prices'
at the network boundary.

Curated rate approximation: for all_upfront products, amortises upfront_usd over
term_months x 730h then divides by gpus_per_node to get a per-GPU hourly rate.
The exact rate requires a ReservedCloudDeployment and compute_reserved_cloud_cost();
that dependency is deferred until the service proves useful in production.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import transaction

from pricing.models import PricingDriftAlert, ReservedCapacityProduct
from pricing.scrapers.computeprices import BASE_URL, GPU_SLUG_MAP, fetch_computeprices_gpu_prices

logger = logging.getLogger("pricing.services.drift_detection")

_NOISE_THRESHOLD = Decimal("0.5")
_WARNING_THRESHOLD = Decimal("5")
_CRITICAL_THRESHOLD = Decimal("20")
_HOURS_PER_MONTH = Decimal("730")


def _curated_hourly_rate(product: ReservedCapacityProduct) -> Decimal:
    """Derive an effective per-GPU hourly rate from curated product fields.

    per_active_hour_usd and upfront_usd are node-level charges covering all GPUs in the node.
    We divide by gpus_per_node to obtain a per-GPU rate that is comparable to
    ComputePrices API's price_per_hour_usd (which is per-GPU).

    Priority:
      1. per_active_hour_usd > 0: divide by gpus_per_node to get per-GPU rate.
      2. upfront_usd > 0: amortise over the full term (term_months x 730h), then divide by gpus_per_node.
      3. Both zero: raise ValueError — cannot compute drift without a curated rate.
    """
    gpus = Decimal(product.gpus_per_node)
    if product.per_active_hour_usd > Decimal("0"):
        return product.per_active_hour_usd / gpus
    if product.upfront_usd > Decimal("0"):
        term_hours = Decimal(product.term_months) * _HOURS_PER_MONTH
        return (product.upfront_usd / term_hours) / gpus
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


def check_tier3_drift() -> list[PricingDriftAlert]:
    """For each active Tier 3 ReservedCapacityProduct, compare curated price against
    ComputePrices.com and create a PricingDriftAlert when divergence exceeds 0.5%.

    HTTP fetches and drift calculations happen outside any DB transaction to avoid
    holding an open connection during network I/O. DB writes are wrapped in a single
    atomic block at the end so alerts are created all-or-nothing.

    Returns the list of PricingDriftAlert instances created in this run.

    Raises:
        ParserDriftError: if the ComputePrices API response is malformed.
        httpx.HTTPStatusError: on non-2xx responses from the ComputePrices API.
    """
    tier3_products = list(
        ReservedCapacityProduct.objects.filter(
            cloud_provider__data_source_tier="manual_curation",
            is_active=True,
        )
        .exclude(payment_cadence__in=["capacity_block", "partial_upfront"])
        .select_related("cloud_provider", "gpu")
    )

    # Phase 1: fetch + compute outside any transaction — avoids holding a DB
    # connection open during HTTP calls to the ComputePrices API.
    _gpu_price_cache: dict[str, list[dict[str, str]]] = {}
    alert_specs: list[dict[str, Any]] = []

    for product in tier3_products:
        gpu_slug = product.gpu.slug
        if gpu_slug not in _gpu_price_cache:
            _gpu_price_cache[gpu_slug] = fetch_computeprices_gpu_prices(gpu_slug)
        observed_rows = _gpu_price_cache[gpu_slug]
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
        abs_pct_exact = abs(signed_pct)

        if abs_pct_exact <= _NOISE_THRESHOLD:
            continue

        abs_pct = abs_pct_exact.quantize(Decimal("0.001"))
        their_slug = GPU_SLUG_MAP.get(product.gpu.slug, product.gpu.slug)
        source_url = (
            matching[0].get("source_url") or f"{BASE_URL}/gpu-prices?gpu={their_slug}&pricing_type=on_demand"
        )
        alert_specs.append(
            {
                "provider": product.cloud_provider,
                "gpu": product.gpu,
                "tier": f"reserved-{product.slug}",
                "curated_usd_per_hour": curated.quantize(Decimal("0.0001")),
                "observed_usd_per_hour": observed.quantize(Decimal("0.0001")),
                "drift_pct": abs_pct,
                "source_url": source_url,
                "severity": _classify_severity(abs_pct_exact),
                "_product_slug": product.slug,
            }
        )

    # Phase 2: atomic DB writes — all alerts created or none.
    return _write_drift_alerts(alert_specs)


@transaction.atomic
def _write_drift_alerts(alert_specs: list[dict[str, Any]]) -> list[PricingDriftAlert]:
    alerts_created: list[PricingDriftAlert] = []
    for spec in alert_specs:
        product_slug = spec["_product_slug"]
        create_kwargs = {k: v for k, v in spec.items() if k != "_product_slug"}
        alert = PricingDriftAlert.objects.create(**create_kwargs)
        alerts_created.append(alert)
        logger.info(
            "Drift alert created: product=%s severity=%s drift=%s%%",
            product_slug,
            alert.severity,
            alert.drift_pct,
        )
    return alerts_created
