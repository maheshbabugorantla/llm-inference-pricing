from __future__ import annotations

from typing import TYPE_CHECKING

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics

from api.filters import CostCellFilter
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
    filterset_class = CostCellFilter
    # Ordering is fixed cheapest-first by CostCellCursorPagination.ordering.
    # OrderingFilter is intentionally omitted: cursor pagination's paginate_queryset
    # overwrites the queryset ordering with its own, so any ?ordering= param sent
    # by a client would be silently discarded, creating a false affordance.

    def get_queryset(self) -> QuerySet[CurrentCostCell]:
        return get_current_cost_cells_queryset()
