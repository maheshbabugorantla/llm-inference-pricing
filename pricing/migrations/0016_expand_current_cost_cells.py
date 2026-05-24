from __future__ import annotations

from django.db import migrations

DROP_SQL = """
DROP INDEX IF EXISTS pricing_cost_cells_uniq;
DROP INDEX IF EXISTS pricing_cost_cells_model;
DROP INDEX IF EXISTS pricing_cost_cells_gpu;
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
        g.slug || m.slug || q.slug || bp.tp_size::text ||
        bp.batch_size::text || bp.context_length::text ||
        p.slug || COALESCE(latest.tier, '') ||
        COALESCE(latest.region, '')
    )                                                   AS row_hash,
    g.slug                                              AS gpu_slug,
    g.display_name                                      AS gpu_display_name,
    m.slug                                              AS model_slug,
    m.display_name                                      AS model_display_name,
    q.slug                                              AS quantization_slug,
    bp.tp_size,
    bp.batch_size,
    bp.context_length,
    p.slug                                              AS provider_slug,
    p.display_name                                      AS provider_display_name,
    p.provider_type,
    p.data_source_tier,
    latest.tier,
    latest.region,
    latest.hourly_usd,
    bp.decode_tps_aggregate,
    bp.prefill_tps_aggregate,
    bp.ttft_ms,
    latest.scraped_at,
    (latest.hourly_usd * bp.tp_size * 1000000.0
        / (bp.prefill_tps_aggregate * 3600.0))::numeric(12, 4)  AS usd_per_m_input,
    (latest.hourly_usd * bp.tp_size * 1000000.0
        / (bp.decode_tps_aggregate  * 3600.0))::numeric(12, 4)  AS usd_per_m_output
FROM catalog_benchmarkpoint bp
JOIN catalog_gpu         g  ON g.id  = bp.gpu_id
JOIN catalog_model       m  ON m.id  = bp.model_id
JOIN catalog_quantization q ON q.id  = bp.quantization_id
JOIN latest              ON latest.gpu_id    = bp.gpu_id
JOIN pricing_provider    p  ON p.id  = latest.provider_id
WHERE bp.prefill_tps_aggregate > 0
  AND bp.decode_tps_aggregate  > 0;

CREATE UNIQUE INDEX pricing_cost_cells_uniq
    ON pricing_current_cost_cells (row_hash);
CREATE INDEX pricing_cost_cells_model
    ON pricing_current_cost_cells (model_slug);
CREATE INDEX pricing_cost_cells_gpu
    ON pricing_current_cost_cells (gpu_slug);
CREATE INDEX pricing_cost_cells_provider
    ON pricing_current_cost_cells (provider_slug);
CREATE INDEX pricing_cost_cells_output_cost
    ON pricing_current_cost_cells (usd_per_m_output);
"""

REVERSE_SQL = DROP_SQL + """
CREATE MATERIALIZED VIEW pricing_current_cost_cells AS
WITH latest AS (
    SELECT DISTINCT ON (provider_id, gpu_id, tier, region)
        provider_id, gpu_id, tier, region, hourly_usd, scraped_at
    FROM pricing_pricingsnapshot
    WHERE available = TRUE
    ORDER BY provider_id, gpu_id, tier, region, scraped_at DESC
)
SELECT
    bp.id                                           AS benchmark_point_id,
    bp.model_id,
    bp.gpu_id,
    bp.quantization_id,
    bp.tp_size,
    bp.batch_size,
    bp.context_length,
    bp.prefill_tps_aggregate,
    bp.decode_tps_aggregate,
    p.id                                            AS provider_id,
    latest.tier                                     AS pricing_tier,
    latest.region,
    latest.hourly_usd,
    latest.scraped_at                               AS pricing_scraped_at,
    (latest.hourly_usd * bp.tp_size * 1000000.0
        / (bp.prefill_tps_aggregate * 3600.0))::numeric(10, 4)  AS usd_per_m_input,
    (latest.hourly_usd * bp.tp_size * 1000000.0
        / (bp.decode_tps_aggregate  * 3600.0))::numeric(10, 4)  AS usd_per_m_output
FROM catalog_benchmarkpoint bp
JOIN latest   ON latest.gpu_id    = bp.gpu_id
JOIN pricing_provider p ON p.id   = latest.provider_id
WHERE bp.prefill_tps_aggregate > 0
  AND bp.decode_tps_aggregate  > 0;

CREATE UNIQUE INDEX pricing_cost_cells_uniq
    ON pricing_current_cost_cells (benchmark_point_id, provider_id, pricing_tier, region);
CREATE INDEX pricing_cost_cells_model ON pricing_current_cost_cells (model_id);
CREATE INDEX pricing_cost_cells_gpu   ON pricing_current_cost_cells (gpu_id);
"""


class Migration(migrations.Migration):
    dependencies = [("pricing", "0015_pricing_drift_alert")]

    operations = [
        migrations.RunSQL(sql=DROP_SQL + VIEW_SQL, reverse_sql=REVERSE_SQL),
    ]
