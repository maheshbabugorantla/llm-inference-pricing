from __future__ import annotations

import pytest
from django.db import connection


@pytest.mark.django_db
def test_timescaledb_extension_is_installed():
    with connection.cursor() as c:
        c.execute("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")
        row = c.fetchone()
        assert row is not None, "timescaledb extension not installed"


@pytest.mark.django_db
def test_pricing_snapshot_is_a_hypertable():
    with connection.cursor() as c:
        c.execute(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'pricing_pricingsnapshot'"
        )
        assert c.fetchone() is not None, "pricing_pricingsnapshot is not a hypertable"
