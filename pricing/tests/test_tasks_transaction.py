"""
TransactionTestCase tests for Celery tasks that require real transactions
(regenerate_on_prem_snapshots_task and refresh_current_cost_cells).

These tests are in a dedicated file so that TransactionTestCase classes
(which flush the database after each test) do not interfere with TestCase
classes in test_tasks.py and other files.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from catalog.tests.factories import (
    BenchmarkPointFactory,
    GPUFactory,
    ModelFactory,
    QuantizationFactory,
)
from pricing.models import PricingSnapshot
from pricing.tasks import refresh_current_cost_cells, regenerate_on_prem_snapshots_task
from pricing.tests.factories import OnPremDeploymentFactory, PricingSnapshotFactory, ProviderFactory

# ---- regenerate_on_prem_snapshots_task ---------------------------------------


class OnPremGeneratorTaskTest(TransactionTestCase):
    def test_regenerate_task_tco_exceeds_marginal_because_capex_included(self):
        """TCO = capex + power + facility + ops. Marginal excludes capex.
        For a new $180k node (4yr depreciation, 70% util) capex alone is ~$7/hr/node,
        so per-GPU TCO must exceed per-GPU marginal by a material amount.
        """
        deployment = OnPremDeploymentFactory(
            hardware_sku__num_gpus=4,
            hardware_sku__gpu__tdp_watts=700,
            hardware_sku__host_tdp_watts=1200,
            capex_per_node_usd=Decimal("180000.00"),
            salvage_pct=Decimal("0.10"),
            depreciation_years=4,
            expected_utilization_pct=Decimal("0.70"),
            power_usd_per_kwh=Decimal("0.10"),
            pue=Decimal("1.4"),
            monthly_colo_usd=Decimal("1000.00"),
            monthly_bandwidth_usd=Decimal("200.00"),
            sysadmin_annual_burdened_usd=Decimal("200000.00"),
            gpu_count_per_admin=128,
        )
        # Clear snapshots created by the post_save signal during factory setup,
        # so we only assert on what the task itself produces.
        PricingSnapshot.objects.all().delete()

        result = regenerate_on_prem_snapshots_task.apply().get()

        self.assertEqual(result, 2)
        tco_snap = PricingSnapshot.objects.get(tier="tco")
        marginal_snap = PricingSnapshot.objects.get(tier="marginal")

        # TCO must exceed marginal -- capex is the difference
        self.assertGreater(tco_snap.hourly_usd, marginal_snap.hourly_usd)

        # Per-GPU TCO should be in the right ballpark (~$3.45/hr for this config)
        self.assertGreater(tco_snap.hourly_usd, Decimal("3.00"))
        self.assertLess(tco_snap.hourly_usd, Decimal("4.00"))

        # Snapshot is linked to the correct GPU
        self.assertEqual(tco_snap.gpu, deployment.hardware_sku.gpu)
        self.assertEqual(marginal_snap.gpu, deployment.hardware_sku.gpu)

    def test_regenerate_task_raw_payload_carries_deployment_slug_for_traceability(self):
        """Each snapshot's raw_payload must record the deployment_slug so engineers
        can trace a price back to its source deployment config."""
        OnPremDeploymentFactory(slug="test-trace-deploy")
        regenerate_on_prem_snapshots_task.apply().get()

        for snap in PricingSnapshot.objects.all():
            self.assertEqual(snap.raw_payload.get("deployment_slug"), "test-trace-deploy")

    def test_regenerate_task_multiple_deployments_produce_independent_snapshots(self):
        """Two deployments with different cost profiles must produce 4 snapshots
        and the TCO prices must differ between deployments."""
        cheap = OnPremDeploymentFactory(
            hardware_sku__num_gpus=4,
            capex_per_node_usd=Decimal("50000.00"),
            power_usd_per_kwh=Decimal("0.05"),
        )
        expensive = OnPremDeploymentFactory(
            hardware_sku=cheap.hardware_sku,
            capex_per_node_usd=Decimal("300000.00"),
            power_usd_per_kwh=Decimal("0.20"),
        )
        # Clear signal-fired snapshots from factory setup before measuring task output.
        PricingSnapshot.objects.all().delete()

        result = regenerate_on_prem_snapshots_task.apply().get()

        self.assertEqual(result, 4)
        cheap_tco = (
            PricingSnapshot.objects.filter(tier="tco", provider__slug__contains=cheap.slug)
            .latest("scraped_at")
            .hourly_usd
        )
        expensive_tco = (
            PricingSnapshot.objects.filter(tier="tco", provider__slug__contains=expensive.slug)
            .latest("scraped_at")
            .hourly_usd
        )
        self.assertGreater(expensive_tco, cheap_tco)

    def test_regenerate_task_excludes_inactive_deployments(self):
        """Inactive deployments must not generate snapshots -- they represent
        decommissioned hardware that should drop out of cost comparisons."""
        OnPremDeploymentFactory(is_active=False)
        result = regenerate_on_prem_snapshots_task.apply().get()

        self.assertEqual(result, 0)
        self.assertEqual(PricingSnapshot.objects.count(), 0)


# ---- refresh_current_cost_cells ----------------------------------------------


class RefreshCostCellsTaskTest(TransactionTestCase):
    def test_refresh_cost_cells_task_produces_queryable_cost_cells(self):
        """End-to-end: create a benchmark point + snapshot, run the task,
        then verify a cost cell row is readable from the materialized view."""
        quant = QuantizationFactory(slug="fp16")
        gpu = GPUFactory(slug="nvidia-h100-sxm-80", vram_gb=80, tdp_watts=700)
        model = ModelFactory(slug="llama-3-70b", recommended_quant=quant)
        BenchmarkPointFactory(
            model=model,
            gpu=gpu,
            quantization=quant,
            tp_size=1,
            batch_size=8,
            context_length=8192,
            prefill_tps_aggregate=Decimal("18000"),
            decode_tps_aggregate=Decimal("750"),
        )
        provider = ProviderFactory(slug="runpod")
        PricingSnapshotFactory(
            provider=provider,
            gpu=gpu,
            tier="on_demand",
            hourly_usd=Decimal("2.49"),
            scraped_at=timezone.now(),
        )

        refresh_current_cost_cells.apply().get()

        with connection.cursor() as c:
            c.execute(
                "SELECT COUNT(*) FROM pricing_current_cost_cells WHERE provider_id = %s",
                [provider.id],
            )
            row_count = c.fetchone()[0]

        self.assertGreater(row_count, 0)
