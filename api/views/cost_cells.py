from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics

from api.pagination import CostCellCursorPagination
from api.serializers.cost_cells import CostCellSerializer
from pricing.services.cost import get_current_cost_cells_queryset

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from pricing.models import CurrentCostCell


class CostCellListView(generics.ListAPIView):  # type: ignore[misc]
    serializer_class = CostCellSerializer
    pagination_class = CostCellCursorPagination
    filter_backends = (DjangoFilterBackend,)
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
    # Ordering is fixed cheapest-first by CostCellCursorPagination.ordering.
    # OrderingFilter is intentionally omitted: cursor pagination's paginate_queryset
    # overwrites the queryset ordering with its own, so any ?ordering= param sent
    # by a client would be silently discarded, creating a false affordance.

    def get_queryset(self) -> QuerySet[CurrentCostCell]:
        qs = get_current_cost_cells_queryset()
        cap = self.request.query_params.get("max_usd_per_m_output")
        if cap:
            try:
                qs = qs.filter(usd_per_m_output__lte=Decimal(cap))
            except (InvalidOperation, TypeError):
                pass
        return qs
