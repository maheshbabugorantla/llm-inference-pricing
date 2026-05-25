from __future__ import annotations

from django.db import migrations

CREATE_INDEXES = """
CREATE INDEX pricing_cost_cells_tier_output
    ON pricing_current_cost_cells (tier, usd_per_m_output, row_hash);

CREATE INDEX pricing_cost_cells_datasource_output
    ON pricing_current_cost_cells (data_source_tier, usd_per_m_output, row_hash);
"""

DROP_INDEXES = """
DROP INDEX IF EXISTS pricing_cost_cells_tier_output;
DROP INDEX IF EXISTS pricing_cost_cells_datasource_output;
"""


class Migration(migrations.Migration):
    """
    Add composite indexes for tier and data_source_tier filter fields.

    CostCellListView exposes both as filterset_fields but the materialized
    view had no indexes starting with these columns. Without them, filtered
    queries fall back to a sequential scan or must traverse the
    (usd_per_m_output, row_hash) ordering index from the front, which
    is expensive as the view grows.
    """

    dependencies = [("pricing", "0020_fix_currentcostcell_field_lengths")]

    operations = [
        migrations.RunSQL(sql=CREATE_INDEXES, reverse_sql=DROP_INDEXES),
    ]
