"""
TransactionTestCase tests for ops hardening (M07.T01 and M07.T02).

These tests query TimescaleDB metadata views which require real transaction
boundaries, so they live in a dedicated TransactionTestCase file to avoid
interfering with TestCase-based tests.
"""

from __future__ import annotations

from django.db import connection
from django.test import TransactionTestCase

# ---------------------------------------------------------------------------
# T01 -- Continuous aggregate
# ---------------------------------------------------------------------------


class ContinuousAggregateTest(TransactionTestCase):
    def test_daily_median_view_exists_in_timescaledb(self):
        with connection.cursor() as c:
            c.execute(
                "SELECT view_name FROM timescaledb_information.continuous_aggregates "
                "WHERE view_name = 'pricing_daily_median'"
            )
            self.assertIsNotNone(c.fetchone(), "pricing_daily_median continuous aggregate missing")


# ---------------------------------------------------------------------------
# T02 -- Retention policy
# ---------------------------------------------------------------------------


class RetentionPolicyTest(TransactionTestCase):
    def test_retention_policy_registered_for_pricing_snapshot(self):
        with connection.cursor() as c:
            c.execute(
                "SELECT config FROM timescaledb_information.jobs "
                "WHERE proc_name = 'policy_retention' "
                "AND hypertable_name = 'pricing_pricingsnapshot'"
            )
            row = c.fetchone()
            self.assertIsNotNone(row, "retention policy missing on pricing_pricingsnapshot")
            self.assertIn("90 days", str(row[0]))
