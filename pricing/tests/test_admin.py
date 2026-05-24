"""Tests for pricing admin — registration and actions.

Business scenario: ops team reviews drift alerts in Django admin, marks them
acknowledged once investigated, and filters by severity to triage quickly.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import site
from django.utils import timezone

from pricing.models import PricingDriftAlert, PricingSnapshot, Provider
from pricing.tests.factories import PricingDriftAlertFactory


@pytest.mark.django_db
def test_pricing_models_registered_in_admin() -> None:
    assert site.is_registered(Provider)
    assert site.is_registered(PricingSnapshot)


@pytest.mark.django_db
def test_pricing_drift_alert_registered_in_admin() -> None:
    """PricingDriftAlert must be visible in admin so ops can review and triage alerts."""
    assert site.is_registered(PricingDriftAlert)


@pytest.mark.django_db
def test_pricing_drift_alert_admin_has_expected_list_display() -> None:
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
    assert expected.issubset(set(model_admin.list_display))


@pytest.mark.django_db
def test_pricing_drift_alert_admin_has_mark_acknowledged_action() -> None:
    """The 'mark acknowledged' admin action must be registered so ops can bulk-triage alerts."""
    model_admin = site._registry[PricingDriftAlert]
    action_names = list(model_admin.actions) if model_admin.actions else []
    action_name_strs = [a if isinstance(a, str) else getattr(a, "__name__", str(a)) for a in action_names]
    assert any("acknowledged" in name.lower() for name in action_name_strs), (
        f"Expected an 'acknowledged' action, got: {action_name_strs}"
    )


@pytest.mark.django_db
def test_mark_acknowledged_action_sets_acknowledged_at_on_selected_alerts(
    admin_client: object,
) -> None:
    """Applying 'mark acknowledged' to a queryset must stamp acknowledged_at = now()
    for each selected alert. Unselected alerts are not touched."""
    alert1 = PricingDriftAlertFactory()
    alert2 = PricingDriftAlertFactory()
    unrelated = PricingDriftAlertFactory()

    assert alert1.acknowledged_at is None
    assert alert2.acknowledged_at is None
    assert unrelated.acknowledged_at is None

    # Locate the action function from the admin registry and call it directly
    model_admin = site._registry[PricingDriftAlert]
    action_fn = None
    for action in model_admin.actions or []:
        name = action if isinstance(action, str) else getattr(action, "__name__", str(action))
        if "acknowledged" in name.lower():
            action_fn = model_admin.get_action(action)[0] if isinstance(action, str) else action
            break

    assert action_fn is not None, "Could not locate mark-acknowledged action"

    before = timezone.now()
    qs = PricingDriftAlert.objects.filter(pk__in=[alert1.pk, alert2.pk])
    action_fn(model_admin, None, qs)

    alert1.refresh_from_db()
    alert2.refresh_from_db()
    unrelated.refresh_from_db()

    assert alert1.acknowledged_at is not None
    assert alert1.acknowledged_at >= before
    assert alert2.acknowledged_at is not None
    assert unrelated.acknowledged_at is None
    # updated_at must also be stamped so the auto_now field reflects the acknowledgement
    assert alert1.updated_at >= before
    assert alert2.updated_at >= before
