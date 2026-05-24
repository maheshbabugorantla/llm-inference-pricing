from __future__ import annotations

from rest_framework import serializers

from pricing.models import CurrentCostCell


class CostCellSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    class Meta:
        model = CurrentCostCell
        fields = (
            "row_hash",
            "gpu_slug",
            "gpu_display_name",
            "model_slug",
            "model_display_name",
            "quantization_slug",
            "tp_size",
            "batch_size",
            "context_length",
            "provider_slug",
            "provider_display_name",
            "provider_type",
            "data_source_tier",
            "tier",
            "region",
            "hourly_usd",
            "decode_tps_aggregate",
            "prefill_tps_aggregate",
            "ttft_ms",
            "usd_per_m_input",
            "usd_per_m_output",
            "scraped_at",
        )
