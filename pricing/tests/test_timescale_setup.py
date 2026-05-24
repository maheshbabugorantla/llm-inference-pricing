from __future__ import annotations

from django.db import connection
from django.test import TestCase


class TimescaleSetupTest(TestCase):
    def test_timescaledb_extension_is_installed(self) -> None:
        with connection.cursor() as c:
            c.execute("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")
            row = c.fetchone()
            self.assertIsNotNone(row, "timescaledb extension not installed")

    def test_pricing_snapshot_is_a_hypertable(self) -> None:
        with connection.cursor() as c:
            c.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'pricing_pricingsnapshot'"
            )
            self.assertIsNotNone(c.fetchone(), "pricing_pricingsnapshot is not a hypertable")
