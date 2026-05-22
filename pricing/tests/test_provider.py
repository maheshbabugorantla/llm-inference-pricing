"""Provider business-scenario tests.

Design: go beyond constraint checking — prove that Provider plays its correct
role as the anchor for PricingSnapshot rows, that the is_active flag gates
pipeline inclusion, and that the data_source_tier drives scheduling decisions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider
from pricing.tests.factories import ProviderFactory


@pytest.mark.django_db
def test_cloud_provider_accepts_pricing_snapshot():
    """A cloud provider with realtime_api tier can anchor PricingSnapshot rows —
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
    assert provider.pricingsnapshot_set.filter(pk=snap.pk).exists()
    assert provider.pricingsnapshot_set.get(pk=snap.pk).hourly_usd == Decimal("2.49")


@pytest.mark.django_db
def test_inactive_provider_excluded_from_active_filter():
    """Inactive providers must not appear when the pipeline filters is_active=True,
    ensuring stale or sunset providers don't pollute cost-cell output."""
    active = ProviderFactory(slug="runpod", is_active=True)
    inactive = ProviderFactory(slug="deprecated-cloud", is_active=False)

    active_slugs = set(Provider.objects.filter(is_active=True).values_list("slug", flat=True))
    assert active.slug in active_slugs
    assert inactive.slug not in active_slugs


@pytest.mark.django_db
def test_duplicate_provider_slug_raises_integrity_error():
    """Provider slugs are the join key used across scrapers, seeds, and snapshots.
    A duplicate slug would silently corrupt which rows belong to which provider."""
    ProviderFactory(slug="runpod")
    with pytest.raises(IntegrityError):
        ProviderFactory(slug="runpod")


@pytest.mark.django_db
def test_on_prem_provider_type_is_accepted_and_persists():
    """on_prem providers are created by the on-prem generator, not scrapers.
    The provider_type choice must be accepted so synthetic snapshots land correctly."""
    p = ProviderFactory(provider_type="on_prem")
    p.full_clean()
    p.refresh_from_db()
    assert p.provider_type == "on_prem"


@pytest.mark.django_db
def test_realtime_api_tier_marks_provider_for_frequent_scraping():
    """realtime_api tier signals Celery Beat to schedule frequent scraping runs.
    The tier must be accepted and persisted exactly as set."""
    p = ProviderFactory(data_source_tier="realtime_api")
    p.full_clean()
    assert Provider.objects.get(pk=p.pk).data_source_tier == "realtime_api"


@pytest.mark.django_db
def test_scraped_page_tier_marks_provider_for_html_scraping():
    """scraped_page tier marks providers (Lambda, Vast) whose prices require
    HTML parsing rather than a machine-readable API."""
    p = ProviderFactory(data_source_tier="scraped_page")
    p.full_clean()
    assert Provider.objects.get(pk=p.pk).data_source_tier == "scraped_page"
