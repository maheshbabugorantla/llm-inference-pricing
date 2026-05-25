"""
Business-scenario tests for Celery tasks (pricing/tasks.py).

Design principle: mock at the network boundary (scraper functions), let persist_prices
and the generator run against the real test DB, then assert on actual DB state.
This proves the full ingestion pipeline works — not just that function A called function B.
"""

from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider
from pricing.scrapers.base import ParserDriftError, ScrapedPrice
from pricing.tasks import (
    computeprices_sanity_check,
    refresh_current_cost_cells,
    regenerate_on_prem_snapshots_task,
    scrape_aws,
    scrape_azure,
    scrape_lambda,
    scrape_nebius,
    scrape_runpod,
    scrape_vast,
)
from pricing.tests.factories import OnPremDeploymentFactory


def _scraped(provider_slug, gpu_hint, tier, price, region="us-east-1"):
    return ScrapedPrice(
        provider_slug=provider_slug,
        gpu_slug_hint=gpu_hint,
        tier=tier,
        region=region,
        hourly_usd=Decimal(price),
        available=True,
        raw={"gpu": gpu_hint, "price": price},
    )


class ScrapeRunpodTaskTest(TestCase):
    def setUp(self) -> None:
        self.runpod_provider = Provider.objects.create(
            slug="runpod", display_name="RunPod", provider_type="cloud", data_source_tier="realtime_api"
        )

    def test_scrape_runpod_writes_multi_tier_snapshots(self) -> None:
        """RunPod returns community + secure prices for H100 — both tiers must land in DB."""
        h100 = GPUFactory(slug="nvidia-h100-sxm-80")
        prices = [
            _scraped("runpod", "H100 SXM", "community", "2.49"),
            _scraped("runpod", "H100 SXM", "secure", "3.49"),
        ]
        with patch("pricing.tasks.runpod.scrape", return_value=prices):
            count = scrape_runpod.apply().get()

        self.assertEqual(count, 2)
        snaps = PricingSnapshot.objects.filter(provider=self.runpod_provider, gpu=h100).order_by("hourly_usd")
        self.assertEqual(snaps.count(), 2)
        self.assertEqual(snaps[0].tier, "community")
        self.assertEqual(snaps[0].hourly_usd, Decimal("2.49"))
        self.assertEqual(snaps[1].tier, "secure")
        self.assertEqual(snaps[1].hourly_usd, Decimal("3.49"))
        self.assertTrue(all(s.scraped_at.tzinfo is not None for s in snaps))

    def test_scrape_runpod_handles_mixed_gpu_batch(self) -> None:
        """Batch with H100 and A100 → two separate GPU slugs persisted correctly."""
        h100 = GPUFactory(slug="nvidia-h100-sxm-80")
        a100 = GPUFactory(slug="nvidia-a100-sxm-80")
        prices = [
            _scraped("runpod", "H100 SXM", "on_demand", "2.49"),
            _scraped("runpod", "A100 SXM", "on_demand", "1.19"),
        ]
        with patch("pricing.tasks.runpod.scrape", return_value=prices):
            count = scrape_runpod.apply().get()

        self.assertEqual(count, 2)
        self.assertEqual(PricingSnapshot.objects.filter(gpu=h100).count(), 1)
        self.assertEqual(PricingSnapshot.objects.filter(gpu=a100).count(), 1)

    def test_scrape_runpod_silently_drops_unmapped_gpu(self) -> None:
        """Unknown GPU hint must be silently skipped — no snapshot written, no exception."""
        prices = [_scraped("runpod", "SomeUnknownGPU-9000", "on_demand", "99.00")]
        with patch("pricing.tasks.runpod.scrape", return_value=prices):
            count = scrape_runpod.apply().get()

        self.assertEqual(count, 0)
        self.assertEqual(PricingSnapshot.objects.count(), 0)

    def test_scrape_runpod_partial_batch_skips_unknown_keeps_known(self) -> None:
        """Mixed batch: known GPU persisted, unknown GPU silently dropped."""
        GPUFactory(slug="nvidia-h100-sxm-80")
        prices = [
            _scraped("runpod", "H100 SXM", "community", "2.49"),
            _scraped("runpod", "FutureTechGPU", "community", "50.00"),
        ]
        with patch("pricing.tasks.runpod.scrape", return_value=prices):
            count = scrape_runpod.apply().get()

        self.assertEqual(count, 1)
        self.assertEqual(PricingSnapshot.objects.get().hourly_usd, Decimal("2.49"))

    def test_scrape_runpod_parser_drift_leaves_db_clean_and_alerts_sentry(self) -> None:
        """Schema drift must not write partial data and must notify Sentry with exact message."""
        GPUFactory(slug="nvidia-h100-sxm-80")
        exc = ParserDriftError("runpod graphql field 'gpuTypes' disappeared")

        with patch("pricing.tasks.runpod.scrape", side_effect=exc):
            with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
                with self.assertRaises(ParserDriftError):
                    scrape_runpod.apply().get()

        self.assertEqual(PricingSnapshot.objects.count(), 0)
        mock_msg.assert_called_once_with(str(exc), level="error")

    def test_scrape_runpod_network_failure_alerts_sentry_and_retries(self) -> None:
        """Transient network error → Sentry capture + retry (max_retries=0 to short-circuit)."""
        exc = OSError("connection reset by peer")

        with patch("pricing.tasks.runpod.scrape", side_effect=exc):
            with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
                with patch.object(scrape_runpod, "max_retries", 0):
                    result = scrape_runpod.apply(throw=False)

        self.assertTrue(result.failed())
        mock_cap.assert_called()
        self.assertEqual(PricingSnapshot.objects.count(), 0)


