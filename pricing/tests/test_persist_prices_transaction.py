"""Transaction-isolated tests for persist_prices() using Django TestCase.

persist_prices() is @transaction.atomic — every snapshot in a batch is committed
together or not at all. TestCase covers the happy-path scenarios using setUpTestData
so the Provider + GPU rows are created once for the class. TransactionTestCase covers
rollback guarantees because a failed inner atomic block inside TestCase sets
connection.needs_rollback on the outer savepoint, preventing further queries.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider
from pricing.scrapers.base import ScrapedPrice
from pricing.services.scrape_runner import persist_prices
from pricing.tests.factories import ProviderFactory


def _price(
    provider_slug: str,
    gpu_slug_hint: str,
    tier: str,
    hourly_usd: str,
    region: str = "",
) -> ScrapedPrice:
    return ScrapedPrice(
        provider_slug=provider_slug,
        gpu_slug_hint=gpu_slug_hint,
        tier=tier,
        hourly_usd=Decimal(hourly_usd),
        region=region,
        raw={"hint": gpu_slug_hint, "tier": tier},
    )


class PersistPricesBatchTest(TestCase):
    """Happy-path batch scenarios for persist_prices().

    setUpTestData creates Provider + two GPU rows once for the class.
    Each test method runs in its own savepoint so snapshots created in one
    test are invisible to the next.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.provider = ProviderFactory(
            slug="runpod-pp",
            provider_type="cloud",
            data_source_tier="realtime_api",
        )
        cls.h100 = GPUFactory(slug="nvidia-h100-sxm-80-pp", vram_gb=80, tdp_watts=700)
        cls.a100 = GPUFactory(slug="nvidia-a100-sxm-80-pp", vram_gb=80, tdp_watts=400)

    def _resolver(self, hint: str) -> str | None:
        return {
            "H100 SXM": "nvidia-h100-sxm-80-pp",
            "A100 SXM": "nvidia-a100-sxm-80-pp",
        }.get(hint)

    def test_batch_of_prices_creates_one_snapshot_per_mapped_gpu(self) -> None:
        """Two ScrapedPrice rows for two GPUs must land as two independent PricingSnapshot
        rows — both committed in the same atomic write, both queryable immediately after."""
        prices = [
            _price("runpod-pp", "H100 SXM", "community", "2.49"),
            _price("runpod-pp", "A100 SXM", "community", "1.29"),
        ]
        count = persist_prices(prices, gpu_slug_resolver=self._resolver)

        self.assertEqual(count, 2)
        self.assertEqual(PricingSnapshot.objects.count(), 2)
        self.assertTrue(PricingSnapshot.objects.filter(gpu=self.h100).exists())
        self.assertTrue(PricingSnapshot.objects.filter(gpu=self.a100).exists())

    def test_multi_tier_prices_for_same_gpu_stored_as_independent_rows(self) -> None:
        """Community and secure tiers for the same GPU must be stored as separate rows.
        Collapsing them into one would silently drop a tier from the cost grid."""
        prices = [
            _price("runpod-pp", "H100 SXM", "community", "1.99"),
            _price("runpod-pp", "H100 SXM", "secure", "3.29"),
        ]
        persist_prices(prices, gpu_slug_resolver=self._resolver)

        snaps = PricingSnapshot.objects.filter(gpu=self.h100)
        self.assertEqual(snaps.count(), 2)
        tiers = set(snaps.values_list("tier", flat=True))
        self.assertIn("community", tiers)
        self.assertIn("secure", tiers)

    def test_unmapped_hint_skipped_remaining_batch_still_committed(self) -> None:
        """An unmapped GPU hint must be silently skipped. Other GPUs in the same
        batch must land in the DB — the skip must not abort the whole batch."""
        prices = [
            _price("runpod-pp", "UnobtaniumGPU", "community", "99.99"),  # unmapped
            _price("runpod-pp", "H100 SXM", "community", "2.49"),
        ]
        count = persist_prices(prices, gpu_slug_resolver=self._resolver)

        self.assertEqual(count, 1)
        self.assertEqual(PricingSnapshot.objects.count(), 1)
        self.assertTrue(PricingSnapshot.objects.filter(gpu=self.h100).exists())

    def test_snapshot_hourly_usd_stored_as_decimal_after_db_round_trip(self) -> None:
        """hourly_usd must survive the DB round-trip as Decimal. A float would
        introduce precision errors that corrupt downstream cost arithmetic."""
        prices = [_price("runpod-pp", "H100 SXM", "community", "2.4900")]
        persist_prices(prices, gpu_slug_resolver=self._resolver)

        snap = PricingSnapshot.objects.get(gpu=self.h100)
        self.assertIsInstance(snap.hourly_usd, Decimal)
        self.assertEqual(snap.hourly_usd, Decimal("2.4900"))

    def test_custom_scraped_at_timestamp_preserved_in_snapshot(self) -> None:
        """When persist_prices receives a scraped_at argument, that exact timestamp
        must be stored on the snapshot — not timezone.now() — so load_pricing
        can replay historical pricing data with the original capture time."""
        fixed_at = dt.datetime(2025, 3, 15, 12, 0, 0, tzinfo=dt.UTC)
        prices = [_price("runpod-pp", "H100 SXM", "community", "2.49")]
        persist_prices(prices, gpu_slug_resolver=self._resolver, scraped_at=fixed_at)

        snap = PricingSnapshot.objects.get(gpu=self.h100)
        self.assertEqual(snap.scraped_at, fixed_at)

    def test_snapshot_raw_payload_preserved_from_scraped_price(self) -> None:
        """raw from ScrapedPrice must land verbatim in snapshot.raw_payload.
        Any mutation or truncation would corrupt the audit trail."""
        raw = {"displayName": "H100 SXM", "communityPrice": "2.49", "region": "us-east"}
        price = ScrapedPrice(
            provider_slug="runpod-pp",
            gpu_slug_hint="H100 SXM",
            tier="community",
            hourly_usd=Decimal("2.49"),
            region="us-east",
            raw=raw,
        )
        persist_prices([price], gpu_slug_resolver=self._resolver)

        snap = PricingSnapshot.objects.get(gpu=self.h100)
        self.assertEqual(snap.raw_payload, raw)

    def test_return_value_equals_number_of_snapshots_created(self) -> None:
        """persist_prices must return the count of snapshots written so callers
        can log or validate batch completeness without a second DB query."""
        prices = [
            _price("runpod-pp", "H100 SXM", "community", "2.49"),
            _price("runpod-pp", "H100 SXM", "secure", "3.29"),
            _price("runpod-pp", "A100 SXM", "community", "1.29"),
        ]
        count = persist_prices(prices, gpu_slug_resolver=self._resolver)
        self.assertEqual(count, PricingSnapshot.objects.count())


