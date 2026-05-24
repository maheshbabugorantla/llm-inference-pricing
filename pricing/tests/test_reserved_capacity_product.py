"""Tests for ReservedCapacityProduct model (M09.T01)."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.tests.factories import GPUFactory
from pricing.models import ReservedCapacityProduct
from pricing.tests.factories import ProviderFactory


class ReservedCapacityProductHappyPathTest(TestCase):
    """Happy-path: each payment cadence saves cleanly."""

    def test_all_upfront_reserved_capacity_product_saves_cleanly(self):
        """An all-upfront product with upfront > 0 and monthly/per-hour = 0 is valid."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="aws", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="aws-p5-14d-all-upfront",
            display_name="AWS p5.48xlarge 14-day Capacity Block",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=1,
            upfront_usd=Decimal("131712.00"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("0"),
            minimum_utilization_floor_pct=Decimal("0.000"),
            listing_observed_at="2025-01-15",
        )
        product.full_clean()
        product.save()
        self.assertTrue(ReservedCapacityProduct.objects.filter(slug="aws-p5-14d-all-upfront").exists())

    def test_capacity_block_reserved_capacity_product_saves_cleanly(self):
        """A capacity_block product with capacity_block_total_usd > 0 is valid."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-cb", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="aws-cb", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="aws-p5-14d-cb",
            display_name="AWS p5.48xlarge 14-day Capacity Block",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="capacity_block",
            term_months=1,
            upfront_usd=Decimal("0"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("131712.00"),
            block_duration_hours=336,
            minimum_utilization_floor_pct=Decimal("1.000"),
            listing_observed_at="2025-01-15",
        )
        product.full_clean()
        product.save()
        self.assertTrue(ReservedCapacityProduct.objects.filter(slug="aws-p5-14d-cb").exists())

    def test_partial_upfront_reserved_capacity_product_saves_cleanly(self):
        """A partial-upfront product with upfront > 0 and monthly > 0 is valid."""
        gpu = GPUFactory(slug="nvidia-h100-pcie-80-pu", vram_gb=80, tdp_watts=400)
        provider = ProviderFactory(slug="azure-pu", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="azure-nd-h100-ri-1yr-partial",
            display_name="Azure NDm H100 v4 1-yr RI Partial Upfront",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="partial_upfront",
            term_months=12,
            upfront_usd=Decimal("50000.00"),
            monthly_recurring_usd=Decimal("4000.00"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("0"),
            minimum_utilization_floor_pct=Decimal("0.000"),
            listing_observed_at="2025-01-15",
        )
        product.full_clean()
        product.save()
        self.assertTrue(ReservedCapacityProduct.objects.filter(slug="azure-nd-h100-ri-1yr-partial").exists())

    def test_no_upfront_reserved_capacity_product_saves_cleanly(self):
        """A no-upfront product with monthly > 0 is valid (per_hour optional)."""
        gpu = GPUFactory(slug="nvidia-a100-sxm-80-nu", vram_gb=80, tdp_watts=400)
        provider = ProviderFactory(slug="gcp-nu", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="gcp-a3-cud-1yr",
            display_name="GCP A3 CUD 1-yr No Upfront",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="no_upfront",
            term_months=12,
            upfront_usd=Decimal("0"),
            monthly_recurring_usd=Decimal("8000.00"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("0"),
            minimum_utilization_floor_pct=Decimal("0.000"),
            listing_observed_at="2025-01-15",
        )
        product.full_clean()
        product.save()
        self.assertTrue(ReservedCapacityProduct.objects.filter(slug="gcp-a3-cud-1yr").exists())


class ReservedCapacityProductValidationTest(TestCase):
    """Cadence-specific validation failures."""

    def test_all_upfront_with_zero_upfront_raises_validation_error(self):
        """all_upfront cadence with upfront_usd=0 must be rejected -- the commitment is undefined."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-val1", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="aws-val1", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="bad-all-upfront-zero",
            display_name="Bad All Upfront",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("0"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )
        with self.assertRaises(ValidationError):
            product.full_clean()

    def test_all_upfront_with_nonzero_monthly_raises_validation_error(self):
        """all_upfront cadence with monthly_recurring_usd != 0 violates the cadence contract."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-val2", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="aws-val2", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="bad-all-upfront-monthly",
            display_name="Bad All Upfront Monthly",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("120000.00"),
            monthly_recurring_usd=Decimal("500.00"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )
        with self.assertRaises(ValidationError):
            product.full_clean()

    def test_capacity_block_with_zero_total_raises_validation_error(self):
        """capacity_block cadence with capacity_block_total_usd=0 must be rejected."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-val3", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="aws-val3", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="bad-capacity-block",
            display_name="Bad Capacity Block",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="capacity_block",
            term_months=1,
            upfront_usd=Decimal("0"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )
        with self.assertRaises(ValidationError):
            product.full_clean()

    def test_partial_upfront_with_zero_upfront_raises_validation_error(self):
        """partial_upfront cadence requires upfront_usd > 0."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-val4", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="azure-val4", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="bad-partial-upfront",
            display_name="Bad Partial Upfront",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="partial_upfront",
            term_months=12,
            upfront_usd=Decimal("0"),
            monthly_recurring_usd=Decimal("4000.00"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )
        with self.assertRaises(ValidationError):
            product.full_clean()

    def test_no_upfront_with_zero_monthly_raises_validation_error(self):
        """no_upfront cadence requires monthly_recurring_usd > 0."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-val5", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="gcp-val5", provider_type="cloud")
        product = ReservedCapacityProduct(
            slug="bad-no-upfront",
            display_name="Bad No Upfront",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="no_upfront",
            term_months=12,
            upfront_usd=Decimal("0"),
            monthly_recurring_usd=Decimal("0"),
            per_active_hour_usd=Decimal("0"),
            capacity_block_total_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )
        with self.assertRaises(ValidationError):
            product.full_clean()


