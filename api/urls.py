from __future__ import annotations

from django.urls import path

from api.views.catalog import GPUListView, ModelListView, QuantizationListView
from api.views.health import health

urlpatterns = [
    path("v1/health/", health, name="health"),
    path("v1/gpus/", GPUListView.as_view(), name="gpu-list"),
    path("v1/models/", ModelListView.as_view(), name="model-list"),
    path("v1/quantizations/", QuantizationListView.as_view(), name="quantization-list"),
]
