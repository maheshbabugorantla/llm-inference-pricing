"""Tests for on-prem snapshot generator and post-save signal (M08.T04)."""

from __future__ import annotations

from django.test import TestCase

from pricing.generators.on_prem import regenerate_on_prem_snapshots
from pricing.models import OnPremDeployment, PricingSnapshot, Provider
from pricing.tests.factories import OnPremDeploymentFactory


class OnPremGeneratorTest(TestCase):
    def setUp(self) -> None:
        self.deployment = OnPremDeploymentFactory()

    def test_generator_emits_two_snapshots_per_deployment(self) -> None:
        PricingSnapshot.objects.all().delete()
        written = regenerate_on_prem_snapshots()
        self.assertEqual(written, 2)
        tiers = list(PricingSnapshot.objects.values_list("tier", flat=True))
        self.assertIn("tco", tiers)
        self.assertIn("marginal", tiers)

    def test_generator_creates_synthetic_provider(self) -> None:
        PricingSnapshot.objects.all().delete()
        regenerate_on_prem_snapshots()
        self.assertTrue(
            Provider.objects.filter(slug=f"on-prem-{self.deployment.slug}", provider_type="on_prem").exists()
        )

    def test_generator_is_idempotent(self) -> None:
        PricingSnapshot.objects.all().delete()
        regenerate_on_prem_snapshots()
        regenerate_on_prem_snapshots()
        # Each call appends 2 new snapshots (hypertable); only 1 provider created
        self.assertEqual(Provider.objects.filter(provider_type="on_prem").count(), 1)

    def test_inactive_deployments_excluded(self) -> None:
        PricingSnapshot.objects.all().delete()
        OnPremDeployment.objects.filter(pk=self.deployment.pk).update(is_active=False)
        written = regenerate_on_prem_snapshots()
        self.assertEqual(written, 0)

    def test_generator_two_deployments_four_snapshots(self) -> None:
        PricingSnapshot.objects.all().delete()
        OnPremDeploymentFactory(hardware_sku=self.deployment.hardware_sku)
        written = regenerate_on_prem_snapshots()
        self.assertEqual(written, 4)

    def test_generator_long_slug_provider_slug_fits_64_chars(self) -> None:
        """Provider.slug max_length=64; OnPremDeployment.slug allows 128 — generator must truncate."""
        PricingSnapshot.objects.all().delete()
        long_slug = "x" * 100
        OnPremDeploymentFactory(slug=long_slug)
        regenerate_on_prem_snapshots()
        for provider in Provider.objects.filter(provider_type="on_prem"):
            self.assertLessEqual(len(provider.slug), 64)


class OnPremSignalTest(TestCase):
    def test_post_save_signal_triggers_regeneration(self) -> None:
        """post_save on OnPremDeployment registers on_commit → regeneration fires on commit."""
        with self.captureOnCommitCallbacks(execute=True):
            d = OnPremDeploymentFactory()
        self.assertEqual(PricingSnapshot.objects.filter(provider__slug=f"on-prem-{d.slug}").count(), 2)

    def test_post_save_signal_inactive_update_skipped(self) -> None:
        """Saving an inactive deployment must not trigger snapshot regeneration."""
        with self.captureOnCommitCallbacks(execute=True):
            d = OnPremDeploymentFactory(is_active=True)
        initial_count = PricingSnapshot.objects.count()
        # Deactivate via update (no signal) then save (triggers signal with is_active=False)
        OnPremDeployment.objects.filter(pk=d.pk).update(is_active=False)
        d.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            d.save()
        self.assertEqual(PricingSnapshot.objects.count(), initial_count)
