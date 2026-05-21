from __future__ import annotations

from django.db import migrations

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

DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS pricing_current_cost_cells;"


class Migration(migrations.Migration):
    dependencies = [("pricing", "0004_check_hourly_usd_nonneg")]

    operations = [
        migrations.RunSQL(sql=VIEW_SQL, reverse_sql=DROP_SQL),
    ]
