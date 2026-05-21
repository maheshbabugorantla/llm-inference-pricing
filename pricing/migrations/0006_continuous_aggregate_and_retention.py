from __future__ import annotations

from django.db import migrations

# TimescaleDB 2.14+ supports ordered-set aggregates (percentile_cont) in
# continuous aggregates. We use `latest-pg16` (≥ 2.17 as of 2026-05) so this
# runs natively. If you downgrade to an older Timescale version, replace the
# continuous aggregate with a regular materialized view refreshed by Celery.
CAGG_SQL = """
CREATE MATERIALIZED VIEW pricing_daily_median
WITH (timescaledb.continuous) AS
SELECT
    provider_id,
    gpu_id,
    tier,
    region,
    time_bucket(INTERVAL '1 day', scraped_at) AS day,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY hourly_usd) AS median_hourly_usd,
    COUNT(*) AS snapshot_count
FROM pricing_pricingsnapshot
WHERE available = TRUE
GROUP BY provider_id, gpu_id, tier, region, day
WITH NO DATA;

SELECT add_continuous_aggregate_policy('pricing_daily_median',
    start_offset => INTERVAL '7 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
"""

DROP_CAGG_SQL = "DROP MATERIALIZED VIEW IF EXISTS pricing_daily_median CASCADE;"

RETENTION_SQL = "SELECT add_retention_policy('pricing_pricingsnapshot', INTERVAL '90 days');"
DROP_RETENTION_SQL = "SELECT remove_retention_policy('pricing_pricingsnapshot', if_exists => TRUE);"


class Migration(migrations.Migration):
    dependencies = [("pricing", "0005_current_cost_cells")]

    operations = [
        migrations.RunSQL(sql=CAGG_SQL, reverse_sql=DROP_CAGG_SQL),
        migrations.RunSQL(sql=RETENTION_SQL, reverse_sql=DROP_RETENTION_SQL),
    ]
