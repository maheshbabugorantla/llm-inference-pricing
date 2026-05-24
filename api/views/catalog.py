from __future__ import annotations

from rest_framework import generics

from api.pagination import SlugCursorPagination
from api.serializers.catalog import GPUSerializer, ModelSerializer, QuantizationSerializer
from catalog.models import GPU, Model, Quantization


class GPUListView(generics.ListAPIView):  # type: ignore[misc]
    serializer_class = GPUSerializer
    queryset = GPU.objects.order_by("slug")
    pagination_class = SlugCursorPagination


class ModelListView(generics.ListAPIView):  # type: ignore[misc]
    serializer_class = ModelSerializer
    queryset = Model.objects.order_by("slug")
    pagination_class = SlugCursorPagination


class QuantizationListView(generics.ListAPIView):  # type: ignore[misc]
    serializer_class = QuantizationSerializer
    queryset = Quantization.objects.order_by("slug")
    pagination_class = SlugCursorPagination
