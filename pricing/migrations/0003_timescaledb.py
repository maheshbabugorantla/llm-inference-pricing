from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0002_pricing_snapshot"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS timescaledb;",
            reverse_sql="-- extension not dropped on reverse",
        ),
        # TimescaleDB requires the partition column (scraped_at) in every unique index.
        # Drop the default Django id PK so create_hypertable can proceed.
        # The autoincrement sequence is preserved; id remains effectively unique.
        migrations.RunSQL(
            sql="ALTER TABLE pricing_pricingsnapshot DROP CONSTRAINT IF EXISTS pricing_pricingsnapshot_pkey;",
            reverse_sql="ALTER TABLE pricing_pricingsnapshot ADD PRIMARY KEY (id);",
        ),
        migrations.RunSQL(
            sql=(
                "SELECT create_hypertable("
                "  'pricing_pricingsnapshot', 'scraped_at',"
                "  chunk_time_interval => INTERVAL '7 days',"
                "  if_not_exists => TRUE,"
                "  migrate_data => TRUE"
                ");"
            ),
            reverse_sql="-- irreversible",
        ),
    ]
