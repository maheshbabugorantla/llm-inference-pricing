from __future__ import annotations

from rest_framework import generics

from api.pagination import SlugCursorPagination
from api.serializers.pricing import ProviderSerializer
from pricing.models import Provider


class ProviderListView(generics.ListAPIView):  # type: ignore[misc]
    serializer_class = ProviderSerializer
    pagination_class = SlugCursorPagination

    def get_queryset(self) -> object:
        from django.db.models import Max

        return Provider.objects.annotate(latest_scrape=Max("pricingsnapshot__scraped_at")).order_by("slug")
