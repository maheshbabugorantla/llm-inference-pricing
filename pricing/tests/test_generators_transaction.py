"""Transaction-isolated generator tests using Django TestCase and TransactionTestCase.

Both generators are @transaction.atomic. Their post_save signals register callbacks
with transaction.on_commit(), which only fires when a real transaction commits —
not inside TestCase's outer savepoint. Structure:

  *HappyPathTest(TestCase)    — setUpTestData; tests direct generator calls
  *SignalTest(TransactionTestCase) — tests post_save → on_commit chain + rollback
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase

from catalog.tests.factories import GPUFactory
from pricing.generators.on_prem import regenerate_on_prem_snapshots
from pricing.generators.reserved_cloud import regenerate_reserved_cloud_snapshots
from pricing.models import (
    OnPremDeployment,
    PricingSnapshot,
    Provider,
    ReservedCapacityProduct,
    ReservedCloudDeployment,
)
from pricing.tests.factories import HardwareSKUFactory, OnPremDeploymentFactory, ProviderFactory

# ---------------------------------------------------------------------------
# On-prem generator — happy path
# ---------------------------------------------------------------------------


class OnPremGeneratorHappyPathTest(TestCase):
    """Direct-call tests for regenerate_on_prem_snapshots().

    setUpTestData creates one active and one inactive deployment once for
    the class. Each test runs in its own savepoint so snapshots created by
    the generator are rolled back after every method.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.gpu = GPUFactory(slug="nvidia-h100-sxm-80-gen", vram_gb=80, tdp_watts=700)
        cls.sku = HardwareSKUFactory(gpu=cls.gpu, num_gpus=8, slug="supermicro-h100-gen")
        cls.active_dep = OnPremDeploymentFactory(
            slug="lambda-h100-gen",
            hardware_sku=cls.sku,
            is_active=True,
        )
        cls.inactive_dep = OnPremDeploymentFactory(
            slug="retired-h100-gen",
            hardware_sku=cls.sku,
            is_active=False,
        )

    def test_two_snapshots_emitted_per_active_deployment(self) -> None:
        """Each active deployment must produce exactly two PricingSnapshot rows —
        one 'tco' (amortised capex + opex) and one 'marginal' (opex only).
        The cost grid requires both so customers can compare full vs marginal cost."""
        PricingSnapshot.objects.all().delete()  # clear signal-created rows
        written = regenerate_on_prem_snapshots()

        self.assertEqual(written, 2)
        tiers = set(PricingSnapshot.objects.values_list("tier", flat=True))
        self.assertIn("tco", tiers)
        self.assertIn("marginal", tiers)

    def test_synthetic_provider_slug_follows_on_prem_prefix_convention(self) -> None:
        """The generator creates a Provider with slug='on-prem-{deployment.slug}'.
        This slug is the FK anchor for all on-prem snapshot rows — a wrong prefix
        would break any query that filters by provider_type='on_prem'."""
        PricingSnapshot.objects.all().delete()
        regenerate_on_prem_snapshots()

        self.assertTrue(
            Provider.objects.filter(
                slug=f"on-prem-{self.active_dep.slug}",
                provider_type="on_prem",
            ).exists()
        )

    def test_second_call_reuses_synthetic_provider_does_not_duplicate_it(self) -> None:
        """Calling regenerate_on_prem_snapshots() twice must leave exactly one Provider
        per deployment — idempotent upsert prevents provider table bloat on scheduler
        retries and ensures PricingSnapshot FKs always point to the same provider row."""
        PricingSnapshot.objects.all().delete()
        regenerate_on_prem_snapshots()
        regenerate_on_prem_snapshots()

        self.assertEqual(
            Provider.objects.filter(provider_type="on_prem").count(),
            1,
            "update_or_create must keep exactly one synthetic provider per deployment",
        )

    def test_inactive_deployment_excluded_from_snapshot_generation(self) -> None:
        """is_active=False deployments must produce zero snapshots. Including them
        would show sunset infrastructure in cost comparisons and mislead customers."""
        PricingSnapshot.objects.all().delete()
        # Temporarily deactivate the only active deployment
        OnPremDeployment.objects.filter(pk=self.active_dep.pk).update(is_active=False)
        written = regenerate_on_prem_snapshots()

        self.assertEqual(written, 0)
        self.assertEqual(PricingSnapshot.objects.count(), 0)

    def test_snapshot_raw_payload_records_deployment_slug_for_auditability(self) -> None:
        """raw_payload must contain deployment_slug so operators can trace each snapshot
        back to its source deployment without decoding the composite provider slug."""
        PricingSnapshot.objects.all().delete()
        regenerate_on_prem_snapshots()

        for snap in PricingSnapshot.objects.filter(tier="tco"):
            self.assertIn("deployment_slug", snap.raw_payload)
            self.assertEqual(snap.raw_payload["deployment_slug"], self.active_dep.slug)

    def test_two_deployments_produce_four_snapshots(self) -> None:
        """N active deployments must yield 2*N snapshots. A missing deployment would
        mean its cost tier disappears from the grid without any error or warning."""
        OnPremDeploymentFactory(
            slug="second-h100-gen",
            hardware_sku=self.sku,
            is_active=True,
        )
        PricingSnapshot.objects.all().delete()
        written = regenerate_on_prem_snapshots()

        self.assertEqual(written, 4)
        self.assertEqual(PricingSnapshot.objects.count(), 4)


