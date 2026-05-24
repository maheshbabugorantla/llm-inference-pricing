"""
Business-scenario tests for Celery tasks (pricing/tasks.py).

Design principle: mock at the network boundary (scraper functions), let persist_prices
and the generator run against the real test DB, then assert on actual DB state.
This proves the full ingestion pipeline works — not just that function A called function B.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

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

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def runpod_provider(db):
    return Provider.objects.create(
        slug="runpod", display_name="RunPod", provider_type="cloud", data_source_tier="realtime_api"
    )


@pytest.fixture()
def aws_provider(db):
    return Provider.objects.create(
        slug="aws", display_name="AWS", provider_type="cloud", data_source_tier="realtime_api"
    )


@pytest.fixture()
def azure_provider(db):
    return Provider.objects.create(
        slug="azure", display_name="Azure", provider_type="cloud", data_source_tier="realtime_api"
    )


@pytest.fixture()
def lambda_provider(db):
    return Provider.objects.create(
        slug="lambda", display_name="Lambda Labs", provider_type="cloud", data_source_tier="realtime_api"
    )


@pytest.fixture()
def nebius_provider(db):
    return Provider.objects.create(
        slug="nebius", display_name="Nebius", provider_type="cloud", data_source_tier="realtime_api"
    )


@pytest.fixture()
def vast_provider(db):
    return Provider.objects.create(
        slug="vast", display_name="Vast.ai", provider_type="cloud", data_source_tier="realtime_api"
    )


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


# ── RunPod ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_runpod_writes_multi_tier_snapshots(runpod_provider):
    """RunPod returns community + secure prices for H100 — both tiers must land in DB."""
    h100 = GPUFactory(slug="nvidia-h100-sxm-80")
    prices = [
        _scraped("runpod", "H100 SXM", "community", "2.49"),
        _scraped("runpod", "H100 SXM", "secure", "3.49"),
    ]
    with patch("pricing.tasks.runpod.scrape", return_value=prices):
        count = scrape_runpod.apply().get()

    assert count == 2
    snaps = PricingSnapshot.objects.filter(provider=runpod_provider, gpu=h100).order_by("hourly_usd")
    assert snaps.count() == 2
    assert snaps[0].tier == "community"
    assert snaps[0].hourly_usd == Decimal("2.49")
    assert snaps[1].tier == "secure"
    assert snaps[1].hourly_usd == Decimal("3.49")
    # Timestamps must be timezone-aware
    assert all(s.scraped_at.tzinfo is not None for s in snaps)


@pytest.mark.django_db
def test_scrape_runpod_handles_mixed_gpu_batch(runpod_provider):
    """Batch with H100 and A100 → two separate GPU slugs persisted correctly."""
    h100 = GPUFactory(slug="nvidia-h100-sxm-80")
    a100 = GPUFactory(slug="nvidia-a100-sxm-80")
    prices = [
        _scraped("runpod", "H100 SXM", "on_demand", "2.49"),
        _scraped("runpod", "A100 SXM", "on_demand", "1.19"),
    ]
    with patch("pricing.tasks.runpod.scrape", return_value=prices):
        count = scrape_runpod.apply().get()

    assert count == 2
    assert PricingSnapshot.objects.filter(gpu=h100).count() == 1
    assert PricingSnapshot.objects.filter(gpu=a100).count() == 1


@pytest.mark.django_db
def test_scrape_runpod_silently_drops_unmapped_gpu(runpod_provider):
    """Unknown GPU hint must be silently skipped — no snapshot written, no exception."""
    prices = [_scraped("runpod", "SomeUnknownGPU-9000", "on_demand", "99.00")]
    with patch("pricing.tasks.runpod.scrape", return_value=prices):
        count = scrape_runpod.apply().get()

    assert count == 0
    assert PricingSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_scrape_runpod_partial_batch_skips_unknown_keeps_known(runpod_provider):
    """Mixed batch: known GPU persisted, unknown GPU silently dropped."""
    GPUFactory(slug="nvidia-h100-sxm-80")
    prices = [
        _scraped("runpod", "H100 SXM", "community", "2.49"),
        _scraped("runpod", "FutureTechGPU", "community", "50.00"),
    ]
    with patch("pricing.tasks.runpod.scrape", return_value=prices):
        count = scrape_runpod.apply().get()

    assert count == 1
    assert PricingSnapshot.objects.get().hourly_usd == Decimal("2.49")


@pytest.mark.django_db
def test_scrape_runpod_parser_drift_leaves_db_clean_and_alerts_sentry(runpod_provider):
    """Schema drift must not write partial data and must notify Sentry with exact message."""
    GPUFactory(slug="nvidia-h100-sxm-80")
    exc = ParserDriftError("runpod graphql field 'gpuTypes' disappeared")

    with patch("pricing.tasks.runpod.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                scrape_runpod.apply().get()

    assert PricingSnapshot.objects.count() == 0
    mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_runpod_network_failure_alerts_sentry_and_retries(runpod_provider):
    """Transient network error → Sentry capture + retry (max_retries=0 to short-circuit)."""
    exc = OSError("connection reset by peer")

    with patch("pricing.tasks.runpod.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            with patch.object(scrape_runpod, "max_retries", 0):
                result = scrape_runpod.apply(throw=False)

    assert result.failed()
    mock_cap.assert_called()
    assert PricingSnapshot.objects.count() == 0


# ── Lambda Labs ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_lambda_writes_on_demand_snapshot(lambda_provider):
    """Lambda Labs H100 on-demand price lands in DB with correct provider and GPU."""
    h100 = GPUFactory(slug="nvidia-h100-sxm-80")
    prices = [_scraped("lambda", "gpu_8x_h100_sxm5", "on_demand", "28.00", region="us-south-1")]
    with patch("pricing.tasks.lambda_labs.scrape", return_value=prices):
        with patch("pricing.tasks.lambda_labs.map_lambda_gpu", return_value="nvidia-h100-sxm-80"):
            count = scrape_lambda.apply().get()

    assert count == 1
    snap = PricingSnapshot.objects.get()
    assert snap.gpu == h100
    assert snap.hourly_usd == Decimal("28.00")
    assert snap.region == "us-south-1"


@pytest.mark.django_db
def test_scrape_lambda_parser_drift_captured(lambda_provider):
    exc = ParserDriftError("lambda pricing page structure changed")
    with patch("pricing.tasks.lambda_labs.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                scrape_lambda.apply().get()
    mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_lambda_generic_exception_retried(lambda_provider):
    exc = TimeoutError("lambda API timed out after 30s")
    with patch("pricing.tasks.lambda_labs.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            with patch.object(scrape_lambda, "max_retries", 0):
                result = scrape_lambda.apply(throw=False)
    assert result.failed()
    mock_cap.assert_called()


# ── Vast.ai ────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_vast_writes_community_snapshot(vast_provider):
    GPUFactory(slug="nvidia-h100-sxm-80")
    prices = [_scraped("vast", "H100 SXM5 80GB", "on_demand", "1.89")]
    with patch("pricing.tasks.vast.scrape", return_value=prices):
        with patch("pricing.tasks.vast.map_vast_gpu", return_value="nvidia-h100-sxm-80"):
            count = scrape_vast.apply().get()
    assert count == 1
    assert PricingSnapshot.objects.get().hourly_usd == Decimal("1.89")


@pytest.mark.django_db
def test_scrape_vast_parser_drift_captured(vast_provider):
    exc = ParserDriftError("vast search API schema changed")
    with patch("pricing.tasks.vast.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                scrape_vast.apply().get()
    mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_vast_generic_exception_retried(vast_provider):
    with patch("pricing.tasks.vast.scrape", side_effect=RuntimeError("vast down")):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            with patch.object(scrape_vast, "max_retries", 0):
                result = scrape_vast.apply(throw=False)
    assert result.failed()
    mock_cap.assert_called()


# ── Nebius ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_nebius_writes_snapshot(nebius_provider):
    GPUFactory(slug="nvidia-h100-sxm-80")
    prices = [_scraped("nebius", "gpu-h100-sxm", "on_demand", "3.10", region="eu-north1")]
    with patch("pricing.tasks.nebius.scrape", return_value=prices):
        with patch("pricing.tasks.nebius.map_nebius_gpu", return_value="nvidia-h100-sxm-80"):
            count = scrape_nebius.apply().get()
    assert count == 1
    snap = PricingSnapshot.objects.get()
    assert snap.region == "eu-north1"
    assert snap.hourly_usd == Decimal("3.10")


@pytest.mark.django_db
def test_scrape_nebius_parser_drift_captured(nebius_provider):
    exc = ParserDriftError("nebius pricing API returned empty items")
    with patch("pricing.tasks.nebius.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                scrape_nebius.apply().get()
    mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_nebius_generic_exception_retried(nebius_provider):
    with patch("pricing.tasks.nebius.scrape", side_effect=ConnectionError("nebius unreachable")):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            with patch.object(scrape_nebius, "max_retries", 0):
                result = scrape_nebius.apply(throw=False)
    assert result.failed()
    mock_cap.assert_called()


# ── AWS ────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_aws_h100_price_written_with_correct_gpu_slug(aws_provider):
    """p5.48xlarge (8xH100) price is already divided by 8 by the scraper; persisted as-is."""
    h100 = GPUFactory(slug="nvidia-h100-sxm-80")
    # $98.32/hr for the node ÷ 8 GPUs = $12.29/GPU/hr — scraper has already divided
    prices = [_scraped("aws", "p5.48xlarge", "on_demand", "12.29", region="us-east-1")]
    with patch("pricing.tasks.aws.scrape", return_value=prices):
        count = scrape_aws.apply().get()

    assert count == 1
    snap = PricingSnapshot.objects.get()
    assert snap.gpu == h100
    assert snap.hourly_usd == Decimal("12.29")
    assert snap.tier == "on_demand"
    assert snap.region == "us-east-1"


@pytest.mark.django_db
def test_scrape_aws_multiple_instance_families_separate_gpu_records(aws_provider):
    """H100 and A100 instance families must land under different GPU records."""
    h100 = GPUFactory(slug="nvidia-h100-sxm-80")
    a100 = GPUFactory(slug="nvidia-a100-sxm-40")
    prices = [
        _scraped("aws", "p5.48xlarge", "on_demand", "12.29"),  # H100
        _scraped("aws", "p4d.24xlarge", "on_demand", "3.21"),  # A100 40GB
    ]
    with patch("pricing.tasks.aws.scrape", return_value=prices):
        count = scrape_aws.apply().get()

    assert count == 2
    assert PricingSnapshot.objects.filter(gpu=h100).count() == 1
    assert PricingSnapshot.objects.filter(gpu=a100).count() == 1


@pytest.mark.django_db
def test_scrape_aws_parser_drift_leaves_db_clean(aws_provider):
    exc = ParserDriftError("AWS Price List API product schema changed")
    with patch("pricing.tasks.aws.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                scrape_aws.apply().get()
    assert PricingSnapshot.objects.count() == 0
    mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_aws_boto_client_error_captured_and_retried(aws_provider):
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
    assert result.failed()
    mock_cap.assert_called()
    assert PricingSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_scrape_aws_generic_network_error_captured_and_retried(aws_provider):
    with patch("pricing.tasks.aws.scrape", side_effect=TimeoutError("bulk pricing fetch timed out")):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            with patch.object(scrape_aws, "max_retries", 0):
                result = scrape_aws.apply(throw=False)
    assert result.failed()
    mock_cap.assert_called()


# ── Azure ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_azure_h100_on_demand_and_reserved_written(azure_provider):
    """Azure returns on-demand + 1yr-reserved for ND96isr_H100_v5 — both tiers persisted."""
    h100 = GPUFactory(slug="nvidia-h100-sxm-80")
    prices = [
        _scraped("azure", "Standard_ND96isr_H100_v5", "on_demand", "27.20", region="eastus"),
        _scraped("azure", "Standard_ND96isr_H100_v5", "reserved-1yr", "16.10", region="eastus"),
    ]
    with patch("pricing.tasks.azure.scrape", return_value=prices):
        count = scrape_azure.apply().get()

    assert count == 2
    snaps = PricingSnapshot.objects.filter(gpu=h100).order_by("hourly_usd")
    assert snaps[0].tier == "reserved-1yr"
    assert snaps[0].hourly_usd == Decimal("16.10")
    assert snaps[1].tier == "on_demand"
    assert snaps[1].hourly_usd == Decimal("27.20")


@pytest.mark.django_db
def test_scrape_azure_parser_drift_leaves_db_clean(azure_provider):
    exc = ParserDriftError("Azure Retail Prices API returned 0 GPU SKUs")
    with patch("pricing.tasks.azure.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                scrape_azure.apply().get()
    assert PricingSnapshot.objects.count() == 0
    mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_azure_rate_limit_captured_and_retried(azure_provider):
    """HTTP 429 from Azure Retail Prices API → captured to Sentry, task retried."""
    request = __import__("httpx").Request("GET", "https://prices.azure.com/api/retail/prices")
    response = __import__("httpx").Response(429, request=request)
    http_err = __import__("httpx").HTTPStatusError(
        "429 Too Many Requests", request=request, response=response
    )
    with patch("pricing.tasks.azure.scrape", side_effect=http_err):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            with patch.object(scrape_azure, "max_retries", 0):
                result = scrape_azure.apply(throw=False)
    assert result.failed()
    mock_cap.assert_called()
    assert PricingSnapshot.objects.count() == 0


@pytest.mark.django_db
def test_scrape_azure_generic_exception_captured_and_retried(azure_provider):
    with patch("pricing.tasks.azure.scrape", side_effect=RuntimeError("DNS resolution failed")):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            with patch.object(scrape_azure, "max_retries", 0):
                result = scrape_azure.apply(throw=False)
    assert result.failed()
    mock_cap.assert_called()


# ── regenerate_on_prem_snapshots_task ─────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_regenerate_task_tco_exceeds_marginal_because_capex_included():
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

    assert result == 2
    tco_snap = PricingSnapshot.objects.get(tier="tco")
    marginal_snap = PricingSnapshot.objects.get(tier="marginal")

    # TCO must exceed marginal — capex is the difference
    assert tco_snap.hourly_usd > marginal_snap.hourly_usd

    # Per-GPU TCO should be in the right ballpark (~$3.45/hr for this config)
    assert Decimal("3.00") < tco_snap.hourly_usd < Decimal("4.00")

    # Snapshot is linked to the correct GPU
    assert tco_snap.gpu == deployment.hardware_sku.gpu
    assert marginal_snap.gpu == deployment.hardware_sku.gpu


@pytest.mark.django_db(transaction=True)
def test_regenerate_task_raw_payload_carries_deployment_slug_for_traceability():
    """Each snapshot's raw_payload must record the deployment_slug so engineers
    can trace a price back to its source deployment config."""
    OnPremDeploymentFactory(slug="test-trace-deploy")
    regenerate_on_prem_snapshots_task.apply().get()

    for snap in PricingSnapshot.objects.all():
        assert snap.raw_payload.get("deployment_slug") == "test-trace-deploy"


@pytest.mark.django_db(transaction=True)
def test_regenerate_task_multiple_deployments_produce_independent_snapshots():
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

    assert result == 4
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
    assert expensive_tco > cheap_tco


@pytest.mark.django_db(transaction=True)
def test_regenerate_task_excludes_inactive_deployments():
    """Inactive deployments must not generate snapshots — they represent
    decommissioned hardware that should drop out of cost comparisons."""
    OnPremDeploymentFactory(is_active=False)
    result = regenerate_on_prem_snapshots_task.apply().get()

    assert result == 0
    assert PricingSnapshot.objects.count() == 0


# ── refresh_current_cost_cells ────────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_refresh_cost_cells_task_produces_queryable_cost_cells():
    """End-to-end: create a benchmark point + snapshot, run the task,
    then verify a cost cell row is readable from the materialized view."""
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
        c.execute("SELECT COUNT(*) FROM pricing_current_cost_cells WHERE provider_id = %s", [provider.id])
        row_count = c.fetchone()[0]

    assert row_count > 0


# ---------------------------------------------------------------------------
# M10.T04 — computeprices_sanity_check task
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_value", [None, "0", "false", "no", "off", "FALSE", ""])
def test_computeprices_sanity_check_returns_zero_for_falsy_env_values(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
) -> None:
    """Task must return 0 and skip drift check for any non-truthy env value.
    Critically, '0' must be treated as disabled — not as enabled — so an operator
    can explicitly turn the flag off without removing the variable entirely."""
    if env_value is None:
        monkeypatch.delenv("ENABLE_COMPUTEPRICES_DRIFT_CHECK", raising=False)
    else:
        monkeypatch.setenv("ENABLE_COMPUTEPRICES_DRIFT_CHECK", env_value)

    with patch("pricing.tasks.check_tier3_drift") as mock_drift:
        result = computeprices_sanity_check.apply().get()

    assert result == 0
    mock_drift.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("env_value", ["1", "true", "yes", "on", "TRUE"])
def test_computeprices_sanity_check_delegates_to_drift_service_for_truthy_env_values(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
) -> None:
    """Task must call check_tier3_drift and return alert count for any truthy env value."""
    monkeypatch.setenv("ENABLE_COMPUTEPRICES_DRIFT_CHECK", env_value)

    with patch("pricing.tasks.check_tier3_drift", return_value=["alert1", "alert2"]) as mock_drift:
        result = computeprices_sanity_check.apply().get()

    assert result == 2
    mock_drift.assert_called_once()
