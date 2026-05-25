"""Four-way Appendix A integration test (M09.T07).

PRD section 7.6 Appendix A: Qwen2.5-Coder-32B FP8 at batch=8 ctx=32k must surface all four
deployment modes simultaneously in pricing_current_cost_cells.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from pricing.generators.on_prem import regenerate_on_prem_snapshots
from pricing.generators.reserved_cloud import regenerate_reserved_cloud_snapshots
from pricing.models import PricingSnapshot, Provider
from pricing.services.cost import refresh_cost_cells


class AppendixAFourWayComparisonTest(TransactionTestCase):
    def test_appendix_a_four_way_comparison(self):
        """End-to-end: seed all scenarios, add one cloud scrape, refresh cost cells, verify all four modes.

        PRD section 7.6 Appendix A -- every cost comparison page must show cloud on-demand, reserved cloud,
        on-prem TCO, and on-prem marginal side-by-side for any compatible (model, GPU) pair.
        If any mode is missing, the customer is making a purchase decision with incomplete data.
        """
        call_command("seed_catalog")
        call_command("seed_providers")
        call_command("seed_on_prem")
        call_command("seed_reserved")

        # Simulate one RunPod community scrape -- this provides the on-demand cloud column.
        runpod_provider = Provider.objects.get(slug="runpod")
        from catalog.models import GPU

        h100 = GPU.objects.get(slug="nvidia-h100-sxm-80")

        # Clear any generator snapshots from post_save signals fired during seeding
        PricingSnapshot.objects.filter(tier__in=["tco", "marginal"]).delete()
        PricingSnapshot.objects.filter(tier__startswith="reserved-").delete()

        PricingSnapshot.objects.create(
            provider=runpod_provider,
            gpu=h100,
            tier="community",
            region="",
            hourly_usd=Decimal("1.99"),
            available=True,
            scraped_at=timezone.now(),
            raw_payload={},
        )

        # Run both generators to produce on-prem and reserved snapshots from seed data
        regenerate_on_prem_snapshots()
        regenerate_reserved_cloud_snapshots()

        refresh_cost_cells(concurrently=False)

        with connection.cursor() as c:
            c.execute(
                """
                SELECT tier, usd_per_m_output
                FROM pricing_current_cost_cells
                WHERE model_slug = 'qwen-2-5-coder-32b'
                  AND tp_size = 1
                  AND batch_size = 8
                  AND context_length = 32768
                ORDER BY usd_per_m_output
                """
            )
            rows = c.fetchall()

        tiers = {r[0] for r in rows}

        self.assertIn("community", tiers, f"on-demand cloud tier 'community' missing. Found: {tiers}")
        self.assertTrue(
            any(t.startswith("reserved-") and not t.startswith("reserved-marginal-") for t in tiers),
            f"reserved cloud tier (reserved-<slug>) missing. Found: {tiers}",
        )
        self.assertIn("tco", tiers, f"on-prem TCO tier missing. Found: {tiers}")
        self.assertIn("marginal", tiers, f"on-prem marginal tier missing. Found: {tiers}")

        # PRD ordering invariant: marginal (sunk capex) is always cheaper than green-field TCO
        marginal_costs = [r[1] for r in rows if r[0] == "marginal"]
        tco_costs = [r[1] for r in rows if r[0] == "tco"]
        self.assertTrue(marginal_costs and tco_costs, "Need at least one marginal and one tco row")
        self.assertLess(
            min(marginal_costs),
            min(tco_costs),
            f"On-prem marginal ({min(marginal_costs)}) should be cheaper than TCO ({min(tco_costs)})",
        )
