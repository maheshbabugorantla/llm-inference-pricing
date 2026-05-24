"""Transaction-isolated drift-detection tests using Django TestCase.

django.test.TestCase wraps every test method in its own database savepoint so
state from one test never bleeds into the next. This removes explicit teardown
and guarantees isolation even for @transaction.atomic code paths.

Use TestCase when:
  - Many test methods share expensive class-level setup (setUpTestData)
  - You want isolation via savepoint rollback rather than full DB flush

Use TransactionTestCase (see DriftAlertAtomicityTest below) when:
  - You need to observe whether a real COMMIT or ROLLBACK occurred, because
    TestCase's outer transaction prevents inner atomic blocks from ever issuing
    a real commit — which also means a failed inner atomic sets
    connection.needs_rollback on the outer transaction and breaks subsequent
    queries in the same test method.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase

from catalog.tests.factories import GPUFactory
from pricing.models import PricingDriftAlert, ReservedCapacityProduct
from pricing.services.drift_detection import _write_drift_alerts, check_tier3_drift
from pricing.tests.factories import ProviderFactory

_PATCH_TARGET = "pricing.services.drift_detection.fetch_computeprices_gpu_prices"

# Curated setup: 8-GPU CoreWeave node at $20/hr → $2.50/GPU/hr
_NODE_RATE = Decimal("20.00")
_GPUS_PER_NODE = 8
_CURATED_PER_GPU = _NODE_RATE / _GPUS_PER_NODE  # Decimal("2.50")


class DriftDetectionServiceTest(TestCase):
    """Business-scenario tests for check_tier3_drift() using shared class-level data.

    setUpTestData() creates one canonical tier-3 product once for the class.
    Each test method gets a savepoint that rolls back after the method returns,
    so alert rows created in one test are invisible to the next.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.gpu = GPUFactory(slug="nvidia-h100-sxm-80-txtest", vram_gb=80, tdp_watts=700)
        cls.provider = ProviderFactory(
            slug="coreweave-txtest",
            provider_type="cloud",
            data_source_tier="manual_curation",
        )
        cls.product = ReservedCapacityProduct.objects.create(
            slug="coreweave-h100-no-upfront-txtest",
            display_name="CoreWeave H100 No Upfront",
            cloud_provider=cls.provider,
            gpu=cls.gpu,
            gpus_per_node=_GPUS_PER_NODE,
            payment_cadence="no_upfront",
            term_months=12,
            per_active_hour_usd=_NODE_RATE,
            monthly_recurring_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )

    def _api_row(self, hourly_usd: str) -> list[dict]:
        return [{"provider": "coreweave-txtest", "hourly_usd": hourly_usd}]

    def test_observed_price_matching_curated_rate_creates_no_alert(self) -> None:
        """Observed == curated ($2.50) means 0% drift — below the 0.5% noise floor.
        No alert is written to the DB, so operators are not flooded by noise."""
        with patch(_PATCH_TARGET, return_value=self._api_row(str(_CURATED_PER_GPU))):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 0)
        self.assertEqual(PricingDriftAlert.objects.count(), 0)

    def test_twenty_five_percent_drift_creates_critical_alert_in_db(self) -> None:
        """$3.125 observed vs $2.50 curated = 25% drift → critical (≥ 20%).
        The alert is persisted and queryable by provider + GPU — this is the
        DB state the admin dashboard reads to surface pricing anomalies."""
        with patch(_PATCH_TARGET, return_value=self._api_row("3.125")):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 1)
        alert = PricingDriftAlert.objects.get(provider=self.provider, gpu=self.gpu)
        self.assertEqual(alert.severity, "critical")
        self.assertEqual(alert.curated_usd_per_hour, Decimal("2.5000"))
        self.assertEqual(alert.observed_usd_per_hour, Decimal("3.1250"))
        self.assertGreater(alert.drift_pct, Decimal("20"))

    def test_seven_percent_drift_creates_warning_alert(self) -> None:
        """$2.675 observed vs $2.50 curated = 7% drift -> warning (5%-20% band).
        Warning triggers a Slack notification but not an on-call page."""
        with patch(_PATCH_TARGET, return_value=self._api_row("2.675")):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "warning")

    def test_two_percent_drift_creates_info_alert(self) -> None:
        """$2.55 observed vs $2.50 curated = 2% drift -> info (0.5%-5% band).
        Info alerts are recorded but do not page anyone."""
        with patch(_PATCH_TARGET, return_value=self._api_row("2.55")):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "info")

    def test_sub_noise_drift_suppressed_and_no_db_row_written(self) -> None:
        """$2.507 vs $2.50 = 0.28% — below the 0.5% noise floor.
        No alert written, so operators aren't flooded by rounding noise and
        the admin dashboard stays uncluttered."""
        with patch(_PATCH_TARGET, return_value=self._api_row("2.507")):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 0)
        self.assertEqual(PricingDriftAlert.objects.count(), 0)

    def test_provider_absent_from_api_response_creates_no_alert(self) -> None:
        """When ComputePrices has no row for this provider, the product is silently
        skipped — no alert, no error. A gap in external coverage is not drift."""
        wrong_provider_row = [{"provider": "lambda", "hourly_usd": "3.00"}]
        with patch(_PATCH_TARGET, return_value=wrong_provider_row):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 0)

    def test_alert_monetary_fields_stored_as_decimal_not_float(self) -> None:
        """curated_usd_per_hour, observed_usd_per_hour, and drift_pct must survive
        a DB round-trip as Decimal. A float would introduce precision errors that
        corrupt cost comparisons downstream."""
        with patch(_PATCH_TARGET, return_value=self._api_row("3.125")):
            alerts = check_tier3_drift()

        fetched = PricingDriftAlert.objects.get(pk=alerts[0].pk)
        self.assertIsInstance(fetched.curated_usd_per_hour, Decimal)
        self.assertIsInstance(fetched.observed_usd_per_hour, Decimal)
        self.assertIsInstance(fetched.drift_pct, Decimal)

    def test_multiple_alerts_created_when_multiple_products_drift(self) -> None:
        """Two drifting tier-3 products produce two independent alerts in one run.
        Both must land in the DB — the atomic write is all-or-nothing but the happy
        path should commit all alerts together."""
        second_gpu = GPUFactory(slug="nvidia-a100-sxm-80-txtest", vram_gb=80, tdp_watts=400)
        second_provider = ProviderFactory(
            slug="lambda-txtest",
            provider_type="cloud",
            data_source_tier="manual_curation",
        )
        ReservedCapacityProduct.objects.create(
            slug="lambda-a100-txtest",
            display_name="Lambda A100 Reserved",
            cloud_provider=second_provider,
            gpu=second_gpu,
            gpus_per_node=8,
            payment_cadence="no_upfront",
            term_months=12,
            per_active_hour_usd=Decimal("12.00"),  # $1.50/GPU/hr
            monthly_recurring_usd=Decimal("0"),
            listing_observed_at="2025-01-15",
        )

        def fake_fetch(gpu_slug: str) -> list[dict]:
            if "h100" in gpu_slug:
                return [{"provider": "coreweave-txtest", "hourly_usd": "3.125"}]
            return [{"provider": "lambda-txtest", "hourly_usd": "1.95"}]  # ~30% drift

        with patch(_PATCH_TARGET, side_effect=fake_fetch):
            alerts = check_tier3_drift()

        self.assertEqual(len(alerts), 2)
        self.assertEqual(PricingDriftAlert.objects.count(), 2)


