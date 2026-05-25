from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics
from rest_framework.filters import OrderingFilter

from api.pagination import CostCellCursorPagination
from api.serializers.cost_cells import CostCellSerializer
from pricing.services.cost import get_current_cost_cells_queryset

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from pricing.models import CurrentCostCell


class CostCellListView(generics.ListAPIView):  # type: ignore[misc]
    serializer_class = CostCellSerializer
    pagination_class = CostCellCursorPagination
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = (
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
    ordering_fields = (
        "usd_per_m_output",
        "usd_per_m_input",
        "hourly_usd",
        "decode_tps_aggregate",
    )
    ordering = ("usd_per_m_output", "row_hash")

    def get_queryset(self) -> QuerySet[CurrentCostCell]:
        qs = get_current_cost_cells_queryset()
        cap = self.request.query_params.get("max_usd_per_m_output")
        if cap:
            try:
                qs = qs.filter(usd_per_m_output__lte=Decimal(cap))
            except (InvalidOperation, TypeError):
                pass
        return qs
