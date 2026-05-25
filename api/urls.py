from __future__ import annotations

from django.urls import path

from api.views.catalog import GPUListView, ModelListView, QuantizationListView
from api.views.cost_cells import CostCellListView
from api.views.health import health
from api.views.pricing import ProviderListView

urlpatterns = [
    path("v1/health/", health, name="health"),
    path("v1/gpus/", GPUListView.as_view(), name="gpu-list"),
    path("v1/models/", ModelListView.as_view(), name="model-list"),
    path("v1/quantizations/", QuantizationListView.as_view(), name="quantization-list"),
    path("v1/providers/", ProviderListView.as_view(), name="provider-list"),
    path("v1/cost-cells/", CostCellListView.as_view(), name="cost-cell-list"),
]
