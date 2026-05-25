from __future__ import annotations

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run inside a transaction block.
    atomic = False

    dependencies = [("pricing", "0016_expand_current_cost_cells")]

    operations = [
        # Simple lookup indexes
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(fields=["model_slug"], name="pricing_cost_cells_model"),
        ),
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(fields=["gpu_slug"], name="pricing_cost_cells_gpu"),
        ),
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(fields=["provider_slug"], name="pricing_cost_cells_provider"),
        ),
        # Composite covering indexes for cost-ordered queries.
        # Names > 30 chars use the pcc_ prefix (pricing_current_cost_cells abbreviated)
        # to satisfy Django's Index.max_name_length = 30 constraint.
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(
                fields=["usd_per_m_output", "row_hash"],
                name="pcc_output_cost_hash",
            ),
        ),
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(
                fields=["provider_slug", "usd_per_m_output", "row_hash"],
                name="pcc_provider_output",
            ),
        ),
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(
                fields=["provider_type", "usd_per_m_output", "row_hash"],
                name="pcc_provtype_output",
            ),
        ),
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(
                fields=["gpu_slug", "usd_per_m_output", "row_hash"],
                name="pricing_cost_cells_gpu_output",
            ),
        ),
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(
                fields=["model_slug", "usd_per_m_output", "row_hash"],
                name="pcc_model_output",
            ),
        ),
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(
                fields=["tier", "usd_per_m_output", "row_hash"],
                name="pricing_cost_cells_tier_output",
            ),
        ),
        AddIndexConcurrently(
            model_name="currentcostcell",
            index=models.Index(
                fields=["data_source_tier", "usd_per_m_output", "row_hash"],
                name="pcc_datasrc_output",
            ),
        ),
    ]
