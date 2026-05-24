from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider


class ScrapePricingCommandTest(TestCase):
    def test_scrape_pricing_runpod_uses_fixture(self) -> None:
        Provider.objects.create(
            slug="runpod",
            display_name="RunPod",
            provider_type="cloud",
            data_source_tier="realtime_api",
        )
        GPUFactory(slug="nvidia-h100-sxm-80")
        GPUFactory(slug="nvidia-rtx-4090")

        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "runpod_gputypes_response.json").read_text()
        )
        with patch("pricing.scrapers.runpod.fetch_runpod_gputypes", return_value=fixture):
            call_command("scrape_pricing", "--provider", "runpod")

        h100_snaps = PricingSnapshot.objects.filter(gpu__slug="nvidia-h100-sxm-80")
        self.assertEqual(h100_snaps.count(), 8)  # 4 on-demand/spot + 4 reserved tiers
        tiers = set(h100_snaps.values_list("tier", flat=True))
        self.assertIn("reserved-1yr", tiers)
        self.assertIn("community", tiers)
