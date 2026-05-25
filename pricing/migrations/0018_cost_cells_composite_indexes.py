from __future__ import annotations

from django.db import migrations

# Replace single-column usd_per_m_output index with a composite (usd_per_m_output, row_hash)
# index. Cursor pagination orders by (usd_per_m_output, row_hash) — the composite index
# eliminates the Incremental Sort that PostgreSQL emits when only the first column is indexed.
#
# Add composite filter+sort indexes for the most-queried filter fields so that
# DjangoFilterBackend WHERE clauses land on index scans instead of SeqScans.

DROP_SINGLE_COL_IDX = """
DROP INDEX IF EXISTS pricing_cost_cells_output_cost;
"""

CREATE_COMPOSITE_INDEXES = """
-- cursor pagination: covers ORDER BY usd_per_m_output, row_hash in one index scan
CREATE INDEX pricing_cost_cells_output_cost_hash
    ON pricing_current_cost_cells (usd_per_m_output, row_hash);

-- filter + sort for the common filter fields
CREATE INDEX pricing_cost_cells_provider_output
    ON pricing_current_cost_cells (provider_slug, usd_per_m_output, row_hash);

CREATE INDEX pricing_cost_cells_provtype_output
    ON pricing_current_cost_cells (provider_type, usd_per_m_output, row_hash);

CREATE INDEX pricing_cost_cells_gpu_output
    ON pricing_current_cost_cells (gpu_slug, usd_per_m_output, row_hash);

CREATE INDEX pricing_cost_cells_model_output
    ON pricing_current_cost_cells (model_slug, usd_per_m_output, row_hash);
"""

REVERSE_SQL = """
DROP INDEX IF EXISTS pricing_cost_cells_output_cost_hash;
DROP INDEX IF EXISTS pricing_cost_cells_provider_output;
DROP INDEX IF EXISTS pricing_cost_cells_provtype_output;
DROP INDEX IF EXISTS pricing_cost_cells_gpu_output;
DROP INDEX IF EXISTS pricing_cost_cells_model_output;

CREATE INDEX pricing_cost_cells_output_cost
    ON pricing_current_cost_cells (usd_per_m_output);
"""


class Migration(migrations.Migration):
    # NOTE: For a large production dataset, run these index builds manually with
    # CREATE INDEX CONCURRENTLY outside a transaction before applying this
    # migration, then fake-apply (migrate --fake) to avoid the exclusive lock.
    # Plain CREATE INDEX is used here so the migration works in test environments,
    # where CONCURRENTLY is prohibited inside Django's transaction-wrapped test DB
    # setup.

    dependencies = [("pricing", "0017_currentcostcell")]

    operations = [
        migrations.RunSQL(
            sql=DROP_SINGLE_COL_IDX + CREATE_COMPOSITE_INDEXES,
            reverse_sql=REVERSE_SQL,
        ),
    ]
