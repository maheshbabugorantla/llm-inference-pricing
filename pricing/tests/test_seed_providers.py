"""seed_providers management command business-scenario tests.

Design: prove the command handles the full lifecycle — initial seeding, idempotent
re-runs, display_name updates, loud rejection of invalid YAML, and that seeded
providers immediately accept PricingSnapshot rows — so operators can safely re-run
seeds in production without fear of data corruption or silent failures.
"""

from __future__ import annotations

import textwrap
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider


@pytest.mark.django_db
def test_seed_providers_creates_all_known_cloud_providers():
    """All six cloud providers used by scrapers must be seeded so scraper tasks
    can look them up by slug without creating orphaned PricingSnapshot rows."""
    call_command("seed_providers")
    slugs = set(Provider.objects.values_list("slug", flat=True))
    assert {"runpod", "lambda", "vast", "nebius", "gcp", "aws", "azure"}.issubset(slugs)


@pytest.mark.django_db
def test_seed_providers_is_idempotent():
    """Re-running seed_providers must not create duplicate rows — idempotency
    lets operators run it safely during deployments without manual cleanup."""
    call_command("seed_providers")
    count_before = Provider.objects.count()
    call_command("seed_providers")
    assert Provider.objects.count() == count_before


@pytest.mark.django_db
def test_seed_providers_sets_correct_data_source_tier():
    """data_source_tier drives Celery Beat scheduling frequency. RunPod with
    realtime_api gets frequent polling; Lambda with scraped_page gets slower
    scraping — the tiers must be seeded exactly as specified."""
    call_command("seed_providers")
    assert Provider.objects.get(slug="runpod").data_source_tier == "realtime_api"
    assert Provider.objects.get(slug="lambda").data_source_tier == "scraped_page"


@pytest.mark.django_db
def test_seed_providers_update_changes_display_name(tmp_path):
    """When a provider's display_name is corrected in the seeds file and the
    command is re-run, the DB row must reflect the new name — stale display
    names would surface in the UI."""
    seeds_file = tmp_path / "providers.yaml"
    seeds_file.write_text(
        textwrap.dedent("""\
        - slug: runpod
          display_name: "RunPod (Old Name)"
          provider_type: cloud
          data_source_tier: realtime_api
    """)
    )
    call_command("seed_providers", "--seeds-file", str(seeds_file))
    assert Provider.objects.get(slug="runpod").display_name == "RunPod (Old Name)"

    seeds_file.write_text(
        textwrap.dedent("""\
        - slug: runpod
          display_name: "RunPod"
          provider_type: cloud
          data_source_tier: realtime_api
    """)
    )
    call_command("seed_providers", "--seeds-file", str(seeds_file))
    assert Provider.objects.get(slug="runpod").display_name == "RunPod"


@pytest.mark.django_db
def test_seed_providers_rejects_yaml_with_invalid_provider_type(tmp_path):
    """An invalid provider_type value must cause the command to fail loudly
    before touching the DB — partial writes would leave the provider table in
    an inconsistent state that breaks scraper task routing."""
    seeds_file = tmp_path / "providers.yaml"
    seeds_file.write_text(
        textwrap.dedent("""\
        - slug: bad-provider
          display_name: "Bad Provider"
          provider_type: datacenter
          data_source_tier: realtime_api
    """)
    )
    with pytest.raises(CommandError, match="YAML schema validation failed"):
        call_command("seed_providers", "--seeds-file", str(seeds_file))
    assert not Provider.objects.filter(slug="bad-provider").exists()


@pytest.mark.django_db
def test_seed_providers_rejects_unknown_data_source_tier(tmp_path):
    """An unrecognised data_source_tier (e.g. 'daily') must be rejected before
    touching the DB — an unknown tier would break Celery Beat scheduling logic
    that routes tasks based on this field."""
    seeds_file = tmp_path / "providers.yaml"
    seeds_file.write_text(
        textwrap.dedent("""\
        - slug: bad-tier-provider
          display_name: "Bad Tier"
          provider_type: cloud
          data_source_tier: daily
    """)
    )
    with pytest.raises(CommandError, match="YAML schema validation failed"):
        call_command("seed_providers", "--seeds-file", str(seeds_file))
    assert not Provider.objects.filter(slug="bad-tier-provider").exists()


@pytest.mark.django_db
def test_seeded_runpod_provider_immediately_accepts_pricing_snapshot():
    """A Provider row seeded by seed_providers must be usable as the FK anchor
    for PricingSnapshot rows immediately after seeding — this is the contract
    that allows scrapers to run right after a fresh deployment."""
    call_command("seed_providers")
    provider = Provider.objects.get(slug="runpod")
    gpu = GPUFactory(slug="nvidia-h100-sxm-80")

    snap = PricingSnapshot.objects.create(
        provider=provider,
        gpu=gpu,
        tier="community",
        region="",
        hourly_usd=Decimal("2.49"),
        available=True,
        scraped_at=timezone.now(),
        raw_payload={},
    )
    assert snap.provider.slug == "runpod"
    assert snap.gpu.slug == "nvidia-h100-sxm-80"


@pytest.mark.django_db
def test_all_seeded_providers_are_active_by_default():
    """All providers seeded by seed_providers must have is_active=True so the
    pipeline includes them in scraping runs immediately after deployment.
    A provider seeded with is_active=False would silently skip scraping."""
    call_command("seed_providers")
    inactive = Provider.objects.filter(is_active=False)
    assert not inactive.exists(), (
        f"These seeded providers must be active: {list(inactive.values_list('slug', flat=True))}"
    )
