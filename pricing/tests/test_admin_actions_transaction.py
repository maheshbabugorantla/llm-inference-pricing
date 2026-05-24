"""Transaction-isolated admin action tests using Django TestCase.

The _mark_drift_alerts_acknowledged action mutates PricingDriftAlert rows via
queryset.update(). TestCase is used with setUpTestData so the shared alert rows
are created once for the class, and each test method runs in its own savepoint
— mutations made by one test are invisible to the next.
"""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from pricing.admin import _mark_drift_alerts_acknowledged
from pricing.models import PricingDriftAlert
from pricing.tests.factories import PricingDriftAlertFactory


class DriftAlertAcknowledgeTest(TestCase):
    """Tests for the _mark_drift_alerts_acknowledged admin action.

    setUpTestData creates three alerts: two unacknowledged and one already
    acknowledged with a known timestamp. Each test runs in its own savepoint
    so the action's .update() is rolled back before the next test.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.unacked_1 = PricingDriftAlertFactory(acknowledged_at=None, severity="critical")
        cls.unacked_2 = PricingDriftAlertFactory(acknowledged_at=None, severity="warning")
        cls.original_ack_time = timezone.make_aware(timezone.datetime(2024, 6, 1, 12, 0, 0))
        cls.already_acked = PricingDriftAlertFactory(acknowledged_at=cls.original_ack_time, severity="info")

    def _run_action(self, queryset: object) -> None:
        """Call the admin action function directly, bypassing HTTP."""
        from unittest.mock import MagicMock

        _mark_drift_alerts_acknowledged(modeladmin=MagicMock(), request=MagicMock(), queryset=queryset)

    def test_action_stamps_acknowledged_at_on_every_selected_alert(self) -> None:
        """Selecting both unacknowledged alerts and running the action must stamp
        acknowledged_at = now() on each. The timestamp proves when ops investigated
        the alert — it must be set to a value >= the call time."""
        before = timezone.now()
        qs = PricingDriftAlert.objects.filter(pk__in=[self.unacked_1.pk, self.unacked_2.pk])
        self._run_action(qs)

        self.unacked_1.refresh_from_db()
        self.unacked_2.refresh_from_db()

        self.assertIsNotNone(self.unacked_1.acknowledged_at)
        self.assertGreaterEqual(self.unacked_1.acknowledged_at, before)
        self.assertIsNotNone(self.unacked_2.acknowledged_at)
        self.assertGreaterEqual(self.unacked_2.acknowledged_at, before)

    def test_action_does_not_touch_alerts_outside_the_selected_queryset(self) -> None:
        """Only the alerts in the queryset passed to the action must be updated.
        An unselected unacknowledged alert must remain unacknowledged — bulk ops
        on a wrong queryset would silently ack alerts ops never reviewed."""
        qs = PricingDriftAlert.objects.filter(pk=self.unacked_1.pk)
        self._run_action(qs)

        self.unacked_2.refresh_from_db()
        self.assertIsNone(
            self.unacked_2.acknowledged_at,
            "Unselected alert must not be acknowledged",
        )

    def test_action_does_not_overwrite_existing_acknowledged_at(self) -> None:
        """Re-running the action on an already-acknowledged alert must NOT overwrite
        the original acknowledged_at timestamp — that timestamp is audit history
        proving when the alert was first investigated."""
        qs = PricingDriftAlert.objects.filter(pk=self.already_acked.pk)
        self._run_action(qs)

        self.already_acked.refresh_from_db()
        self.assertEqual(
            self.already_acked.acknowledged_at,
            self.original_ack_time,
            "Existing acknowledged_at must not be overwritten by a second action run",
        )

    def test_action_on_empty_queryset_writes_nothing_to_db(self) -> None:
        """Running the action on an empty queryset must be a no-op. Zero rows
        updated means updated_at is not touched on any alert — a clean no-op
        avoids spurious audit trail entries."""
        before = timezone.now()
        qs = PricingDriftAlert.objects.none()
        self._run_action(qs)

        # No alert should have been touched
        touched = PricingDriftAlert.objects.filter(updated_at__gte=before)
        self.assertEqual(touched.count(), 0)

    def test_updated_at_refreshed_alongside_acknowledged_at(self) -> None:
        """The action must update both acknowledged_at and updated_at together.
        updated_at records that the row was modified; a mismatch between the two
        would mislead anyone reading the audit trail."""
        before = timezone.now()
        qs = PricingDriftAlert.objects.filter(pk=self.unacked_1.pk)
        self._run_action(qs)

        self.unacked_1.refresh_from_db()
        self.assertGreaterEqual(self.unacked_1.acknowledged_at, before)
        self.assertGreaterEqual(self.unacked_1.updated_at, before)

    def test_already_acknowledged_alert_updated_at_is_not_bumped_on_re_action(self) -> None:
        """When the action is run on an already-acknowledged alert, neither
        acknowledged_at nor updated_at should change. The filter
        acknowledged_at__isnull=True ensures already-acked rows are skipped."""
        acked_updated_at = self.already_acked.updated_at
        qs = PricingDriftAlert.objects.filter(pk=self.already_acked.pk)
        self._run_action(qs)

        self.already_acked.refresh_from_db()
        self.assertEqual(self.already_acked.updated_at, acked_updated_at)

    def test_action_filters_only_unacknowledged_within_selected_queryset(self) -> None:
        """When the queryset contains a mix of acknowledged and unacknowledged alerts,
        the action must only stamp the unacknowledged ones. The filter
        acknowledged_at__isnull=True is applied inside the action, not by the caller."""
        before = timezone.now()
        qs = PricingDriftAlert.objects.filter(pk__in=[self.unacked_1.pk, self.already_acked.pk])
        self._run_action(qs)

        self.unacked_1.refresh_from_db()
        self.already_acked.refresh_from_db()

        self.assertGreaterEqual(self.unacked_1.acknowledged_at, before)
        self.assertEqual(
            self.already_acked.acknowledged_at,
            self.original_ack_time,
        )