class ScrapeLambdaTaskTest(TestCase):
    def setUp(self) -> None:
        self.lambda_provider = Provider.objects.create(
            slug="lambda", display_name="Lambda Labs", provider_type="cloud", data_source_tier="realtime_api"
        )

    def test_scrape_lambda_writes_on_demand_snapshot(self) -> None:
        """Lambda Labs H100 on-demand price lands in DB with correct provider and GPU."""
        h100 = GPUFactory(slug="nvidia-h100-sxm-80")
        prices = [_scraped("lambda", "gpu_8x_h100_sxm5", "on_demand", "28.00", region="us-south-1")]
        with patch("pricing.tasks.lambda_labs.scrape", return_value=prices):
            with patch("pricing.tasks.lambda_labs.map_lambda_gpu", return_value="nvidia-h100-sxm-80"):
                count = scrape_lambda.apply().get()

        self.assertEqual(count, 1)
        snap = PricingSnapshot.objects.get()
        self.assertEqual(snap.gpu, h100)
        self.assertEqual(snap.provider, self.lambda_provider)
        self.assertEqual(snap.hourly_usd, Decimal("28.00"))
        self.assertEqual(snap.region, "us-south-1")

    def test_scrape_lambda_parser_drift_captured(self) -> None:
        exc = ParserDriftError("lambda pricing page structure changed")
        with patch("pricing.tasks.lambda_labs.scrape", side_effect=exc):
            with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
                with self.assertRaises(ParserDriftError):
                    scrape_lambda.apply().get()
        mock_msg.assert_called_once_with(str(exc), level="error")

    def test_scrape_lambda_timeout_captured_and_retried(self) -> None:
        exc = TimeoutError("lambda API timed out after 30s")
        with patch("pricing.tasks.lambda_labs.scrape", side_effect=exc):
            with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
                with patch.object(scrape_lambda, "max_retries", 0):
                    result = scrape_lambda.apply(throw=False)
        self.assertTrue(result.failed())
        mock_cap.assert_called()


class ScrapeVastTaskTest(TestCase):
    def setUp(self) -> None:
        self.vast_provider = Provider.objects.create(
            slug="vast", display_name="Vast.ai", provider_type="cloud", data_source_tier="realtime_api"
        )

    def test_scrape_vast_writes_community_snapshot(self) -> None:
        GPUFactory(slug="nvidia-h100-sxm-80")
        prices = [_scraped("vast", "H100 SXM5 80GB", "on_demand", "1.89")]
        with patch("pricing.tasks.vast.scrape", return_value=prices):
            with patch("pricing.tasks.vast.map_vast_gpu", return_value="nvidia-h100-sxm-80"):
                count = scrape_vast.apply().get()
        self.assertEqual(count, 1)
        self.assertEqual(PricingSnapshot.objects.get().hourly_usd, Decimal("1.89"))

    def test_scrape_vast_parser_drift_captured(self) -> None:
        exc = ParserDriftError("vast search API schema changed")
        with patch("pricing.tasks.vast.scrape", side_effect=exc):
            with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
                with self.assertRaises(ParserDriftError):
                    scrape_vast.apply().get()
        mock_msg.assert_called_once_with(str(exc), level="error")

    def test_scrape_vast_generic_exception_retried(self) -> None:
        with patch("pricing.tasks.vast.scrape", side_effect=RuntimeError("vast down")):
            with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
                with patch.object(scrape_vast, "max_retries", 0):
                    result = scrape_vast.apply(throw=False)
        self.assertTrue(result.failed())
        mock_cap.assert_called()


