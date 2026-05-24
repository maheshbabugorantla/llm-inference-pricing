"""Tests for ReservedCloudDeployment model (M09.T02)."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction as db_tx
from django.db.models import ProtectedError
from django.test import TestCase

from catalog.tests.factories import GPUFactory
from pricing.models import ReservedCapacityProduct, ReservedCloudDeployment
from pricing.tests.factories import ProviderFactory


def _make_product(slug_suffix: str = "") -> ReservedCapacityProduct:
    gpu = GPUFactory(slug=f"nvidia-h100-sxm-80-rcd{slug_suffix}", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(slug=f"lambda-rcd{slug_suffix}", provider_type="cloud")
    return ReservedCapacityProduct.objects.create(
        slug=f"lambda-h100-1yr-rcd{slug_suffix}",
        display_name="Lambda Reserved H100 1-yr",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="all_upfront",
        term_months=12,
        upfront_usd=Decimal("500000.00"),
        listing_observed_at="2025-01-15",
    )


class ReservedCloudDeploymentTest(TestCase):
    def test_reserved_cloud_deployment_saves_with_required_fields(self):
        """A deployment linked to a ReservedCapacityProduct saves cleanly with defaults."""
        product = _make_product("a")
        dep = ReservedCloudDeployment.objects.create(
            slug="lambda-prod-h100-1yr-a",
            display_name="Lambda Production H100 1-yr",
            product=product,
            cloud_provider=product.cloud_provider,
            num_nodes=2,
            expected_utilization_pct=Decimal("0.700"),
        )
        self.assertIsNotNone(dep.pk)
        self.assertEqual(dep.expected_utilization_pct, Decimal("0.700"))

    def test_reserved_cloud_deployment_overrides_are_nullable(self):
        """Override fields (upfront, monthly, per_hour) are optional -- None by default."""
        product = _make_product("b")
        dep = ReservedCloudDeployment.objects.create(
            slug="lambda-prod-h100-1yr-b",
            display_name="Lambda Production H100 1-yr",
            product=product,
            cloud_provider=product.cloud_provider,
        )
        self.assertIsNone(dep.upfront_override_usd)
        self.assertIsNone(dep.monthly_recurring_override_usd)
        self.assertIsNone(dep.per_hour_override_usd)

    def test_reserved_cloud_deployment_with_negotiated_override_saves_cleanly(self):
        """A deployment with all three override fields set is valid (negotiated deal scenario)."""
        product = _make_product("c")
        dep = ReservedCloudDeployment.objects.create(
            slug="lambda-prod-h100-1yr-override-c",
            display_name="Lambda Production H100 1-yr (negotiated)",
            product=product,
            cloud_provider=product.cloud_provider,
            expected_utilization_pct=Decimal("0.800"),
            upfront_override_usd=Decimal("450000.00"),
            monthly_recurring_override_usd=Decimal("0"),
            per_hour_override_usd=Decimal("0"),
        )
        self.assertEqual(dep.upfront_override_usd, Decimal("450000.00"))

    def test_reserved_cloud_deployment_fk_to_product_is_protected(self):
        """Deleting a ReservedCapacityProduct that has deployments is blocked (PROTECT)."""
        product = _make_product("d")
        ReservedCloudDeployment.objects.create(
            slug="lambda-prod-h100-1yr-d",
            display_name="Lambda Production H100 1-yr",
            product=product,
            cloud_provider=product.cloud_provider,
        )
        with self.assertRaises(ProtectedError):
            with db_tx.atomic():
                product.delete()

    def test_reserved_cloud_deployment_str_returns_display_name(self):
        """__str__ must return display_name per SHARED.md convention."""
        product = _make_product("e")
        dep = ReservedCloudDeployment.objects.create(
            slug="lambda-prod-h100-1yr-e",
            display_name="Lambda Production H100 1-yr",
            product=product,
            cloud_provider=product.cloud_provider,
        )
        self.assertEqual(str(dep), "Lambda Production H100 1-yr")

    def test_duplicate_deployment_slug_raises_integrity_error(self):
        """Two ReservedCloudDeployment rows with the same slug must be rejected."""
        product = _make_product("f")
        ReservedCloudDeployment.objects.create(
            slug="lambda-prod-h100-1yr-dup",
            display_name="Lambda Production H100 1-yr",
            product=product,
            cloud_provider=product.cloud_provider,
        )
        with self.assertRaises(IntegrityError):
            with db_tx.atomic():
                ReservedCloudDeployment.objects.create(
                    slug="lambda-prod-h100-1yr-dup",
                    display_name="Lambda Production H100 1-yr Dup",
                    product=product,
                    cloud_provider=product.cloud_provider,
                )

    def test_mismatched_cloud_provider_fails_full_clean(self):
        """full_clean() must reject a deployment whose cloud_provider differs from product.cloud_provider.

        Django does not call clean() on objects.create()/save(), so this invariant is enforced
        only when full_clean() is called (e.g. via the seed command or admin).
        """
        product = _make_product("g")
        other_provider = ProviderFactory(slug="other-cloud-g", provider_type="cloud")
        dep = ReservedCloudDeployment(
            slug="lambda-prod-h100-mismatch-g",
            display_name="Lambda Production H100 1-yr (mismatch)",
            product=product,
            cloud_provider=other_provider,
        )
        with self.assertRaisesRegex(ValidationError, "cloud_provider must match"):
            dep.full_clean()
