"""Tests for PricingDriftAlert model (M10.T01).

Business scenario: the drift detection pipeline observes that a provider's
scraped GPU price has diverged from the curated baseline. These tests prove
that alerts are stored with correct severity classification, are ordered
newest-first for the ops dashboard, and can be acknowledged without deleting
history.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db import models as django_models
from django.test import TestCase
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.models import PricingDriftAlert
from pricing.tests.factories import PricingDriftAlertFactory, ProviderFactory


class PricingDriftAlertHappyPathTest(TestCase):
    """Happy path: alert persists with correct field values."""

    def test_aws_h100_on_demand_drift_alert_saves_with_correct_fields(self):
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

        self.assertEqual(alert.curated_usd_per_hour, Decimal("2.0000"))
        self.assertEqual(alert.observed_usd_per_hour, Decimal("2.2000"))
        self.assertEqual(alert.drift_pct, Decimal("10.000"))
        self.assertEqual(alert.severity, "warning")
        self.assertEqual(alert.source_url, "https://aws.amazon.com/ec2/pricing/")
        self.assertIsNotNone(alert.detected_at)
        self.assertIsNotNone(alert.detected_at.tzinfo)


class PricingDriftAlertAcknowledgementTest(TestCase):
    """acknowledged_at is null by default."""

    def test_new_drift_alert_is_unacknowledged_by_default(self):
        """A freshly created drift alert must have acknowledged_at=None -- it has
        not been reviewed by an operator. The ops dashboard queries on this field."""
        alert = PricingDriftAlertFactory()
        alert.refresh_from_db()
        self.assertIsNone(alert.acknowledged_at)

    def test_acknowledging_drift_alert_sets_acknowledged_at(self):
        """Setting acknowledged_at on an alert records the review timestamp,
        allowing the ops dashboard to distinguish open from resolved alerts."""
        alert = PricingDriftAlertFactory()
        before = timezone.now()
        alert.acknowledged_at = timezone.now()
        alert.save()
        alert.refresh_from_db()

        self.assertIsNotNone(alert.acknowledged_at)
        self.assertGreaterEqual(alert.acknowledged_at, before)
        self.assertIsNotNone(alert.acknowledged_at.tzinfo)


class PricingDriftAlertSeverityTest(TestCase):
    """Severity boundary values."""

    def test_drift_alert_severity_boundary_values_persist_correctly(self):
        """Drift alerts store whichever severity the caller assigns; the boundary
        thresholds (info < 5%, warning 5-20%, critical >= 20%) must round-trip
        through the DB without coercion.
        """
        cases = [
            (Decimal("0.000"), "info"),
            (Decimal("4.999"), "info"),
            (Decimal("5.000"), "warning"),
            (Decimal("19.999"), "warning"),
            (Decimal("20.000"), "critical"),
            (Decimal("50.000"), "critical"),
        ]
        for drift_pct, expected_severity in cases:
            with self.subTest(drift_pct=drift_pct, expected_severity=expected_severity):
                alert = PricingDriftAlertFactory(
                    drift_pct=drift_pct,
                    severity=expected_severity,
                )
                alert.refresh_from_db()
                self.assertEqual(alert.drift_pct, drift_pct)
                self.assertEqual(alert.severity, expected_severity)


class PricingDriftAlertOrderingTest(TestCase):
    """Ordering: newer alerts surface first."""

    def test_drift_alerts_are_ordered_newest_first(self):
        """The ops dashboard must see the most recent drift alerts at the top.
        Meta.ordering = ('-detected_at') enforces this at the ORM level."""
        provider = ProviderFactory(slug="gcp-drift-order", provider_type="cloud")
        gpu = GPUFactory(slug="nvidia-a100-drift-order", vram_gb=80, tdp_watts=400)

        older = PricingDriftAlertFactory(provider=provider, gpu=gpu, severity="info")
        newer = PricingDriftAlertFactory(provider=provider, gpu=gpu, severity="critical")

        old_time = timezone.make_aware(timezone.datetime(2024, 1, 1))
        new_time = timezone.make_aware(timezone.datetime(2025, 1, 1))
        PricingDriftAlert.objects.filter(pk=older.pk).update(detected_at=old_time)
        PricingDriftAlert.objects.filter(pk=newer.pk).update(detected_at=new_time)

        alerts = list(PricingDriftAlert.objects.filter(provider=provider, gpu=gpu))

        self.assertEqual(alerts[0].pk, newer.pk, "Newer alert must appear first")
        self.assertEqual(alerts[1].pk, older.pk, "Older alert must appear second")


class PricingDriftAlertDecimalTest(TestCase):
    """Decimal precision round-trips."""

    def test_drift_alert_decimal_fields_are_decimal_not_float(self):
        """All money and rate fields must return Decimal, never float -- SHARED.md invariant."""
        alert = PricingDriftAlertFactory(
            curated_usd_per_hour=Decimal("3.2500"),
            observed_usd_per_hour=Decimal("3.9000"),
            drift_pct=Decimal("20.000"),
        )
        alert.refresh_from_db()

        self.assertIsInstance(alert.curated_usd_per_hour, Decimal)
        self.assertIsInstance(alert.observed_usd_per_hour, Decimal)
        self.assertIsInstance(alert.drift_pct, Decimal)


class PricingDriftAlertFKProtectionTest(TestCase):
    """FK protection: deleting a provider or GPU must be blocked."""

    def test_deleting_provider_with_drift_alert_is_blocked(self):
        """Provider FK is PROTECT -- deleting a provider that has alerts must raise."""
        alert = PricingDriftAlertFactory()
        provider_pk = alert.provider.pk

        with self.assertRaises(django_models.ProtectedError):
            alert.provider.__class__.objects.filter(pk=provider_pk).delete()

    def test_deleting_gpu_with_drift_alert_is_blocked(self):
        """GPU FK is PROTECT -- deleting a GPU that has alerts must raise."""
        alert = PricingDriftAlertFactory()
        gpu_pk = alert.gpu.pk

        with self.assertRaises(django_models.ProtectedError):
            alert.gpu.__class__.objects.filter(pk=gpu_pk).delete()


class PricingDriftAlertConstraintTest(TestCase):
    """Failure modes: constraint enforcement."""

    def test_negative_drift_pct_is_rejected_by_db_constraint(self):
        """drift_pct must be non-negative -- the DB constraint must fire on negative values."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PricingDriftAlertFactory(drift_pct=Decimal("-1.000"))

    def test_negative_curated_usd_per_hour_is_rejected_by_db_constraint(self):
        """curated_usd_per_hour must be non-negative -- a negative curated rate is corrupt data."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PricingDriftAlertFactory(curated_usd_per_hour=Decimal("-0.0001"))

    def test_negative_observed_usd_per_hour_is_rejected_by_db_constraint(self):
        """observed_usd_per_hour must be non-negative -- a negative observed rate is corrupt data."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PricingDriftAlertFactory(observed_usd_per_hour=Decimal("-0.0001"))

    def test_invalid_severity_choice_is_not_enforced_at_db_level(self):
        """Django choices= is form/admin validation only -- the DB accepts any string in severity."""
        alert = PricingDriftAlertFactory(severity="extreme")
        alert.refresh_from_db()
        self.assertEqual(alert.severity, "extreme")
