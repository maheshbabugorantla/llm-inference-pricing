from __future__ import annotations

from django.db import migrations

# Fixes three issues flagged in code review:
#
# 1. row_hash collision risk: bare concatenation (a||b||c) is ambiguous
#    ("ab"+"c" == "a"+"bc"). Add '|' delimiters so every component boundary
#    is unambiguous.
#
# 2. Float arithmetic in cost columns: multiplying by 1000000.0 / 3600.0
#    promotes the expression to double precision before the ::numeric cast,
#    violating the project invariant of keeping money math in numeric space.
#    Cast the float TPS columns to numeric first, then use integer literals.
#
# 3. decode_tps_aggregate / prefill_tps_aggregate are stored as float in
#    catalog_benchmarkpoint but modelled as DecimalField on CurrentCostCell.
#    Emit them as numeric(12,2) from the view so the type contract is exact.

DROP_INDEXES = """
DROP INDEX IF EXISTS pricing_cost_cells_uniq;
DROP INDEX IF EXISTS pricing_cost_cells_model;
DROP INDEX IF EXISTS pricing_cost_cells_gpu;
DROP INDEX IF EXISTS pricing_cost_cells_provider;
DROP INDEX IF EXISTS pricing_cost_cells_output_cost;
DROP INDEX IF EXISTS pricing_cost_cells_output_cost_hash;
DROP INDEX IF EXISTS pricing_cost_cells_provider_output;
DROP INDEX IF EXISTS pricing_cost_cells_provtype_output;
DROP INDEX IF EXISTS pricing_cost_cells_gpu_output;
DROP INDEX IF EXISTS pricing_cost_cells_model_output;
DROP MATERIALIZED VIEW IF EXISTS pricing_current_cost_cells;
"""

VIEW_SQL = """
CREATE MATERIALIZED VIEW pricing_current_cost_cells AS
WITH latest AS (
    SELECT DISTINCT ON (provider_id, gpu_id, tier, region)
        provider_id, gpu_id, tier, region, hourly_usd, scraped_at
    FROM pricing_pricingsnapshot
    WHERE available = TRUE
    ORDER BY provider_id, gpu_id, tier, region, scraped_at DESC
)
SELECT
    MD5(
        g.slug   || '|' || m.slug  || '|' || q.slug || '|' ||
        bp.tp_size::text || '|' || bp.batch_size::text || '|' ||
        bp.context_length::text || '|' || p.slug || '|' ||
        COALESCE(latest.tier, '') || '|' || COALESCE(latest.region, '')
    )                                                           AS row_hash,
    g.slug                                                      AS gpu_slug,
    g.display_name                                              AS gpu_display_name,
    m.slug                                                      AS model_slug,
    m.display_name                                              AS model_display_name,
    q.slug                                                      AS quantization_slug,
    bp.tp_size,
    bp.batch_size,
    bp.context_length,
    p.slug                                                      AS provider_slug,
    p.display_name                                              AS provider_display_name,
    p.provider_type,
    p.data_source_tier,
    latest.tier,
    latest.region,
    latest.hourly_usd,
    bp.decode_tps_aggregate::numeric(12, 2)                     AS decode_tps_aggregate,
    bp.prefill_tps_aggregate::numeric(12, 2)                    AS prefill_tps_aggregate,
    bp.ttft_ms,
    latest.scraped_at,
    (latest.hourly_usd * bp.tp_size::numeric * 1000000
        / (bp.prefill_tps_aggregate::numeric * 3600))::numeric(12, 4)   AS usd_per_m_input,
    (latest.hourly_usd * bp.tp_size::numeric * 1000000
        / (bp.decode_tps_aggregate::numeric  * 3600))::numeric(12, 4)   AS usd_per_m_output
FROM catalog_benchmarkpoint bp
JOIN catalog_gpu         g  ON g.id = bp.gpu_id
JOIN catalog_model       m  ON m.id = bp.model_id
JOIN catalog_quantization q ON q.id = bp.quantization_id
JOIN latest                 ON latest.gpu_id  = bp.gpu_id
JOIN pricing_provider    p  ON p.id = latest.provider_id
WHERE bp.prefill_tps_aggregate > 0
  AND bp.decode_tps_aggregate  > 0;
"""

CREATE_INDEXES = """
CREATE UNIQUE INDEX pricing_cost_cells_uniq
    ON pricing_current_cost_cells (row_hash);
CREATE INDEX pricing_cost_cells_model
    ON pricing_current_cost_cells (model_slug);
CREATE INDEX pricing_cost_cells_gpu
    ON pricing_current_cost_cells (gpu_slug);
CREATE INDEX pricing_cost_cells_provider
    ON pricing_current_cost_cells (provider_slug);
CREATE INDEX pricing_cost_cells_output_cost_hash
    ON pricing_current_cost_cells (usd_per_m_output, row_hash);
CREATE INDEX pricing_cost_cells_provider_output
    ON pricing_current_cost_cells (provider_slug, usd_per_m_output, row_hash);
CREATE INDEX pricing_cost_cells_provtype_output
    ON pricing_current_cost_cells (provider_type, usd_per_m_output, row_hash);
CREATE INDEX pricing_cost_cells_gpu_output
    ON pricing_current_cost_cells (gpu_slug, usd_per_m_output, row_hash);
CREATE INDEX pricing_cost_cells_model_output
    ON pricing_current_cost_cells (model_slug, usd_per_m_output, row_hash);
"""


class Migration(migrations.Migration):
    dependencies = [("pricing", "0018_cost_cells_composite_indexes")]

    operations = [
        migrations.RunSQL(
            sql=DROP_INDEXES + VIEW_SQL + CREATE_INDEXES,
            reverse_sql=DROP_INDEXES + VIEW_SQL + CREATE_INDEXES,
        ),
    ]
