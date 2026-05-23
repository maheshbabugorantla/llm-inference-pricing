"""Tests for PricingDriftAlert model (M10.T01).

Business scenario: the drift detection pipeline observes that a provider's
scraped GPU price has diverged from the curated baseline. These tests prove
that alerts are stored with correct severity classification, are ordered
newest-first for the ops dashboard, and can be acknowledged without deleting
history.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.models import PricingDriftAlert
from pricing.tests.factories import PricingDriftAlertFactory, ProviderFactory

# ---------------------------------------------------------------------------
# Happy path: alert persists with correct field values
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_aws_h100_on_demand_drift_alert_saves_with_correct_fields():
    """A drift alert for AWS H100 on-demand pricing saves to DB with curated
    and observed rates, drift_pct, severity, and a timezone-aware detected_at."""
    provider = ProviderFactory(slug="aws-drift-test", provider_type="cloud")
    gpu = GPUFactory(slug="nvidia-h100-sxm-drift", vram_gb=80, tdp_watts=700)

    alert = PricingDriftAlert.objects.create(
        provider=provider,
        gpu=gpu,
        tier="on_demand",
        curated_usd_per_hour=Decimal("2.0000"),
        observed_usd_per_hour=Decimal("2.2000"),
        drift_pct=Decimal("10.000"),
        source_url="https://aws.amazon.com/ec2/pricing/",
        severity="warning",
    )
    alert.refresh_from_db()

    assert alert.curated_usd_per_hour == Decimal("2.0000")
    assert alert.observed_usd_per_hour == Decimal("2.2000")
    assert alert.drift_pct == Decimal("10.000")
    assert alert.severity == "warning"
    assert alert.source_url == "https://aws.amazon.com/ec2/pricing/"
    assert alert.detected_at is not None
    assert alert.detected_at.tzinfo is not None


# ---------------------------------------------------------------------------
# acknowledged_at is null by default
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_new_drift_alert_is_unacknowledged_by_default():
    """A freshly created drift alert must have acknowledged_at=None — it has
    not been reviewed by an operator. The ops dashboard queries on this field."""
    alert = PricingDriftAlertFactory()
    alert.refresh_from_db()

    assert alert.acknowledged_at is None


@pytest.mark.django_db
def test_acknowledging_drift_alert_sets_acknowledged_at():
    """Setting acknowledged_at on an alert records the review timestamp,
    allowing the ops dashboard to distinguish open from resolved alerts."""
    alert = PricingDriftAlertFactory()
    before = timezone.now()
    alert.acknowledged_at = timezone.now()
    alert.save()
    alert.refresh_from_db()

    assert alert.acknowledged_at is not None
    assert alert.acknowledged_at >= before
    assert alert.acknowledged_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Severity boundary values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "drift_pct,expected_severity",
    [
        (Decimal("0.000"), "info"),
        (Decimal("4.999"), "info"),
        (Decimal("5.000"), "warning"),
        (Decimal("19.999"), "warning"),
        (Decimal("20.000"), "critical"),
        (Decimal("50.000"), "critical"),
    ],
)
@pytest.mark.django_db
def test_drift_alert_severity_boundary_values_persist_correctly(
    drift_pct: Decimal, expected_severity: str
) -> None:
    """Drift alerts store whichever severity the caller assigns; the boundary
    thresholds (info < 5%, warning 5-20%, critical >= 20%) must round-trip
    through the DB without coercion.

    Severity classification logic lives in pricing/services/drift_detection.py.
    This test proves the field accepts and preserves all three severity values
    at the precise drift_pct thresholds used by the service.
    """
    alert = PricingDriftAlertFactory(
        drift_pct=drift_pct,
        severity=expected_severity,
    )
    alert.refresh_from_db()

    assert alert.drift_pct == drift_pct
    assert alert.severity == expected_severity


# ---------------------------------------------------------------------------
# Ordering: newer alerts surface first
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_drift_alerts_are_ordered_newest_first():
    """The ops dashboard must see the most recent drift alerts at the top.
    Meta.ordering = ('-detected_at') enforces this at the ORM level."""
    provider = ProviderFactory(slug="gcp-drift-order", provider_type="cloud")
    gpu = GPUFactory(slug="nvidia-a100-drift-order", vram_gb=80, tdp_watts=400)

    older = PricingDriftAlertFactory(provider=provider, gpu=gpu, severity="info")
    newer = PricingDriftAlertFactory(provider=provider, gpu=gpu, severity="critical")

    old_time = timezone.now().replace(year=2024, month=1, day=1)
    new_time = timezone.now().replace(year=2025, month=1, day=1)
    PricingDriftAlert.objects.filter(pk=older.pk).update(detected_at=old_time)
    PricingDriftAlert.objects.filter(pk=newer.pk).update(detected_at=new_time)

    alerts = list(PricingDriftAlert.objects.filter(provider=provider, gpu=gpu))

    assert alerts[0].pk == newer.pk, "Newer alert must appear first"
    assert alerts[1].pk == older.pk, "Older alert must appear second"


# ---------------------------------------------------------------------------
# Decimal precision round-trips
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_drift_alert_decimal_fields_are_decimal_not_float():
    """All money and rate fields must return Decimal, never float — SHARED.md invariant."""
    alert = PricingDriftAlertFactory(
        curated_usd_per_hour=Decimal("3.2500"),
        observed_usd_per_hour=Decimal("3.9000"),
        drift_pct=Decimal("20.000"),
    )
    alert.refresh_from_db()

    assert isinstance(alert.curated_usd_per_hour, Decimal)
    assert isinstance(alert.observed_usd_per_hour, Decimal)
    assert isinstance(alert.drift_pct, Decimal)


# ---------------------------------------------------------------------------
# FK protection: deleting a provider or GPU must be blocked
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_deleting_provider_with_drift_alert_is_blocked():
    """Provider FK is PROTECT — deleting a provider that has alerts must raise,
    preserving alert history for audit purposes."""
    from django.db import models as django_models

    alert = PricingDriftAlertFactory()
    provider_pk = alert.provider.pk

    with pytest.raises(django_models.ProtectedError):
        alert.provider.__class__.objects.filter(pk=provider_pk).delete()


@pytest.mark.django_db
def test_deleting_gpu_with_drift_alert_is_blocked():
    """GPU FK is PROTECT — deleting a GPU that has alerts must raise."""
    from django.db import models as django_models

    alert = PricingDriftAlertFactory()
    gpu_pk = alert.gpu.pk

    with pytest.raises(django_models.ProtectedError):
        alert.gpu.__class__.objects.filter(pk=gpu_pk).delete()


# ---------------------------------------------------------------------------
# Failure modes: constraint enforcement
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_negative_drift_pct_is_rejected_by_db_constraint():
    """drift_pct must be non-negative — the DB constraint must fire on negative values.

    The drift detection service always computes abs(drift), but if a bug produces
    a negative value the constraint must block it from persisting silently."""
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        PricingDriftAlertFactory(drift_pct=Decimal("-1.000"))


@pytest.mark.django_db
def test_negative_curated_usd_per_hour_is_rejected_by_db_constraint():
    """curated_usd_per_hour must be non-negative — a negative curated rate is corrupt data."""
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        PricingDriftAlertFactory(curated_usd_per_hour=Decimal("-0.0001"))


@pytest.mark.django_db
def test_negative_observed_usd_per_hour_is_rejected_by_db_constraint():
    """observed_usd_per_hour must be non-negative — a negative observed rate is corrupt data."""
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        PricingDriftAlertFactory(observed_usd_per_hour=Decimal("-0.0001"))


@pytest.mark.django_db
def test_invalid_severity_choice_is_not_enforced_at_db_level():
    """Django choices= is form/admin validation only — the DB accepts any string in severity.

    This documents a known gap: to reject invalid severity at the DB level, a
    CheckConstraint would be needed. The service layer is responsible for only
    passing valid severity values."""
    alert = PricingDriftAlertFactory(severity="extreme")
    alert.refresh_from_db()

    assert alert.severity == "extreme"
