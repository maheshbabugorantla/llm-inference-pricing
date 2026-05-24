"""seed_providers management command business-scenario tests."""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider


class SeedProvidersTest(TestCase):
    def test_seed_providers_creates_all_known_cloud_providers(self) -> None:
        """All seven cloud providers used by scrapers must be seeded."""
        call_command("seed_providers")
        slugs = set(Provider.objects.values_list("slug", flat=True))
        for slug in ("runpod", "lambda", "vast", "nebius", "gcp", "aws", "azure"):
            self.assertIn(slug, slugs)

    def test_seed_providers_is_idempotent(self) -> None:
        """Re-running seed_providers must not create duplicate rows."""
        call_command("seed_providers")
        count_before = Provider.objects.count()
        call_command("seed_providers")
        self.assertEqual(Provider.objects.count(), count_before)

    def test_seed_providers_sets_correct_data_source_tier(self) -> None:
        """data_source_tier drives Celery Beat scheduling frequency."""
        call_command("seed_providers")
        self.assertEqual(Provider.objects.get(slug="runpod").data_source_tier, "realtime_api")
        self.assertEqual(Provider.objects.get(slug="lambda").data_source_tier, "scraped_page")

    def test_seed_providers_update_changes_display_name(self) -> None:
        """Re-seeding with changed display_name updates the DB row."""
        tmp = tempfile.mkdtemp()
        try:
            seeds_file = Path(tmp) / "providers.yaml"
            seeds_file.write_text(
                textwrap.dedent("""\
                - slug: runpod
                  display_name: "RunPod (Old Name)"
                  provider_type: cloud
                  data_source_tier: realtime_api
            """)
            )
            call_command("seed_providers", "--seeds-file", str(seeds_file))
            self.assertEqual(Provider.objects.get(slug="runpod").display_name, "RunPod (Old Name)")

            seeds_file.write_text(
                textwrap.dedent("""\
                - slug: runpod
                  display_name: "RunPod"
                  provider_type: cloud
                  data_source_tier: realtime_api
            """)
            )
            call_command("seed_providers", "--seeds-file", str(seeds_file))
            self.assertEqual(Provider.objects.get(slug="runpod").display_name, "RunPod")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_seed_providers_rejects_yaml_with_invalid_provider_type(self) -> None:
        """An invalid provider_type must cause the command to fail before touching the DB."""
        tmp = tempfile.mkdtemp()
        try:
            seeds_file = Path(tmp) / "providers.yaml"
            seeds_file.write_text(
                textwrap.dedent("""\
                - slug: bad-provider
                  display_name: "Bad Provider"
                  provider_type: datacenter
                  data_source_tier: realtime_api
            """)
            )
            with self.assertRaisesRegex(CommandError, "YAML schema validation failed"):
                call_command("seed_providers", "--seeds-file", str(seeds_file))
            self.assertFalse(Provider.objects.filter(slug="bad-provider").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_seed_providers_rejects_unknown_data_source_tier(self) -> None:
        """An unrecognised data_source_tier must be rejected before touching the DB."""
        tmp = tempfile.mkdtemp()
        try:
            seeds_file = Path(tmp) / "providers.yaml"
            seeds_file.write_text(
                textwrap.dedent("""\
                - slug: bad-tier-provider
                  display_name: "Bad Tier"
                  provider_type: cloud
                  data_source_tier: daily
            """)
            )
            with self.assertRaisesRegex(CommandError, "YAML schema validation failed"):
                call_command("seed_providers", "--seeds-file", str(seeds_file))
            self.assertFalse(Provider.objects.filter(slug="bad-tier-provider").exists())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_seeded_runpod_provider_immediately_accepts_pricing_snapshot(self) -> None:
        """A Provider row seeded by seed_providers must be usable as FK anchor immediately."""
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
        self.assertEqual(snap.provider.slug, "runpod")
        self.assertEqual(snap.gpu.slug, "nvidia-h100-sxm-80")

    def test_all_seeded_providers_are_active_by_default(self) -> None:
        """All providers seeded by seed_providers must have is_active=True."""
        call_command("seed_providers")
        inactive = Provider.objects.filter(is_active=False)
        self.assertFalse(
            inactive.exists(),
            f"These seeded providers must be active: {list(inactive.values_list('slug', flat=True))}",
        )