class ScrapeNebiusTaskTest(TestCase):
    def setUp(self) -> None:
        self.nebius_provider = Provider.objects.create(
            slug="nebius", display_name="Nebius", provider_type="cloud", data_source_tier="realtime_api"
        )

    def test_scrape_nebius_writes_snapshot(self) -> None:
        GPUFactory(slug="nvidia-h100-sxm-80")
        prices = [_scraped("nebius", "gpu-h100-sxm", "on_demand", "3.10", region="eu-north1")]
        with patch("pricing.tasks.nebius.scrape", return_value=prices):
            with patch("pricing.tasks.nebius.map_nebius_gpu", return_value="nvidia-h100-sxm-80"):
                count = scrape_nebius.apply().get()
        self.assertEqual(count, 1)
        snap = PricingSnapshot.objects.get()
        self.assertEqual(snap.region, "eu-north1")
        self.assertEqual(snap.hourly_usd, Decimal("3.10"))

    def test_scrape_nebius_parser_drift_captured(self) -> None:
        exc = ParserDriftError("nebius pricing API returned empty items")
        with patch("pricing.tasks.nebius.scrape", side_effect=exc):
            with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
                with self.assertRaises(ParserDriftError):
                    scrape_nebius.apply().get()
        mock_msg.assert_called_once_with(str(exc), level="error")

    def test_scrape_nebius_generic_exception_retried(self) -> None:
        with patch("pricing.tasks.nebius.scrape", side_effect=ConnectionError("nebius unreachable")):
            with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
                with patch.object(scrape_nebius, "max_retries", 0):
                    result = scrape_nebius.apply(throw=False)
        self.assertTrue(result.failed())
        mock_cap.assert_called()


class ScrapeAwsTaskTest(TestCase):
    def setUp(self) -> None:
        self.aws_provider = Provider.objects.create(
            slug="aws", display_name="AWS", provider_type="cloud", data_source_tier="realtime_api"
        )

    def test_scrape_aws_h100_price_written_with_correct_gpu_slug(self) -> None:
        """p5.48xlarge (8xH100) price is already divided by 8 by the scraper; persisted as-is."""
        h100 = GPUFactory(slug="nvidia-h100-sxm-80")
        prices = [_scraped("aws", "p5.48xlarge", "on_demand", "12.29", region="us-east-1")]
        with patch("pricing.tasks.aws.scrape", return_value=prices):
            count = scrape_aws.apply().get()

        self.assertEqual(count, 1)
        snap = PricingSnapshot.objects.get()
        self.assertEqual(snap.gpu, h100)
        self.assertEqual(snap.hourly_usd, Decimal("12.29"))
        self.assertEqual(snap.tier, "on_demand")
        self.assertEqual(snap.region, "us-east-1")

    def test_scrape_aws_multiple_instance_families_separate_gpu_records(self) -> None:
        """H100 and A100 instance families must land under different GPU records."""
        h100 = GPUFactory(slug="nvidia-h100-sxm-80")
        a100 = GPUFactory(slug="nvidia-a100-sxm-40")
        prices = [
            _scraped("aws", "p5.48xlarge", "on_demand", "12.29"),
            _scraped("aws", "p4d.24xlarge", "on_demand", "3.21"),
        ]
        with patch("pricing.tasks.aws.scrape", return_value=prices):
            count = scrape_aws.apply().get()

        self.assertEqual(count, 2)
        self.assertEqual(PricingSnapshot.objects.filter(gpu=h100).count(), 1)
        self.assertEqual(PricingSnapshot.objects.filter(gpu=a100).count(), 1)

    def test_scrape_aws_parser_drift_leaves_db_clean(self) -> None:
        exc = ParserDriftError("AWS Price List API product schema changed")
        with patch("pricing.tasks.aws.scrape", side_effect=exc):
            with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
                with self.assertRaises(ParserDriftError):
                    scrape_aws.apply().get()
        self.assertEqual(PricingSnapshot.objects.count(), 0)
        mock_msg.assert_called_once_with(str(exc), level="error")

    def test_scrape_aws_boto_client_error_captured_and_retried(self) -> None:
        """IAM permission denied for Capacity Blocks → captured to Sentry, task retried."""
        import botocore.exceptions

        client_error = botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "User is not authorized"}},
            "DescribeCapacityReservationFleets",
        )
        with patch("pricing.tasks.aws.scrape", side_effect=client_error):
            with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
                with patch.object(scrape_aws, "max_retries", 0):
                    result = scrape_aws.apply(throw=False)
        self.assertTrue(result.failed())
        mock_cap.assert_called()
        self.assertEqual(PricingSnapshot.objects.count(), 0)

    def test_scrape_aws_generic_network_error_captured_and_retried(self) -> None:
        with patch("pricing.tasks.aws.scrape", side_effect=TimeoutError("bulk pricing fetch timed out")):
            with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
                with patch.object(scrape_aws, "max_retries", 0):
                    result = scrape_aws.apply(throw=False)
        self.assertTrue(result.failed())
        mock_cap.assert_called()