# ---------------------------------------------------------------------------
# On-prem generator — signal + atomicity (needs real transactions)
# ---------------------------------------------------------------------------


class OnPremGeneratorSignalTest(TransactionTestCase):
    """Tests for the post_save → transaction.on_commit → regenerate chain
    and for the @transaction.atomic rollback guarantee.

    TransactionTestCase is required because:
      - transaction.on_commit() only fires when a real transaction commits,
        not inside TestCase's wrapping savepoint.
      - Rollback verification after a failed atomic block requires a real
        transaction (TestCase's outer savepoint would be poisoned by
        connection.needs_rollback=True).
    """

    def setUp(self) -> None:
        self.gpu = GPUFactory(slug="nvidia-h100-sxm-80-sig", vram_gb=80, tdp_watts=700)
        self.sku = HardwareSKUFactory(gpu=self.gpu, num_gpus=8, slug="supermicro-h100-sig")

    def test_creating_active_deployment_fires_on_commit_regeneration(self) -> None:
        """Creating an OnPremDeployment commits the transaction, which fires the
        post_save → on_commit callback and runs regenerate_on_prem_snapshots().
        Two snapshots (tco + marginal) must be queryable immediately after save()."""
        dep = OnPremDeploymentFactory(slug="oncommit-dep", hardware_sku=self.sku, is_active=True)
        provider_slug = f"on-prem-{dep.slug}"

        self.assertEqual(
            PricingSnapshot.objects.filter(provider__slug=provider_slug).count(),
            2,
        )

    def test_deactivating_deployment_suppresses_subsequent_regeneration(self) -> None:
        """Updating is_active=False on an existing deployment must NOT trigger another
        regeneration run. The signal skips when is_active=False and created=False,
        so no stale snapshots are generated after the deployment is retired."""
        dep = OnPremDeploymentFactory(slug="deactivate-dep", hardware_sku=self.sku, is_active=True)
        snapshot_count_after_create = PricingSnapshot.objects.count()

        dep.is_active = False
        dep.save()

        self.assertEqual(
            PricingSnapshot.objects.count(),
            snapshot_count_after_create,
            "Deactivating a deployment must not trigger a new regeneration run",
        )

    def test_generator_atomic_rollback_leaves_no_partial_snapshots(self) -> None:
        """regenerate_on_prem_snapshots() is @transaction.atomic. If snapshot creation
        fails mid-run, the atomic block rolls back all Provider and PricingSnapshot
        rows written so far — operators see a clean DB, not a half-generated grid."""
        OnPremDeploymentFactory(slug="rollback-dep", hardware_sku=self.sku, is_active=True)
        PricingSnapshot.objects.all().delete()
        Provider.objects.filter(provider_type="on_prem").delete()

        def create_then_fail(**kwargs: object) -> PricingSnapshot:
            raise RuntimeError("simulated snapshot write failure")

        with patch.object(PricingSnapshot.objects, "create", side_effect=create_then_fail):
            with self.assertRaises(RuntimeError):
                regenerate_on_prem_snapshots()

        self.assertEqual(PricingSnapshot.objects.count(), 0)


