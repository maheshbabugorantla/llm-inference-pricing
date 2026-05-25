from __future__ import annotations

from rest_framework import serializers

from pricing.models import Provider


class ProviderSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    last_scraped_at = serializers.DateTimeField(read_only=True, source="latest_scrape", allow_null=True)

    class Meta:
        model = Provider
        fields = (
            "slug",
            "display_name",
            "provider_type",
            "data_source_tier",
            "has_api",
            "is_active",
            "last_scraped_at",
        )
