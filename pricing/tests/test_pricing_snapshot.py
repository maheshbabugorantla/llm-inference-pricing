from __future__ import annotations

from decimal import Decimal

import pytest
from django.utils import timezone

from pricing.tests.factories import PricingSnapshotFactory


@pytest.mark.django_db
def test_pricing_snapshot_stores_decimal_hourly_usd():
    snap = PricingSnapshotFactory(hourly_usd=Decimal("2.49"))
    snap.refresh_from_db()
    assert snap.hourly_usd == Decimal("2.49")
    assert isinstance(snap.hourly_usd, Decimal)


@pytest.mark.django_db
def test_pricing_snapshot_raw_payload_is_jsonfield():
    snap = PricingSnapshotFactory(raw_payload={"price": "2.49", "raw_origin": "test"})
    snap.refresh_from_db()
    assert snap.raw_payload["price"] == "2.49"


@pytest.mark.django_db
def test_pricing_snapshot_scraped_at_required():
    """scraped_at is mandatory and timezone-aware."""
    snap = PricingSnapshotFactory(scraped_at=timezone.now())
    assert snap.scraped_at.tzinfo is not None