class ScrapeAzureTaskTest(TestCase):
    def setUp(self) -> None:
        self.azure_provider = Provider.objects.create(
            slug="azure", display_name="Azure", provider_type="cloud", data_source_tier="realtime_api"
        )

    def test_scrape_azure_h100_on_demand_and_reserved_written(self) -> None:
        """Azure returns on-demand + 1yr-reserved for ND96isr_H100_v5 — both tiers persisted."""
        h100 = GPUFactory(slug="nvidia-h100-sxm-80")
        prices = [
            _scraped("azure", "Standard_ND96isr_H100_v5", "on_demand", "27.20", region="eastus"),
            _scraped("azure", "Standard_ND96isr_H100_v5", "reserved-1yr", "16.10", region="eastus"),
        ]
        with patch("pricing.tasks.azure.scrape", return_value=prices):
            count = scrape_azure.apply().get()

        self.assertEqual(count, 2)
        snaps = PricingSnapshot.objects.filter(gpu=h100).order_by("hourly_usd")
        self.assertEqual(snaps[0].tier, "reserved-1yr")
        self.assertEqual(snaps[0].hourly_usd, Decimal("16.10"))
        self.assertEqual(snaps[1].tier, "on_demand")
        self.assertEqual(snaps[1].hourly_usd, Decimal("27.20"))

    def test_scrape_azure_parser_drift_leaves_db_clean(self) -> None:
        exc = ParserDriftError("Azure Retail Prices API returned 0 GPU SKUs")
        with patch("pricing.tasks.azure.scrape", side_effect=exc):
            with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
                with self.assertRaises(ParserDriftError):
                    scrape_azure.apply().get()
        self.assertEqual(PricingSnapshot.objects.count(), 0)
        mock_msg.assert_called_once_with(str(exc), level="error")

    def test_scrape_azure_rate_limit_captured_and_retried(self) -> None:
        """HTTP 429 from Azure Retail Prices API → captured to Sentry, task retried."""
        import httpx

        request = httpx.Request("GET", "https://prices.azure.com/api/retail/prices")
        response = httpx.Response(429, request=request)
        http_err = httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)
        with patch("pricing.tasks.azure.scrape", side_effect=http_err):
            with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
                with patch.object(scrape_azure, "max_retries", 0):
                    result = scrape_azure.apply(throw=False)
        self.assertTrue(result.failed())
        mock_cap.assert_called()
        self.assertEqual(PricingSnapshot.objects.count(), 0)

    def test_scrape_azure_generic_exception_captured_and_retried(self) -> None:
        with patch("pricing.tasks.azure.scrape", side_effect=RuntimeError("DNS resolution failed")):
            with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
                with patch.object(scrape_azure, "max_retries", 0):
                    result = scrape_azure.apply(throw=False)
        self.assertTrue(result.failed())
        mock_cap.assert_called()


