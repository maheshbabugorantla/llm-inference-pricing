from __future__ import annotations

from django.db import migrations, models

# Final, correct view definition.
#
# Key invariants:
#   1. All money arithmetic uses numeric literals and ::numeric casts —
#      no float literals — so division stays in numeric space.
#   2. row_hash uses '|' delimiters between every variable-length field
#      to prevent ambiguous concatenation (e.g. "ab"+"c" vs "a"+"bc").
#   3. decode_tps_aggregate / prefill_tps_aggregate are cast to
#      numeric(12,2) so the unmanaged model's DecimalField matches exactly.
VIEW_SQL = """
CREATE MATERIALIZED VIEW pricing_current_cost_cells AS
WITH latest AS (
    SELECT DISTINCT ON (provider_id, gpu_id, tier)
        id, provider_id, gpu_id, tier, hourly_usd, scraped_at, region
    FROM   pricing_pricingsnapshot
    WHERE  available = TRUE
    ORDER  BY provider_id, gpu_id, tier, scraped_at DESC
)
SELECT
    MD5(
        g.slug  || '|' || m.slug  || '|' || q.slug       || '|' ||
        bp.tp_size::text          || '|' ||
        bp.batch_size::text       || '|' ||
        bp.context_length::text   || '|' ||
        p.slug  || '|' || latest.tier || '|' ||
        COALESCE(latest.region, '')
    )                                                       AS row_hash,
    g.slug                                                  AS gpu_slug,
    g.display_name                                          AS gpu_display_name,
    m.slug                                                  AS model_slug,
    m.display_name                                          AS model_display_name,
    q.slug                                                  AS quantization_slug,
    bp.tp_size                                              AS tp_size,
    bp.batch_size                                           AS batch_size,
    bp.context_length                                       AS context_length,
    p.slug                                                  AS provider_slug,
    p.display_name                                          AS provider_display_name,
    p.provider_type                                         AS provider_type,
    p.data_source_tier                                      AS data_source_tier,
    latest.tier                                             AS tier,
    latest.region                                           AS region,
    latest.hourly_usd                                       AS hourly_usd,
    bp.decode_tps_aggregate::numeric(12, 2)                 AS decode_tps_aggregate,
    bp.prefill_tps_aggregate::numeric(12, 2)                AS prefill_tps_aggregate,
    bp.ttft_ms                                              AS ttft_ms,
    (latest.hourly_usd * bp.tp_size::numeric * 1000000
        / (bp.prefill_tps_aggregate::numeric * 3600))::numeric(12, 4)   AS usd_per_m_input,
    (latest.hourly_usd * bp.tp_size::numeric * 1000000
        / (bp.decode_tps_aggregate::numeric  * 3600))::numeric(12, 4)   AS usd_per_m_output,
    latest.scraped_at                                       AS scraped_at
FROM       catalog_benchmarkpoint  bp
INNER JOIN catalog_gpu             g  ON g.id  = bp.gpu_id
INNER JOIN catalog_model           m  ON m.id  = bp.model_id
INNER JOIN catalog_quantization    q  ON q.id  = bp.quantization_id
INNER JOIN pricing_pricingsnapshot ps ON ps.gpu_id = bp.gpu_id
INNER JOIN latest                     ON latest.id = ps.id
INNER JOIN pricing_provider        p  ON p.id  = latest.provider_id
WHERE bp.prefill_tps_aggregate > 0
  AND bp.decode_tps_aggregate  > 0;
"""

DROP_VIEW_SQL = """
DROP MATERIALIZED VIEW IF EXISTS pricing_current_cost_cells;
"""

# Unique index on row_hash acts as the primary key for the unmanaged model.
UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX pricing_cost_cells_uniq
    ON pricing_current_cost_cells (row_hash);
"""

DROP_UNIQUE_INDEX_SQL = """
DROP INDEX IF EXISTS pricing_cost_cells_uniq;
"""

# Restores the M05 view definition so reversing this migration leaves the
# database in the state that existed after 0005_current_cost_cells ran.
RESTORE_M05_VIEW_SQL = """
CREATE MATERIALIZED VIEW pricing_current_cost_cells AS
WITH latest AS (
    SELECT DISTINCT ON (provider_id, gpu_id, tier, region)
        provider_id, gpu_id, tier, region, hourly_usd, scraped_at
    FROM   pricing_pricingsnapshot
    WHERE  available = TRUE
    ORDER  BY provider_id, gpu_id, tier, region, scraped_at DESC
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
JOIN latest            ON latest.gpu_id = bp.gpu_id
JOIN pricing_provider  p ON p.id        = latest.provider_id
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
        migrations.RunSQL(
            sql=DROP_VIEW_SQL + VIEW_SQL + UNIQUE_INDEX_SQL,
            reverse_sql=DROP_UNIQUE_INDEX_SQL + DROP_VIEW_SQL + RESTORE_M05_VIEW_SQL,
        ),
        # State-only: register CurrentCostCell as an unmanaged model so the
        # ORM can query the view. managed=False means Django never runs DDL
        # for this model; the view and indexes are managed via RunSQL above.
        migrations.CreateModel(
            name="CurrentCostCell",
            fields=[
                ("row_hash", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("gpu_slug", models.CharField(max_length=64)),
                ("gpu_display_name", models.CharField(max_length=128)),
                ("model_slug", models.CharField(max_length=128)),
                ("model_display_name", models.CharField(max_length=128)),
                ("quantization_slug", models.CharField(max_length=64)),
                ("tp_size", models.PositiveIntegerField()),
                ("batch_size", models.PositiveIntegerField()),
                ("context_length", models.PositiveIntegerField()),
                ("provider_slug", models.CharField(max_length=64)),
                ("provider_display_name", models.CharField(max_length=64)),
                ("provider_type", models.CharField(max_length=16)),
                ("data_source_tier", models.CharField(max_length=24)),
                ("tier", models.CharField(max_length=64)),
                ("region", models.CharField(max_length=64, null=True, blank=True)),
                ("hourly_usd", models.DecimalField(max_digits=12, decimal_places=4)),
                ("decode_tps_aggregate", models.DecimalField(max_digits=12, decimal_places=2)),
                ("prefill_tps_aggregate", models.DecimalField(max_digits=12, decimal_places=2)),
                ("ttft_ms", models.FloatField(null=True)),
                ("usd_per_m_input", models.DecimalField(max_digits=12, decimal_places=4)),
                ("usd_per_m_output", models.DecimalField(max_digits=12, decimal_places=4)),
                ("scraped_at", models.DateTimeField()),
            ],
            options={
                "managed": False,
                "db_table": "pricing_current_cost_cells",
            },
        ),
    ]
