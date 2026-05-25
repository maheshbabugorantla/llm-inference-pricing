from __future__ import annotations

import django_filters

from pricing.models import CurrentCostCell


class CostCellFilter(django_filters.FilterSet):  # type: ignore[misc]
    max_usd_per_m_output = django_filters.NumberFilter(
        field_name="usd_per_m_output",
        lookup_expr="lte",
    )

    class Meta:
        model = CurrentCostCell
        fields = (
            "gpu_slug",
            "model_slug",
            "quantization_slug",
            "provider_slug",
            "provider_type",
            "data_source_tier",
            "tier",
            "batch_size",
            "context_length",
        )