# ---------------------------------------------------------------------------
# Reserved cloud generator — happy path
# ---------------------------------------------------------------------------


def _make_reserved_scenario(suffix: str) -> tuple[ReservedCloudDeployment, Provider]:
    gpu = GPUFactory(slug=f"nvidia-h100-sxm-80-res{suffix}", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(slug=f"lambda-res{suffix}", provider_type="cloud")
    product = ReservedCapacityProduct.objects.create(
        slug=f"lambda-h100-1yr-res{suffix}",
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
        slug=f"lambda-prod-res{suffix}",
        display_name="Lambda Production H100",
        product=product,
        cloud_provider=provider,
        expected_utilization_pct=Decimal("0.700"),
    )
    return deployment, provider


class ReservedCloudGeneratorHappyPathTest(TestCase):
    """Direct-call tests for regenerate_reserved_cloud_snapshots().

    setUpTestData creates one product + deployment once for the class.
    Each test clears signal-created rows before calling the generator so
    assertions reflect exactly what the generator (not the signal) produced.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.deployment, cls.provider = _make_reserved_scenario("happy")

    def setUp(self) -> None:
        # Clear any snapshots created by the post_save signal during setUpTestData
        PricingSnapshot.objects.all().delete()

    def test_committed_and_marginal_snapshots_created_per_deployment(self) -> None:
        """Each active deployment must produce exactly two snapshots:
        'reserved-{slug}' (committed cost) and 'reserved-marginal-{slug}' (marginal).
        The cost grid requires both tiers to compare reservation vs spot economics."""
        written = regenerate_reserved_cloud_snapshots()

        self.assertEqual(written, 2)
        tiers = set(PricingSnapshot.objects.values_list("tier", flat=True))
        self.assertIn(f"reserved-{self.deployment.slug}", tiers)
        self.assertIn(f"reserved-marginal-{self.deployment.slug}", tiers)

    def test_cloud_provider_reused_not_a_new_synthetic_one(self) -> None:
        """ADR-010: reserved snapshots attach to the existing cloud Provider FK,
        not a fabricated 'synthetic' provider. A wrong provider would make reserved
        prices invisible in provider-scoped cost queries."""
        regenerate_reserved_cloud_snapshots()

        for snap in PricingSnapshot.objects.all():
            self.assertEqual(
                snap.provider.slug,
                self.provider.slug,
                f"Snapshot must use the cloud provider '{self.provider.slug}', got '{snap.provider.slug}'",
            )

    def test_stale_tiers_marked_unavailable_before_new_snapshots_created(self) -> None:
        """When the generator runs a second time, old reserved tiers for the same
        deployment must be marked available=False before new rows are inserted.
        Without this, cost queries would double-count the deployment's cost."""
        regenerate_reserved_cloud_snapshots()
        first_run_pks = set(PricingSnapshot.objects.values_list("pk", flat=True))

        regenerate_reserved_cloud_snapshots()

        stale = PricingSnapshot.objects.filter(pk__in=first_run_pks)
        self.assertTrue(stale.exists())
        for snap in stale:
            self.assertFalse(snap.available, f"Stale snapshot pk={snap.pk} must be available=False")

    def test_inactive_deployment_produces_no_snapshots(self) -> None:
        """is_active=False deployments must be excluded. The generator's filter
        product__is_active=True also gates on the product, so retiring a product
        silently removes all its deployments from the snapshot grid."""
        self.deployment.is_active = False
        self.deployment.save()

        written = regenerate_reserved_cloud_snapshots()

        self.assertEqual(written, 0)
        self.assertEqual(PricingSnapshot.objects.count(), 0)

    def test_raw_payload_records_deployment_and_product_slug(self) -> None:
        """raw_payload must carry deployment_slug and product_slug so each snapshot
        can be traced to its source without decoding the composite tier string."""
        regenerate_reserved_cloud_snapshots()

        snap = PricingSnapshot.objects.first()
        self.assertIsNotNone(snap)
        self.assertEqual(snap.raw_payload["deployment_slug"], self.deployment.slug)
        self.assertEqual(snap.raw_payload["product_slug"], self.deployment.product.slug)


# ---------------------------------------------------------------------------
# Reserved cloud generator — signal + atomicity
# ---------------------------------------------------------------------------


class ReservedCloudGeneratorSignalTest(TransactionTestCase):
    """Tests for the post_save → transaction.on_commit → regenerate chain
    and for the @transaction.atomic rollback guarantee.

    TransactionTestCase is required for the same reasons as OnPremGeneratorSignalTest.
    """

    def test_creating_active_deployment_triggers_on_commit_snapshot_generation(self) -> None:
        """Creating a ReservedCloudDeployment commits the transaction, firing the
        post_save → on_commit callback. Two snapshots must be queryable right after
        the save() returns."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-rsig", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="lambda-rsig", provider_type="cloud")
        product = ReservedCapacityProduct.objects.create(
            slug="lambda-h100-rsig",
            display_name="Lambda Reserved H100",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("500000.00"),
            listing_observed_at="2025-01-15",
        )
        PricingSnapshot.objects.all().delete()

        ReservedCloudDeployment.objects.create(
            slug="lambda-rsig-dep",
            display_name="Lambda Signal Test",
            product=product,
            cloud_provider=provider,
            expected_utilization_pct=Decimal("0.700"),
        )

        self.assertEqual(PricingSnapshot.objects.count(), 2)

    def test_deactivating_deployment_retires_its_snapshot_tiers_via_on_commit(self) -> None:
        """Setting is_active=False on an existing deployment must mark the two reserved
        tiers as available=False via the on_commit callback — preserving the rows for
        audit but hiding them from active cost-cell queries."""
        deployment, _ = _make_reserved_scenario("retire")
        PricingSnapshot.objects.all().delete()
        regenerate_reserved_cloud_snapshots()
        tiers = [
            f"reserved-{deployment.slug}",
            f"reserved-marginal-{deployment.slug}",
        ]
        self.assertEqual(PricingSnapshot.objects.filter(tier__in=tiers, available=True).count(), 2)

        deployment.is_active = False
        deployment.save()

        self.assertEqual(PricingSnapshot.objects.filter(tier__in=tiers, available=False).count(), 2)
        self.assertEqual(PricingSnapshot.objects.filter(tier__in=tiers, available=True).count(), 0)

    def test_creating_inactive_deployment_does_not_generate_snapshots(self) -> None:
        """Creating a deployment with is_active=False must NOT emit snapshot rows.
        The signal checks is_active and skips regeneration for inactive deployments
        to prevent empty cost tiers from appearing in the grid."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-inact", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="lambda-inact-r", provider_type="cloud")
        product = ReservedCapacityProduct.objects.create(
            slug="lambda-h100-inact",
            display_name="Lambda Reserved H100 Inactive",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("500000.00"),
            listing_observed_at="2025-01-15",
        )
        PricingSnapshot.objects.all().delete()

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

    def test_generator_atomic_rollback_leaves_no_partial_snapshots(self) -> None:
        """regenerate_reserved_cloud_snapshots() is @transaction.atomic. A failure
        mid-run must roll back all snapshot rows written so far — the DB is left
        exactly as it was before the call."""
        _, _ = _make_reserved_scenario("rollback")
        PricingSnapshot.objects.all().delete()

        def create_then_fail(**kwargs: object) -> PricingSnapshot:
            raise RuntimeError("simulated snapshot creation failure")

        with patch.object(PricingSnapshot.objects, "create", side_effect=create_then_fail):
            with self.assertRaises(RuntimeError):
                regenerate_reserved_cloud_snapshots()

        self.assertEqual(PricingSnapshot.objects.count(), 0)
