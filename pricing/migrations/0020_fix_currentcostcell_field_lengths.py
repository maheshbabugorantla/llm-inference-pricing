from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    State-only migration: correct max_length for CurrentCostCell.model_slug.

    Migration 0017 recorded model_slug as max_length=64, but the source column
    catalog.Model.slug is max_length=128. provider_slug and provider_display_name
    were correctly recorded at 64 in 0017 (matching pricing.Provider.slug = 64
    and Provider.display_name = 64) and are not touched here.

    CurrentCostCell is unmanaged (managed=False), so no SQL is emitted.
    makemigrations does not auto-detect max_length changes on unmanaged models,
    so this migration is written by hand to keep the recorded state consistent
    with the live model definition.
    """

    dependencies = [("pricing", "0019_fix_cost_cells_view_precision_and_hash")]

    operations = [
        migrations.AlterField(
            model_name="currentcostcell",
            name="model_slug",
            field=models.CharField(max_length=128),
        ),
    ]
