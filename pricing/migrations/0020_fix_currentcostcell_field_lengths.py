from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    State-only migration: correct max_length in the migration state for three
    CurrentCostCell fields that were recorded as 64 in 0017 but should match
    their catalog source columns (catalog.Model.slug = 128,
    catalog.Provider.slug = 128, catalog.Provider.display_name = 128).

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
        migrations.AlterField(
            model_name="currentcostcell",
            name="provider_slug",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="currentcostcell",
            name="provider_display_name",
            field=models.CharField(max_length=128),
        ),
    ]