class DriftAlertAtomicityTest(TransactionTestCase):
    """Verifies @transaction.atomic rollback in _write_drift_alerts.

    TransactionTestCase (not TestCase) is required here because TestCase wraps
    each test in an outer transaction, which causes a failed inner @transaction.atomic
    block to set connection.needs_rollback=True on the outer transaction — making
    further DB queries in the same test method impossible. TransactionTestCase
    resets the DB between tests via flush rather than savepoints, so the inner
    atomic block gets its own real transaction and a failed rollback leaves the
    connection in a clean autocommit state.
    """

    def test_write_drift_alerts_rolls_back_all_alerts_on_mid_write_exception(self) -> None:
        """If _write_drift_alerts raises on the Nth alert, the @transaction.atomic
        block must roll back all N-1 previously created alerts. Zero rows must
        remain so operators never see a partial, half-created batch of alerts."""
        gpu = GPUFactory(slug="nvidia-h100-atomicity-test", vram_gb=80, tdp_watts=700)
        provider = ProviderFactory(
            slug="coreweave-atomicity",
            provider_type="cloud",
            data_source_tier="manual_curation",
        )
        spec_base: dict = {
            "provider": provider,
            "gpu": gpu,
            "curated_usd_per_hour": Decimal("2.5000"),
            "observed_usd_per_hour": Decimal("3.1250"),
            "drift_pct": Decimal("25.000"),
            "source_url": "https://computeprices.com/gpu-prices",
            "severity": "critical",
        }
        two_specs = [
            {**spec_base, "tier": "reserved-product-alpha", "_product_slug": "product-alpha"},
            {**spec_base, "tier": "reserved-product-beta", "_product_slug": "product-beta"},
        ]

        call_count = 0
        real_create = PricingDriftAlert.objects.create

        def create_then_explode(**kwargs: object) -> PricingDriftAlert:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise RuntimeError("simulated write failure on second alert")
            return real_create(**kwargs)

        with patch.object(PricingDriftAlert.objects, "create", side_effect=create_then_explode):
            with self.assertRaises(RuntimeError):
                _write_drift_alerts(two_specs)

        # The atomic block must have rolled back — zero alerts in the DB
        self.assertEqual(PricingDriftAlert.objects.count(), 0)
        self.assertEqual(call_count, 2, "Both specs must have been attempted before the failure")
