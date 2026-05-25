from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    State-only migration: correct max_length for provider_slug and
    provider_display_name on CurrentCostCell.

    Migration 0020 set both fields to 128, but the source columns are
    pricing.Provider.slug (max_length=64) and Provider.display_name (max_length=64).
    This corrects the recorded state to match the actual source model.

    CurrentCostCell is unmanaged (managed=False), so no SQL is emitted.
    """

    dependencies = [("pricing", "0020_fix_currentcostcell_field_lengths")]

    operations = [
        migrations.AlterField(
            model_name="currentcostcell",
            name="provider_slug",
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name="currentcostcell",
            name="provider_display_name",
            field=models.CharField(max_length=64),
        ),
    ]
