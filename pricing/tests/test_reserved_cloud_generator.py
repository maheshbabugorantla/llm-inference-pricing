"""Tests for reserved cloud snapshot generator (M09.T04).

Note: the scenarios here overlap with test_generators_transaction.py which provides
the same coverage using cleaner setUpTestData / setUp patterns. Both files are kept
so no tests are dropped during the pytest to Django TestCase migration.

Uses Django TestCase with captureOnCommitCallbacks(execute=True) to test
on_commit callbacks without requiring real transactions.

IMPORTANT: Does NOT use setUpTestData() because creating a ReservedCloudDeployment
fires a post_save signal that registers transaction.on_commit(regenerate_*), which
when fired after setUpTestData's atomic block commits, deadlocks against TimescaleDB
background workers. Per-test setUp() + per-test data creation avoids this issue.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from catalog.tests.factories import GPUFactory
from pricing.generators.reserved_cloud import regenerate_reserved_cloud_snapshots
from pricing.models import PricingSnapshot, ReservedCapacityProduct, ReservedCloudDeployment
from pricing.tests.factories import ProviderFactory


def _make_scenario(suffix: str) -> tuple[ReservedCloudDeployment, str]:
    """Create a ReservedCapacityProduct + ReservedCloudDeployment; return (deployment, provider_slug).

    Callers should delete signal-created PricingSnapshot rows after calling this if they
    want a clean slate for direct generator-call tests.
    """
    gpu = GPUFactory(slug=f"nvidia-h100-sxm-80-gen{suffix}", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(slug=f"lambda-gen{suffix}", provider_type="cloud")
    product = ReservedCapacityProduct.objects.create(
        slug=f"lambda-h100-1yr-gen{suffix}",
        display_name="Lambda Reserved H100 1-yr",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="all_upfront",
        term_months=12,
        upfront_usd=Decimal("500000.00"),
        listing_observed_at="2025-01-15",
    )
    deployment = ReservedCloudDeployment.objects.create(
        slug=f"lambda-prod-h100-1yr-gen{suffix}",
        display_name="Lambda Production H100 1-yr",
        product=product,
        cloud_provider=provider,
        expected_utilization_pct=Decimal("0.700"),
    )
    return deployment, provider.slug


class ReservedCloudGeneratorTest(TestCase):
    """Generator and post-save signal tests using per-test setUp (no setUpTestData).

    setUpTestData is intentionally NOT used here: creating a ReservedCloudDeployment
    fires a post_save signal that calls transaction.on_commit(regenerate_*). When that
    on_commit callback fires after setUpTestData's atomic block commits, it causes a
    deadlock against TimescaleDB background workers. Using setUp() instead keeps each
    test's data within a per-test savepoint that is rolled back cleanly.
    """

    def setUp(self) -> None:
        self.deployment, self.provider_slug = _make_scenario("setup")
        # Clear signal-created snapshots from setUp; direct-call tests assert
        # what regenerate_*() produces, not what the signal produces.
        PricingSnapshot.objects.all().delete()

    def test_generator_emits_both_committed_and_marginal_snapshots(self) -> None:
        """Each active deployment produces exactly two snapshots."""
        written = regenerate_reserved_cloud_snapshots()
        self.assertEqual(written, 2)
        tiers = set(PricingSnapshot.objects.values_list("tier", flat=True))
        self.assertIn(f"reserved-{self.deployment.slug}", tiers)
        self.assertIn(f"reserved-marginal-{self.deployment.slug}", tiers)

    def test_generator_reuses_cloud_provider_not_synthetic_one(self) -> None:
        """ADR-010: reserved snapshots attach to the existing cloud Provider, not a new synthetic one."""
        regenerate_reserved_cloud_snapshots()
        snaps = PricingSnapshot.objects.filter(tier__startswith="reserved-")
        self.assertTrue(snaps.exists())
        for snap in snaps:
            self.assertEqual(
                snap.provider.slug,
                self.provider_slug,
                f"Expected provider '{self.provider_slug}', got '{snap.provider.slug}'",
            )

    def test_tier_string_is_composite_with_deployment_slug(self) -> None:
        """Tier must be 'reserved-<slug>' and 'reserved-marginal-<slug>' for traceability."""
        regenerate_reserved_cloud_snapshots()
        tier_strings = {s.tier for s in PricingSnapshot.objects.all()}
        self.assertIn(f"reserved-{self.deployment.slug}", tier_strings)
        self.assertIn(f"reserved-marginal-{self.deployment.slug}", tier_strings)

    def test_generator_snapshots_have_correct_gpu(self) -> None:
        """Each generated snapshot must reference the GPU from the product."""
        expected_gpu_slug = self.deployment.product.gpu.slug
        regenerate_reserved_cloud_snapshots()
        for snap in PricingSnapshot.objects.all():
            self.assertEqual(snap.gpu.slug, expected_gpu_slug)

    def test_generator_skips_inactive_deployments(self) -> None:
        """Inactive ReservedCloudDeployment rows must not produce snapshots."""
        self.deployment.is_active = False
        self.deployment.save()
        written = regenerate_reserved_cloud_snapshots()
        self.assertEqual(written, 0)
        self.assertEqual(PricingSnapshot.objects.count(), 0)

    def test_generator_raw_payload_contains_deployment_and_product_slug(self) -> None:
        """raw_payload must record deployment_slug and product_slug for auditability."""
        regenerate_reserved_cloud_snapshots()
        snap = PricingSnapshot.objects.first()
        self.assertIsNotNone(snap)
        self.assertEqual(snap.raw_payload["deployment_slug"], self.deployment.slug)
        self.assertEqual(snap.raw_payload["product_slug"], self.deployment.product.slug)

    def test_generator_returns_count_equal_to_two_per_active_deployment(self) -> None:
        """With N active deployments, regenerate_reserved_cloud_snapshots returns 2*N."""
        _make_scenario("g1")
        _make_scenario("g2")
        PricingSnapshot.objects.all().delete()
        written = regenerate_reserved_cloud_snapshots()
        self.assertEqual(written, 6)
        self.assertEqual(PricingSnapshot.objects.count(), 6)

    def test_post_save_signal_regenerates_snapshots_on_deployment_create(self) -> None:
        """Creating a ReservedCloudDeployment triggers the post_save signal -> snapshots generated."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-sig", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="lambda-sig", provider_type="cloud")
        product = ReservedCapacityProduct.objects.create(
            slug="lambda-h100-1yr-sig",
            display_name="Lambda Reserved H100 1-yr",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("500000.00"),
            listing_observed_at="2025-01-15",
        )
        PricingSnapshot.objects.all().delete()

        with self.captureOnCommitCallbacks(execute=True):
            new_dep = ReservedCloudDeployment.objects.create(
                slug="lambda-sig-dep",
                display_name="Lambda Signal Test",
                product=product,
                cloud_provider=provider,
                expected_utilization_pct=Decimal("0.700"),
            )

        # Signal fires on_commit -> regenerate_reserved_cloud_snapshots() runs, creating
        # snapshots for all active deployments. Verify at least the two expected tiers
        # for the newly created deployment exist (the setUp deployment also gets snapshots).
        expected_tiers = {f"reserved-{new_dep.slug}", f"reserved-marginal-{new_dep.slug}"}
        actual_tiers = set(PricingSnapshot.objects.values_list("tier", flat=True))
        for tier in expected_tiers:
            self.assertIn(tier, actual_tiers, f"Expected tier '{tier}' in snapshots after signal")

    def test_post_save_signal_retires_snapshots_on_deactivation(self) -> None:
        """Setting is_active=False on an existing deployment marks its snapshot tiers available=False."""
        regenerate_reserved_cloud_snapshots()
        tiers = [
            f"reserved-{self.deployment.slug}",
            f"reserved-marginal-{self.deployment.slug}",
        ]
        self.assertEqual(PricingSnapshot.objects.filter(tier__in=tiers, available=True).count(), 2)

        with self.captureOnCommitCallbacks(execute=True):
            self.deployment.is_active = False
            self.deployment.save()

        self.assertEqual(PricingSnapshot.objects.filter(tier__in=tiers).count(), 2)
        self.assertEqual(PricingSnapshot.objects.filter(tier__in=tiers, available=False).count(), 2)
        self.assertEqual(PricingSnapshot.objects.filter(tier__in=tiers, available=True).count(), 0)

    def test_generator_skips_deployments_with_inactive_product(self) -> None:
        """Generator must skip deployments whose product has is_active=False."""
        self.deployment.product.is_active = False
        self.deployment.product.save()
        written = regenerate_reserved_cloud_snapshots()
        self.assertEqual(written, 0)
        self.assertEqual(PricingSnapshot.objects.count(), 0)

    def test_post_save_signal_does_not_regenerate_when_created_inactive(self) -> None:
        """Creating a deployment with is_active=False must NOT trigger snapshot generation."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-inact", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="lambda-inact", provider_type="cloud")
        product = ReservedCapacityProduct.objects.create(
            slug="lambda-h100-1yr-inact",
            display_name="Lambda Reserved H100 1-yr (inactive)",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("500000.00"),
            listing_observed_at="2025-01-15",
        )
        PricingSnapshot.objects.all().delete()

        with self.captureOnCommitCallbacks(execute=True):
            ReservedCloudDeployment.objects.create(
                slug="lambda-inact-dep",
                display_name="Lambda Inactive Deployment",
                product=product,
                cloud_provider=provider,
                is_active=False,
                expected_utilization_pct=Decimal("0.700"),
            )

        self.assertEqual(
            PricingSnapshot.objects.count(),
            0,
            "Creating an inactive deployment must not emit any snapshot rows",
        )