class ReservedCapacityProductConstraintsTest(TestCase):
    """Slug uniqueness and FK constraints."""

    def test_duplicate_reserved_product_slug_raises_integrity_error(self):
        """Two ReservedCapacityProduct rows with the same slug must be rejected by the DB."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-dup", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="aws-dup", provider_type="cloud")
        ReservedCapacityProduct.objects.create(
            slug="aws-p5-1yr-dup",
            display_name="AWS P5 1-yr",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("1000000.00"),
            listing_observed_at="2025-01-15",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ReservedCapacityProduct.objects.create(
                    slug="aws-p5-1yr-dup",
                    display_name="AWS P5 1-yr Duplicate",
                    cloud_provider=provider,
                    gpu=gpu,
                    gpus_per_node=8,
                    payment_cadence="all_upfront",
                    term_months=12,
                    upfront_usd=Decimal("1000000.00"),
                    listing_observed_at="2025-01-15",
                )

    def test_reserved_product_str_returns_display_name(self):
        """__str__ must return display_name per SHARED.md convention."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-str", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="lambda-str", provider_type="cloud")
        product = ReservedCapacityProduct.objects.create(
            slug="lambda-h100-1yr-str",
            display_name="Lambda Reserved H100 1-yr",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("500000.00"),
            listing_observed_at="2025-01-15",
        )
        self.assertEqual(str(product), "Lambda Reserved H100 1-yr")

    def test_on_demand_reference_usd_is_optional(self):
        """on_demand_reference_usd_per_hour is display-only and must be nullable."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-ref", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(slug="lambda-ref", provider_type="cloud")
        product = ReservedCapacityProduct.objects.create(
            slug="lambda-h100-1yr-ref",
            display_name="Lambda Reserved H100 1-yr",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="all_upfront",
            term_months=12,
            upfront_usd=Decimal("500000.00"),
            listing_observed_at="2025-01-15",
        )
        self.assertIsNone(product.on_demand_reference_usd_per_hour)
