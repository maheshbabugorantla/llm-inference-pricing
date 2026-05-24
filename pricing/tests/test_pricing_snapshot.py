from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from pricing.tests.factories import PricingSnapshotFactory


class PricingSnapshotTest(TestCase):
    def test_pricing_snapshot_stores_decimal_hourly_usd(self):
        snap = PricingSnapshotFactory(hourly_usd=Decimal("2.49"))
        snap.refresh_from_db()
        self.assertEqual(snap.hourly_usd, Decimal("2.49"))
        self.assertIsInstance(snap.hourly_usd, Decimal)

    def test_pricing_snapshot_raw_payload_is_jsonfield(self):
        snap = PricingSnapshotFactory(raw_payload={"price": "2.49", "raw_origin": "test"})
        snap.refresh_from_db()
        self.assertEqual(snap.raw_payload["price"], "2.49")

    def test_pricing_snapshot_scraped_at_required(self):
        """scraped_at is mandatory and timezone-aware."""
        snap = PricingSnapshotFactory(scraped_at=timezone.now())
        self.assertIsNotNone(snap.scraped_at.tzinfo)
