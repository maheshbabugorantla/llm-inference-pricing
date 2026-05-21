"""Tests for on-prem snapshot generator and post-save signal (M08.T04)."""

from __future__ import annotations

import pytest

from pricing.generators.on_prem import regenerate_on_prem_snapshots
from pricing.models import OnPremDeployment, PricingSnapshot, Provider
from pricing.tests.factories import OnPremDeploymentFactory


@pytest.mark.django_db(transaction=True)
def test_generator_emits_two_snapshots_per_deployment():
    OnPremDeploymentFactory()
    written = regenerate_on_prem_snapshots()
    assert written == 2
    tiers = list(PricingSnapshot.objects.values_list("tier", flat=True))
    assert "tco" in tiers
    assert "marginal" in tiers


@pytest.mark.django_db(transaction=True)
def test_generator_creates_synthetic_provider():
    OnPremDeploymentFactory(slug="test-deploy")
    regenerate_on_prem_snapshots()
    assert Provider.objects.filter(slug="on-prem-test-deploy", provider_type="on_prem").exists()


@pytest.mark.django_db(transaction=True)
def test_generator_is_idempotent():
    OnPremDeploymentFactory()
    regenerate_on_prem_snapshots()
    regenerate_on_prem_snapshots()
    # Each call creates 2 new snapshots (TimescaleDB append-only) but only 1 provider
    assert Provider.objects.filter(provider_type="on_prem").count() == 1


@pytest.mark.django_db(transaction=True)
def test_inactive_deployments_excluded():
    OnPremDeploymentFactory(is_active=False)
    written = regenerate_on_prem_snapshots()
    assert written == 0


@pytest.mark.django_db(transaction=True)
def test_generator_two_deployments_four_snapshots():
    OnPremDeploymentFactory()
    OnPremDeploymentFactory()
    written = regenerate_on_prem_snapshots()
    assert written == 4


@pytest.mark.django_db(transaction=True)
def test_post_save_signal_triggers_regeneration():
    d = OnPremDeploymentFactory()
    # signal fires on create (created=True) → snapshots exist
    assert PricingSnapshot.objects.filter(provider__slug=f"on-prem-{d.slug}").count() == 2


@pytest.mark.django_db(transaction=True)
def test_post_save_signal_inactive_update_skipped():
    d = OnPremDeploymentFactory(is_active=True)
    initial_count = PricingSnapshot.objects.count()
    # Deactivate — signal should skip regeneration
    OnPremDeployment.objects.filter(pk=d.pk).update(is_active=False)
    d.refresh_from_db()
    d.save()  # triggers signal with created=False, is_active=False
    assert PricingSnapshot.objects.count() == initial_count