class PersistPricesAtomicityTest(TransactionTestCase):
    """Rollback guarantee tests for persist_prices() @transaction.atomic block.

    TransactionTestCase is required: a failed inner atomic block within TestCase
    sets connection.needs_rollback=True on the outer transaction, blocking the
    subsequent count() assertions needed to verify rollback occurred.
    """

    def test_unknown_provider_slug_raises_does_not_exist_and_leaves_db_clean(self) -> None:
        """If the Provider slug from ScrapedPrice doesn't exist in the DB, persist_prices
        raises Provider.DoesNotExist. The @transaction.atomic block rolls back — zero
        snapshots remain so no orphaned rows accumulate between failed scraper runs."""
        GPUFactory(slug="nvidia-h100-sxm-80-atomic", vram_gb=80, tdp_watts=700)
        # Intentionally no Provider created

        prices = [_price("ghost-provider", "H100 SXM", "community", "2.49")]

        with self.assertRaises(Provider.DoesNotExist):
            persist_prices(prices, gpu_slug_resolver=lambda hint: "nvidia-h100-sxm-80-atomic")

        self.assertEqual(PricingSnapshot.objects.count(), 0)

    def test_mid_batch_exception_rolls_back_all_previously_created_snapshots(self) -> None:
        """persist_prices is @transaction.atomic so any exception mid-batch must roll back
        every snapshot created so far. Operators see all-or-nothing pricing updates —
        no partially-ingested batches that would skew cost cells."""
        ProviderFactory(slug="runpod-atomic", data_source_tier="realtime_api")
        GPUFactory(slug="nvidia-h100-sxm-80-atomic2", vram_gb=80, tdp_watts=700)

        call_count = 0
        real_create = PricingSnapshot.objects.create

        def create_then_fail(**kwargs: object) -> PricingSnapshot:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise RuntimeError("simulated storage failure on second snapshot")
            return real_create(**kwargs)

        prices = [
            _price("runpod-atomic", "H100 SXM", "community", "1.99"),
            _price("runpod-atomic", "H100 SXM", "secure", "3.29"),
        ]

        def resolver(hint: str) -> str:
            return "nvidia-h100-sxm-80-atomic2"

        with patch.object(PricingSnapshot.objects, "create", side_effect=create_then_fail):
            with self.assertRaises(RuntimeError):
                persist_prices(prices, gpu_slug_resolver=resolver)

        self.assertEqual(PricingSnapshot.objects.count(), 0)
        self.assertEqual(call_count, 2, "Both create attempts must have been made")
