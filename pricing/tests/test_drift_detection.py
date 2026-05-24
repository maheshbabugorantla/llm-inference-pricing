"""Tests for pricing.services.drift_detection — Tier 3 price drift detection service.

Business scenario: ComputePrices.com returns an H100 price for a Tier 3 provider.
The service compares that against the curated YAML-seeded price and writes a
PricingDriftAlert when the gap exceeds the 0.5% noise threshold.

All tests patch fetch_computeprices_gpu_prices at the service module boundary.
No real network calls are made.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from catalog.tests.factories import GPUFactory
from pricing.models import PricingDriftAlert, ReservedCapacityProduct
from pricing.services.drift_detection import check_tier3_drift
from pricing.tests.factories import ProviderFactory

_PATCH_TARGET = "pricing.services.drift_detection.fetch_computeprices_gpu_prices"


def _make_tier3_product(
    provider_slug: str,
    gpu_slug: str,
    *,
    per_active_hour_usd: str = "0",
    upfront_usd: str = "0",
    suffix: str = "",
) -> ReservedCapacityProduct:
    """Build a Tier 3 ReservedCapacityProduct in the DB.

    Uses no_upfront cadence when per_active_hour_usd > 0, all_upfront otherwise.
    Note: monthly_recurring_usd is left at 0 for simplicity — objects.create()
    does not call clean(), so validate_payment_cadence is not enforced here.
    """
    gpu = GPUFactory(slug=gpu_slug, vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(
        slug=provider_slug,
        provider_type="cloud",
        data_source_tier="manual_curation",
    )
    cadence = "no_upfront" if Decimal(per_active_hour_usd) > 0 else "all_upfront"
    return ReservedCapacityProduct.objects.create(
        slug=f"test-product-{provider_slug}{suffix}",
        display_name=f"Test Product {provider_slug}",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence=cadence,
        term_months=12,
        upfront_usd=Decimal(upfront_usd),
        monthly_recurring_usd=Decimal("0"),
        per_active_hour_usd=Decimal(per_active_hour_usd),
        listing_observed_at="2025-01-15",
    )


# ---------------------------------------------------------------------------
# Happy path: alert created when prices diverge
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tier3_h100_price_divergence_from_coreweave_creates_drift_alert() -> None:
    """When ComputePrices.com lists CoreWeave H100 at $3.20/GPU/hr and curated node rate is
    $16.00/node/hr (8 GPUs → $2.00/GPU/hr), a PricingDriftAlert is written with
    severity=critical and drift_pct=60.000."""
    product = _make_tier3_product(
        "coreweave",
        "nvidia-h100-sxm-80-drft1",
        per_active_hour_usd="16.0000",
        suffix="-drft1",
    )
    fake_rows = [{"provider": "coreweave", "hourly_usd": "3.20"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert len(alerts) == 1
    alert = PricingDriftAlert.objects.get(pk=alerts[0].pk)
    assert alert.provider == product.cloud_provider
    assert alert.gpu == product.gpu
    assert alert.tier == f"reserved-{product.slug}"
    assert alert.curated_usd_per_hour == Decimal("16.0000") / Decimal("8")
    assert alert.observed_usd_per_hour == Decimal("3.20")
    assert alert.drift_pct == Decimal("60.000")
    assert alert.severity == "critical"
    assert alert.drift_pct >= Decimal("0")


# ---------------------------------------------------------------------------
# Noise threshold: no alert when drift is negligible
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tier3_h100_price_within_noise_threshold_produces_no_alert() -> None:
    """When ComputePrices.com shows $2.005/GPU/hr vs curated node rate $16.00/node/hr
    (8 GPUs → $2.00/GPU/hr, 0.25% drift), no alert is created — within 0.5% noise floor."""
    _make_tier3_product(
        "coreweave",
        "nvidia-h100-sxm-80-noise1",
        per_active_hour_usd="16.0000",
        suffix="-noise1",
    )
    fake_rows = [{"provider": "coreweave", "hourly_usd": "2.005"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert alerts == []
    assert PricingDriftAlert.objects.count() == 0


@pytest.mark.django_db
def test_tier3_price_at_exact_noise_threshold_produces_no_alert() -> None:
    """A drift of exactly 0.5% must NOT create an alert — the threshold is inclusive.
    curated $2.00/GPU/hr, observed $2.01/GPU/hr = exactly 0.5% drift."""
    _make_tier3_product(
        "coreweave",
        "nvidia-h100-sxm-80-boundary1",
        per_active_hour_usd="16.0000",
        suffix="-boundary1",
    )
    fake_rows = [{"provider": "coreweave", "hourly_usd": "2.01"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert alerts == []
    assert PricingDriftAlert.objects.count() == 0


# ---------------------------------------------------------------------------
# Severity classification at each threshold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("curated_rate", "observed_rate", "expected_severity"),
    [
        # curated_rate is node-level per_active_hour_usd (8 GPUs → divide by 8 for per-GPU)
        # curated_rate=16.0000 → $2.00/GPU/hr; observed_rate is per-GPU from ComputePrices
        ("16.0000", "2.08", "info"),  # 4% drift → info
        ("16.0000", "2.12", "warning"),  # 6% drift → warning
        ("16.0000", "2.50", "critical"),  # 25% drift → critical
        ("16.0000", "1.60", "critical"),  # 20% drift → critical
    ],
)
@pytest.mark.django_db
def test_severity_classification_at_each_threshold(
    curated_rate: str,
    observed_rate: str,
    expected_severity: str,
) -> None:
    """Severity levels are assigned correctly at info/warning/critical boundaries.

    Thresholds: info < 5%, warning 5-20%, critical >= 20%.
    drift_pct is stored as absolute value (non-negative DB constraint).
    curated_rate is the node-level per_active_hour_usd; the service divides by gpus_per_node (8).
    """
    suffix = f"-{curated_rate.replace('.', '')}{observed_rate.replace('.', '')}"
    slug_suffix = suffix[:16]
    product = _make_tier3_product(
        f"coreweave{slug_suffix}",
        f"nvidia-h100-sxm-sev{slug_suffix}",
        per_active_hour_usd=curated_rate,
        suffix=slug_suffix,
    )
    fake_rows = [{"provider": product.cloud_provider.slug, "hourly_usd": observed_rate}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert len(alerts) == 1
    assert alerts[0].severity == expected_severity
    assert alerts[0].drift_pct >= Decimal("0")


# ---------------------------------------------------------------------------
# No match in aggregator
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_alert_when_no_matching_provider_in_aggregator() -> None:
    """When ComputePrices.com returns rows only for RunPod but the curated product
    is for CoreWeave, no alert is created — the provider slug does not match."""
    _make_tier3_product(
        "coreweave",
        "nvidia-h100-sxm-80-nomatch",
        per_active_hour_usd="16.0000",
        suffix="-nomatch",
    )
    fake_rows = [{"provider": "runpod", "hourly_usd": "1.99"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert alerts == []
    assert PricingDriftAlert.objects.count() == 0


# ---------------------------------------------------------------------------
# Filtering: inactive products and non-Tier-3 providers excluded
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tier3_drift_check_ignores_inactive_products() -> None:
    """Inactive ReservedCapacityProduct rows are excluded from drift checking."""
    product = _make_tier3_product(
        "coreweave",
        "nvidia-h100-sxm-80-inact2",
        per_active_hour_usd="16.0000",
        suffix="-inact2",
    )
    product.is_active = False
    product.save()
    fake_rows = [{"provider": "coreweave", "hourly_usd": "3.50"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert alerts == []


@pytest.mark.django_db
def test_tier3_drift_check_ignores_non_manual_curation_providers() -> None:
    """Products whose cloud_provider has data_source_tier != 'manual_curation' are excluded."""
    gpu = GPUFactory(slug="nvidia-h100-sxm-80-t1check", vram_gb=80, tdp_watts=700)
    tier1_provider = ProviderFactory(
        slug="runpod-t1-check",
        provider_type="cloud",
        data_source_tier="realtime_api",
    )
    ReservedCapacityProduct.objects.create(
        slug="runpod-h100-1yr-tier1-check",
        display_name="RunPod H100 1yr (Tier 1)",
        cloud_provider=tier1_provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="no_upfront",
        term_months=12,
        per_active_hour_usd=Decimal("2.0000"),
        monthly_recurring_usd=Decimal("0"),
        listing_observed_at="2025-01-15",
    )
    fake_rows = [{"provider": "runpod-t1-check", "hourly_usd": "3.99"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert alerts == []


# ---------------------------------------------------------------------------
# Curated rate: all_upfront uses 730h/month (aligned with reserved_cloud_cost.py)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tier3_upfront_only_product_amortises_over_full_term() -> None:
    """For all_upfront products, curated hourly rate is upfront_usd / (term_months * 730h) / gpus_per_node.
    730h/month matches the convention in reserved_cloud_cost.py and on_prem_cost.py.
    $87,600 upfront over 12 months / 8 GPUs -> $1.25/GPU/hr curated.
    Aggregator at $1.375/GPU/hr = 10% drift (warning)."""
    _make_tier3_product(
        "coreweave",
        "nvidia-h100-sxm-80-upfront1",
        upfront_usd="87600.00",
        suffix="-upfront1",
    )
    fake_rows = [{"provider": "coreweave", "hourly_usd": "1.375"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert len(alerts) == 1
    expected_curated = Decimal("87600.00") / (Decimal("12") * Decimal("730")) / Decimal("8")
    assert alerts[0].curated_usd_per_hour == expected_curated
    assert alerts[0].severity == "warning"


# ---------------------------------------------------------------------------
# Atomicity: no partial alerts on mid-loop error
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tier3_drift_is_atomic_no_partial_alerts_on_error() -> None:
    """If fetch raises mid-loop, no alerts are written — fetches complete before
    any DB writes begin, so a mid-fetch error means _write_drift_alerts is never
    called and no partial state is committed."""
    _make_tier3_product(
        "coreweave",
        "nvidia-h100-sxm-80-atm1",
        per_active_hour_usd="16.0000",
        suffix="-atm1",
    )
    _make_tier3_product(
        "crusoe",
        "nvidia-h100-sxm-80-atm2",
        per_active_hour_usd="16.0000",
        suffix="-atm2",
    )

    # coreweave < crusoe alphabetically; queryset is ordered by cloud_provider__slug
    # so call 1 is always coreweave (produces alert) and call 2 is crusoe (raises).
    call_count = 0

    def _fake_fetch(gpu_slug: str) -> list[dict]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [{"provider": "coreweave", "hourly_usd": "3.50"}]
        raise RuntimeError("simulated transient fetch failure")

    with pytest.raises(RuntimeError, match="simulated transient fetch failure"):
        with patch(_PATCH_TARGET, side_effect=_fake_fetch):
            check_tier3_drift()

    assert PricingDriftAlert.objects.count() == 0


# ---------------------------------------------------------------------------
# capacity_block products excluded from drift check
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tier3_partial_upfront_product_is_excluded_from_drift_check() -> None:
    """partial_upfront products have both upfront_usd and monthly_recurring_usd.
    Computing a curated rate that ignores monthly_recurring_usd would understate
    the true committed rate and generate misleading drift alerts. Excluded until
    the service handles both payment components."""
    gpu = GPUFactory(slug="nvidia-h100-sxm-80-partup", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(
        slug="coreweave-partup-test",
        provider_type="cloud",
        data_source_tier="manual_curation",
    )
    ReservedCapacityProduct.objects.create(
        slug="coreweave-h100-partial-upfront-test",
        display_name="CoreWeave H100 Partial Upfront Test",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="partial_upfront",
        term_months=12,
        upfront_usd=Decimal("20000.00"),
        monthly_recurring_usd=Decimal("5000.00"),
        listing_observed_at="2025-01-15",
    )
    fake_rows = [{"provider": "coreweave-partup-test", "hourly_usd": "3.50"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert alerts == []
    assert PricingDriftAlert.objects.count() == 0


@pytest.mark.django_db
def test_tier3_capacity_block_product_is_excluded_from_drift_check() -> None:
    """capacity_block products have no comparable hourly rate derivable from
    per_active_hour_usd or upfront_usd — they are excluded from drift checking
    to avoid misleading alerts based on a bad approximation."""
    gpu = GPUFactory(slug="nvidia-h100-sxm-80-capblk", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(
        slug="aws-capblk-test",
        provider_type="cloud",
        data_source_tier="manual_curation",
    )
    ReservedCapacityProduct.objects.create(
        slug="aws-p5-capacity-block-test",
        display_name="AWS p5 Capacity Block Test",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="capacity_block",
        term_months=1,
        block_duration_hours=336,
        capacity_block_total_usd=Decimal("65856.00"),
        listing_observed_at="2025-01-15",
    )
    fake_rows = [{"provider": "aws-capblk-test", "hourly_usd": "150.00"}]

    with patch(_PATCH_TARGET, return_value=fake_rows):
        alerts = check_tier3_drift()

    assert alerts == []
    assert PricingDriftAlert.objects.count() == 0
