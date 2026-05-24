"""Tests for pricing admin — registration and actions.

Business scenario: ops team reviews drift alerts in Django admin, marks them
acknowledged once investigated, and filters by severity to triage quickly.
"""

from __future__ import annotations

from django.contrib.admin.sites import site
from django.test import TestCase
from django.utils import timezone

from pricing.models import PricingDriftAlert, PricingSnapshot, Provider
from pricing.tests.factories import PricingDriftAlertFactory


def _get_acknowledged_action(model_admin):
    """Return the mark-acknowledged action function from the admin registry."""
    for action in model_admin.actions or []:
        name = action if isinstance(action, str) else getattr(action, "__name__", str(action))
        if "acknowledged" in name.lower():
            return model_admin.get_action(action)[0] if isinstance(action, str) else action
    return None


class PricingAdminRegistrationTest(TestCase):
    def test_pricing_models_registered_in_admin(self) -> None:
        self.assertTrue(site.is_registered(Provider))
        self.assertTrue(site.is_registered(PricingSnapshot))

    def test_pricing_drift_alert_registered_in_admin(self) -> None:
        """PricingDriftAlert must be visible in admin so ops can review and triage alerts."""
        self.assertTrue(site.is_registered(PricingDriftAlert))

    def test_pricing_drift_alert_admin_has_expected_list_display(self) -> None:
        """list_display must expose all columns ops needs to triage alerts at a glance."""
        model_admin = site._registry[PricingDriftAlert]
        expected = {
            "detected_at",
            "provider",
            "gpu",
            "tier",
            "curated_usd_per_hour",
            "observed_usd_per_hour",
            "drift_pct",
            "severity",
            "acknowledged_at",
        }
        self.assertTrue(expected.issubset(set(model_admin.list_display)))

    def test_pricing_drift_alert_admin_has_mark_acknowledged_action(self) -> None:
        """The 'mark acknowledged' admin action must be registered so ops can bulk-triage alerts."""
        model_admin = site._registry[PricingDriftAlert]
        action_fn = _get_acknowledged_action(model_admin)
        self.assertIsNotNone(action_fn, "Could not locate mark-acknowledged action")


class PricingDriftAlertAdminActionTest(TestCase):
    def test_mark_acknowledged_action_sets_acknowledged_at_on_selected_alerts(self) -> None:
        """Applying 'mark acknowledged' to a queryset must stamp acknowledged_at = now()
        for each selected alert. Unselected alerts are not touched."""
        alert1 = PricingDriftAlertFactory()
        alert2 = PricingDriftAlertFactory()
        unrelated = PricingDriftAlertFactory()

        model_admin = site._registry[PricingDriftAlert]
        action_fn = _get_acknowledged_action(model_admin)
        self.assertIsNotNone(action_fn, "Could not locate mark-acknowledged action")

        before = timezone.now()
        qs = PricingDriftAlert.objects.filter(pk__in=[alert1.pk, alert2.pk])
        action_fn(model_admin, None, qs)

        alert1.refresh_from_db()
        alert2.refresh_from_db()
        unrelated.refresh_from_db()

        self.assertIsNotNone(alert1.acknowledged_at)
        self.assertGreaterEqual(alert1.acknowledged_at, before)
        self.assertIsNotNone(alert2.acknowledged_at)
        self.assertIsNone(unrelated.acknowledged_at)
        self.assertGreaterEqual(alert1.updated_at, before)
        self.assertGreaterEqual(alert2.updated_at, before)

    def test_mark_acknowledged_action_does_not_overwrite_existing_acknowledged_at(self) -> None:
        """Re-running 'mark acknowledged' on an already-acknowledged alert must not
        overwrite the original acknowledgement timestamp — that timestamp is audit history."""
        original_time = timezone.make_aware(timezone.datetime(2024, 6, 1, 12, 0, 0))
        already_acked = PricingDriftAlertFactory(acknowledged_at=original_time)
        unacked = PricingDriftAlertFactory()

        model_admin = site._registry[PricingDriftAlert]
        action_fn = _get_acknowledged_action(model_admin)
        self.assertIsNotNone(action_fn)

        before = timezone.now()
        qs = PricingDriftAlert.objects.filter(pk__in=[already_acked.pk, unacked.pk])
        action_fn(model_admin, None, qs)

        already_acked.refresh_from_db()
        unacked.refresh_from_db()

        self.assertEqual(
            already_acked.acknowledged_at, original_time, "Existing acknowledged_at must not be overwritten"
        )
        self.assertLess(
            already_acked.updated_at, before, "updated_at of already-acknowledged alert must not be touched"
        )
        self.assertIsNotNone(unacked.acknowledged_at, "Unacknowledged alert must be stamped")
