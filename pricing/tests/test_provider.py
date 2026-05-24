"""Provider business-scenario tests.

Design: go beyond constraint checking -- prove that Provider plays its correct
role as the anchor for PricingSnapshot rows, that the is_active flag gates
pipeline inclusion, and that the data_source_tier drives scheduling decisions.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import ProtectedError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider
from pricing.tests.factories import ProviderFactory


class ProviderTest(TestCase):
    def test_cloud_provider_accepts_pricing_snapshot(self):
        """A cloud provider with realtime_api tier can anchor PricingSnapshot rows --
        the FK relationship the cost-cell pipeline queries to join prices to GPUs."""
        provider = ProviderFactory(slug="runpod", provider_type="cloud", data_source_tier="realtime_api")
        gpu = GPUFactory(slug="nvidia-h100-sxm-80")
        snap = PricingSnapshot.objects.create(
            provider=provider,
            gpu=gpu,
            tier="community",
            region="us-east-1",
            hourly_usd=Decimal("2.49"),
            available=True,
            scraped_at=timezone.now(),
            raw_payload={"price": "2.49"},
        )
        self.assertTrue(provider.pricingsnapshot_set.filter(pk=snap.pk).exists())
        self.assertEqual(provider.pricingsnapshot_set.get(pk=snap.pk).hourly_usd, Decimal("2.49"))

    def test_inactive_provider_excluded_from_active_filter(self):
        """Inactive providers must not appear when the pipeline filters is_active=True,
        ensuring stale or sunset providers do not pollute cost-cell output."""
        active = ProviderFactory(slug="runpod", is_active=True)
        inactive = ProviderFactory(slug="deprecated-cloud", is_active=False)

        active_slugs = set(Provider.objects.filter(is_active=True).values_list("slug", flat=True))
        self.assertIn(active.slug, active_slugs)
        self.assertNotIn(inactive.slug, active_slugs)

    def test_duplicate_provider_slug_raises_integrity_error(self):
        """Provider slugs are the join key used across scrapers, seeds, and snapshots.
        A duplicate slug would silently corrupt which rows belong to which provider."""
        ProviderFactory(slug="runpod")
        with self.assertRaises(IntegrityError):
            ProviderFactory(slug="runpod")

    def test_on_prem_provider_type_is_accepted_and_persists(self):
        """on_prem providers are created by the on-prem generator, not scrapers.
        The provider_type choice must be accepted so synthetic snapshots land correctly."""
        p = ProviderFactory(provider_type="on_prem")
        p.full_clean()
        p.refresh_from_db()
        self.assertEqual(p.provider_type, "on_prem")

    def test_realtime_api_tier_marks_provider_for_frequent_scraping(self):
        """realtime_api tier signals Celery Beat to schedule frequent scraping runs.
        The tier must be accepted and persisted exactly as set."""
        p = ProviderFactory(data_source_tier="realtime_api")
        p.full_clean()
        self.assertEqual(Provider.objects.get(pk=p.pk).data_source_tier, "realtime_api")

    def test_scraped_page_tier_marks_provider_for_html_scraping(self):
        """scraped_page tier marks providers (Lambda, Vast) whose prices require
        HTML parsing rather than a machine-readable API."""
        p = ProviderFactory(data_source_tier="scraped_page")
        p.full_clean()
        self.assertEqual(Provider.objects.get(pk=p.pk).data_source_tier, "scraped_page")

    def test_multiple_providers_price_same_gpu_all_snapshots_independently_queryable(self):
        """RunPod and Lambda can both offer H100 pricing simultaneously. The cost-cell
        pipeline queries all providers for a given GPU and needs each provider's
        snapshot independently -- a shared row or collisions would corrupt comparisons."""
        runpod = ProviderFactory(slug="runpod", data_source_tier="realtime_api")
        lambda_labs = ProviderFactory(slug="lambda", data_source_tier="scraped_page")
        gpu = GPUFactory(slug="nvidia-h100-sxm-80")

        now = timezone.now()
        snap_runpod = PricingSnapshot.objects.create(
            provider=runpod,
            gpu=gpu,
            tier="on_demand",
            region="",
            hourly_usd=Decimal("2.49"),
            available=True,
            scraped_at=now,
            raw_payload={},
        )
        snap_lambda = PricingSnapshot.objects.create(
            provider=lambda_labs,
            gpu=gpu,
            tier="on_demand",
            region="",
            hourly_usd=Decimal("2.99"),
            available=True,
            scraped_at=now,
            raw_payload={},
        )

        h100_snaps = PricingSnapshot.objects.filter(gpu=gpu)
        self.assertEqual(h100_snaps.count(), 2)
        self.assertEqual(h100_snaps.filter(provider=runpod).get().hourly_usd, Decimal("2.49"))
        self.assertEqual(h100_snaps.filter(provider=lambda_labs).get().hourly_usd, Decimal("2.99"))
        self.assertNotEqual(snap_runpod.pk, snap_lambda.pk)

    def test_deleting_provider_with_active_snapshots_is_blocked(self):
        """Provider to PricingSnapshot FK is PROTECT so deleting a provider with live
        snapshot rows fails loudly. Silent cascade-delete would destroy the historical
        pricing record needed for cost trend analysis and audit."""
        provider = ProviderFactory(slug="runpod")
        gpu = GPUFactory(slug="nvidia-h100-sxm-80")
        PricingSnapshot.objects.create(
            provider=provider,
            gpu=gpu,
            tier="on_demand",
            region="",
            hourly_usd=Decimal("2.49"),
            available=True,
            scraped_at=timezone.now(),
            raw_payload={},
        )
        with self.assertRaises(ProtectedError):
            provider.delete()

    def test_scraped_page_provider_accepts_blank_api_endpoint(self):
        """Lambda Labs and Vast.ai are scraped_page providers -- they have no public API
        endpoint. api_endpoint must accept blank so these providers can be seeded
        without a placeholder URL that would mislead operators."""
        p = ProviderFactory(slug="lambda", data_source_tier="scraped_page", api_endpoint="")
        p.full_clean()
        self.assertEqual(Provider.objects.get(pk=p.pk).api_endpoint, "")