class RegenerateOnPremSnapshotsTaskTest(TransactionTestCase):
    def test_regenerate_task_tco_exceeds_marginal_because_capex_included(self) -> None:
        """TCO = capex + power + facility + ops. Marginal excludes capex."""
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
        PricingSnapshot.objects.all().delete()

        result = regenerate_on_prem_snapshots_task.apply().get()

        self.assertEqual(result, 2)
        tco_snap = PricingSnapshot.objects.get(tier="tco")
        marginal_snap = PricingSnapshot.objects.get(tier="marginal")

        self.assertGreater(tco_snap.hourly_usd, marginal_snap.hourly_usd)
        self.assertGreater(tco_snap.hourly_usd, Decimal("3.00"))
        self.assertLess(tco_snap.hourly_usd, Decimal("4.00"))
        self.assertEqual(tco_snap.gpu, deployment.hardware_sku.gpu)
        self.assertEqual(marginal_snap.gpu, deployment.hardware_sku.gpu)

    def test_regenerate_task_raw_payload_carries_deployment_slug_for_traceability(self) -> None:
        """Each snapshot's raw_payload must record the deployment_slug."""
        OnPremDeploymentFactory(slug="test-trace-deploy")
        regenerate_on_prem_snapshots_task.apply().get()

        for snap in PricingSnapshot.objects.all():
            self.assertEqual(snap.raw_payload.get("deployment_slug"), "test-trace-deploy")

    def test_regenerate_task_multiple_deployments_produce_independent_snapshots(self) -> None:
        """Two deployments with different cost profiles must produce 4 snapshots."""
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

    def test_regenerate_task_excludes_inactive_deployments(self) -> None:
        """Inactive deployments must not generate snapshots."""
        OnPremDeploymentFactory(is_active=False)
        result = regenerate_on_prem_snapshots_task.apply().get()

        self.assertEqual(result, 0)
        self.assertEqual(PricingSnapshot.objects.count(), 0)


class RefreshCostCellsTaskTest(TransactionTestCase):
    def test_refresh_cost_cells_task_produces_queryable_cost_cells(self) -> None:
        """End-to-end: create a benchmark point + snapshot, run the task, verify cost cell."""
        from decimal import Decimal

        from django.db import connection
        from django.utils import timezone

        from catalog.tests.factories import BenchmarkPointFactory, ModelFactory, QuantizationFactory
        from pricing.tests.factories import PricingSnapshotFactory, ProviderFactory

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
                "SELECT COUNT(*) FROM pricing_current_cost_cells WHERE provider_slug = %s", [provider.slug]
            )
            row_count = c.fetchone()[0]

        self.assertGreater(row_count, 0)


class ComputepricesSanityCheckTaskTest(TestCase):
    def setUp(self) -> None:
        self._orig_key = os.environ.pop("ENABLE_COMPUTEPRICES_DRIFT_CHECK", None)

    def tearDown(self) -> None:
        if self._orig_key is not None:
            os.environ["ENABLE_COMPUTEPRICES_DRIFT_CHECK"] = self._orig_key
        else:
            os.environ.pop("ENABLE_COMPUTEPRICES_DRIFT_CHECK", None)

    def test_computeprices_sanity_check_returns_zero_for_falsy_env_values(self) -> None:
        """Task must return 0 and skip drift check for any non-truthy env value."""
        falsy_values = [None, "0", "false", "no", "off", "FALSE", ""]
        for env_value in falsy_values:
            with self.subTest(env_value=env_value):
                if env_value is None:
                    os.environ.pop("ENABLE_COMPUTEPRICES_DRIFT_CHECK", None)
                else:
                    os.environ["ENABLE_COMPUTEPRICES_DRIFT_CHECK"] = env_value

                with patch("pricing.tasks.check_tier3_drift") as mock_drift:
                    result = computeprices_sanity_check.apply().get()

                self.assertEqual(result, 0)
                mock_drift.assert_not_called()

    def test_computeprices_sanity_check_delegates_to_drift_service_for_truthy_env_values(self) -> None:
        """Task must call check_tier3_drift and return alert count for any truthy env value."""
        truthy_values = ["1", "true", "yes", "on", "TRUE"]
        for env_value in truthy_values:
            with self.subTest(env_value=env_value):
                os.environ["ENABLE_COMPUTEPRICES_DRIFT_CHECK"] = env_value

                with patch(
                    "pricing.tasks.check_tier3_drift", return_value=["alert1", "alert2"]
                ) as mock_drift:
                    result = computeprices_sanity_check.apply().get()

                self.assertEqual(result, 2)
                mock_drift.assert_called_once()
