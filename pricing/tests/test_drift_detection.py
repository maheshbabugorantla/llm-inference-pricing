"""Tests for pricing.services.drift_detection -- Tier 3 price drift detection service.

Business scenario: ComputePrices.com returns an H100 price for a Tier 3 provider.
The service compares that against the curated YAML-seeded price and writes a
PricingDriftAlert when the gap exceeds the 0.5% noise threshold.

All tests patch fetch_computeprices_gpu_prices at the service module boundary.
No real network calls are made.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from catalog.tests.factories import GPUFactory
from pricing.models import PricingDriftAlert, ReservedCapacityProduct
from pricing.services.drift_detection import check_tier3_drift
from pricing.tests.factories import ProviderFactory

_PATCH_TARGET = "pricing.services.drift_detection.fetch_computeprices_gpu_prices"


def _make_tier3_product(
    provider_slug: str,
    gpu_slug: str,
    *,
    per_active_hour_usd: str = "0",
    upfront_usd: str = "0",
    suffix: str = "",
) -> ReservedCapacityProduct:
    """Build a Tier 3 ReservedCapacityProduct in the DB."""
    gpu = GPUFactory(slug=gpu_slug, vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(
        slug=provider_slug,
        provider_type="cloud",
        data_source_tier="manual_curation",
    )
    cadence = "no_upfront" if Decimal(per_active_hour_usd) > 0 else "all_upfront"
    return ReservedCapacityProduct.objects.create(
        slug=f"test-product-{provider_slug}{suffix}",
        display_name=f"Test Product {provider_slug}",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence=cadence,
        term_months=12,
        upfront_usd=Decimal(upfront_usd),
        monthly_recurring_usd=Decimal("0"),
        per_active_hour_usd=Decimal(per_active_hour_usd),
        listing_observed_at="2025-01-15",
    )


class Tier3DriftAlertCreationTest(TestCase):
    """Happy path: alert created when prices diverge."""

    def test_tier3_h100_price_divergence_from_coreweave_creates_drift_alert(self):
        """When ComputePrices.com lists CoreWeave H100 at $3.20/GPU/hr and curated node rate is
        $16.00/node/hr (8 GPUs gives $2.00/GPU/hr), a PricingDriftAlert is written with
        severity=critical and drift_pct=60.000."""
        product = _make_tier3_product(
            "coreweave",
            "nvidia-h100-sxm-80-drft1",
            per_active_hour_usd="16.0000",
            suffix="-drft1",
        )
        fake_rows = [{"provider": "coreweave", "hourly_usd": "3.20"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 1)
        alert = PricingDriftAlert.objects.get(pk=alerts[0].pk)
        self.assertEqual(alert.provider, product.cloud_provider)
        self.assertEqual(alert.gpu, product.gpu)
        self.assertEqual(alert.tier, f"reserved-{product.slug}")
        self.assertEqual(alert.curated_usd_per_hour, Decimal("16.0000") / Decimal("8"))
        self.assertEqual(alert.observed_usd_per_hour, Decimal("3.20"))
        self.assertEqual(alert.drift_pct, Decimal("60.000"))
        self.assertEqual(alert.severity, "critical")
        self.assertGreaterEqual(alert.drift_pct, Decimal("0"))


class Tier3DriftNoiseThresholdTest(TestCase):
    """Noise threshold: no alert when drift is negligible."""

    def test_tier3_h100_price_within_noise_threshold_produces_no_alert(self):
        """When drift is 0.25%, no alert is created -- within 0.5% noise floor."""
        _make_tier3_product(
            "coreweave",
            "nvidia-h100-sxm-80-noise1",
            per_active_hour_usd="16.0000",
            suffix="-noise1",
        )
        fake_rows = [{"provider": "coreweave", "hourly_usd": "2.005"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(alerts, [])
        self.assertEqual(PricingDriftAlert.objects.count(), 0)

    def test_tier3_price_at_exact_noise_threshold_produces_no_alert(self):
        """A drift of exactly 0.5% must NOT create an alert -- the threshold is inclusive."""
        _make_tier3_product(
            "coreweave",
            "nvidia-h100-sxm-80-boundary1",
            per_active_hour_usd="16.0000",
            suffix="-boundary1",
        )
        fake_rows = [{"provider": "coreweave", "hourly_usd": "2.01"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(alerts, [])
        self.assertEqual(PricingDriftAlert.objects.count(), 0)


class Tier3DriftSeverityClassificationTest(TestCase):
    """Severity classification at each threshold."""

    def test_severity_classification_at_each_threshold(self):
        """Severity levels are assigned correctly at info/warning/critical boundaries."""
        cases = [
            ("16.0000", "2.08", "info"),
            ("16.0000", "2.12", "warning"),
            ("16.0000", "2.50", "critical"),
            ("16.0000", "1.60", "critical"),
        ]
        for curated_rate, observed_rate, expected_severity in cases:
            with self.subTest(curated_rate=curated_rate, observed_rate=observed_rate):
                suffix = f"-{curated_rate.replace('.', '')}{observed_rate.replace('.', '')}"
                slug_suffix = suffix[:16]
                product = _make_tier3_product(
                    f"coreweave{slug_suffix}",
                    f"nvidia-h100-sxm-sev{slug_suffix}",
                    per_active_hour_usd=curated_rate,
                    suffix=slug_suffix,
                )
                fake_rows = [{"provider": product.cloud_provider.slug, "hourly_usd": observed_rate}]

                with patch(_PATCH_TARGET, return_value=fake_rows):
                    alerts = check_tier3_drift()

                self.assertEqual(len(alerts), 1)
                self.assertEqual(alerts[0].severity, expected_severity)
                self.assertGreaterEqual(alerts[0].drift_pct, Decimal("0"))

                # Clean up for next subtest iteration
                PricingDriftAlert.objects.all().delete()


class Tier3DriftNoMatchTest(TestCase):
    """No match in aggregator."""

    def test_no_alert_when_no_matching_provider_in_aggregator(self):
        """When ComputePrices.com returns rows only for RunPod but curated product is
        for CoreWeave, no alert is created -- the provider slug does not match."""
        _make_tier3_product(
            "coreweave",
            "nvidia-h100-sxm-80-nomatch",
            per_active_hour_usd="16.0000",
            suffix="-nomatch",
        )
        fake_rows = [{"provider": "runpod", "hourly_usd": "1.99"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(alerts, [])
        self.assertEqual(PricingDriftAlert.objects.count(), 0)


class Tier3DriftFilteringTest(TestCase):
    """Filtering: inactive products and non-Tier-3 providers excluded."""

    def test_tier3_drift_check_ignores_inactive_products(self):
        """Inactive ReservedCapacityProduct rows are excluded from drift checking."""
        product = _make_tier3_product(
            "coreweave",
            "nvidia-h100-sxm-80-inact2",
            per_active_hour_usd="16.0000",
            suffix="-inact2",
        )
        product.is_active = False
        product.save()
        fake_rows = [{"provider": "coreweave", "hourly_usd": "3.50"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(alerts, [])

    def test_tier3_drift_check_ignores_non_manual_curation_providers(self):
        """Products whose cloud_provider has data_source_tier != 'manual_curation' are excluded."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-t1check", vram_gb=80, tdp_watts=700)
        tier1_provider = ProviderFactory(
            slug="runpod-t1-check",
            provider_type="cloud",
            data_source_tier="realtime_api",
        )
        ReservedCapacityProduct.objects.create(
            slug="runpod-h100-1yr-tier1-check",
            display_name="RunPod H100 1yr (Tier 1)",
            cloud_provider=tier1_provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="no_upfront",
            term_months=12,
            per_active_hour_usd=Decimal("2.0000"),
            monthly_recurring_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )
        fake_rows = [{"provider": "runpod-t1-check", "hourly_usd": "3.99"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(alerts, [])


class Tier3DriftEdgeCasesTest(TestCase):
    """Edge cases and guard conditions."""

    def test_tier3_drift_handles_tiny_curated_rate_without_crashing(self):
        """per_active_hour_usd=0.0002 / 8 GPUs = 0.000025/GPU/hr -- tiny rate produces valid alert."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-tiny-rate", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(
            slug="coreweave-tiny-rate",
            provider_type="cloud",
            data_source_tier="manual_curation",
        )
        ReservedCapacityProduct.objects.create(
            slug="coreweave-tiny-rate-product",
            display_name="Tiny Rate Product",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="no_upfront",
            term_months=12,
            per_active_hour_usd=Decimal("0.0002"),
            monthly_recurring_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )
        fake_rows = [{"provider": "coreweave-tiny-rate", "hourly_usd": "0.000030"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "critical")

    def test_tier3_upfront_only_product_amortises_over_full_term(self):
        """For all_upfront products, curated hourly rate = upfront_usd / (term_months * 730h) / gpus_per_node."""
        _make_tier3_product(
            "coreweave",
            "nvidia-h100-sxm-80-upfront1",
            upfront_usd="87600.00",
            suffix="-upfront1",
        )
        fake_rows = [{"provider": "coreweave", "hourly_usd": "1.375"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 1)
        expected_curated = Decimal("87600.00") / (Decimal("12") * Decimal("730")) / Decimal("8")
        self.assertEqual(alerts[0].curated_usd_per_hour, expected_curated)
        self.assertEqual(alerts[0].severity, "warning")

    def test_tier3_drift_is_atomic_no_partial_alerts_on_error(self):
        """If fetch raises mid-loop, no alerts are written."""
        _make_tier3_product(
            "coreweave",
            "nvidia-h100-sxm-80-atm1",
            per_active_hour_usd="16.0000",
            suffix="-atm1",
        )
        _make_tier3_product(
            "crusoe",
            "nvidia-h100-sxm-80-atm2",
            per_active_hour_usd="16.0000",
            suffix="-atm2",
        )

        call_count = 0

        def _fake_fetch(gpu_slug: str) -> list[dict]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"provider": "coreweave", "hourly_usd": "3.50"}]
            raise RuntimeError("simulated transient fetch failure")

        with self.assertRaisesRegex(RuntimeError, "simulated transient fetch failure"):
            with patch(_PATCH_TARGET, side_effect=_fake_fetch):
                check_tier3_drift()

        self.assertEqual(PricingDriftAlert.objects.count(), 0)


class Tier3DriftExcludedProductsTest(TestCase):
    """Products excluded from drift check."""

    def test_tier3_partial_upfront_product_is_excluded_from_drift_check(self):
        """partial_upfront products are excluded to avoid misleading alerts."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-partup", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(
            slug="coreweave-partup-test",
            provider_type="cloud",
            data_source_tier="manual_curation",
        )
        ReservedCapacityProduct.objects.create(
            slug="coreweave-h100-partial-upfront-test",
            display_name="CoreWeave H100 Partial Upfront Test",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="partial_upfront",
            term_months=12,
            upfront_usd=Decimal("20000.00"),
            monthly_recurring_usd=Decimal("5000.00"),
            listing_observed_at="2025-01-15",
        )
        fake_rows = [{"provider": "coreweave-partup-test", "hourly_usd": "3.50"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(alerts, [])
        self.assertEqual(PricingDriftAlert.objects.count(), 0)

    def test_tier3_capacity_block_product_is_excluded_from_drift_check(self):
        """capacity_block products are excluded to avoid misleading alerts."""
        gpu = GPUFactory(slug="nvidia-h100-sxm-80-capblk", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(
            slug="aws-capblk-test",
            provider_type="cloud",
            data_source_tier="manual_curation",
        )
        ReservedCapacityProduct.objects.create(
            slug="aws-p5-capacity-block-test",
            display_name="AWS p5 Capacity Block Test",
            cloud_provider=provider,
            gpu=gpu,
            gpus_per_node=8,
            payment_cadence="capacity_block",
            term_months=1,
            block_duration_hours=336,
            capacity_block_total_usd=Decimal("65856.00"),
            listing_observed_at="2025-01-15",
        )
        fake_rows = [{"provider": "aws-capblk-test", "hourly_usd": "150.00"}]

        with patch(_PATCH_TARGET, return_value=fake_rows):
            alerts = check_tier3_drift()

        self.assertEqual(alerts, [])
        self.assertEqual(PricingDriftAlert.objects.count(), 0)
