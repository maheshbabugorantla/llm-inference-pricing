from __future__ import annotations

from rest_framework import serializers

from pricing.models import Provider


class ProviderSerializer(serializers.ModelSerializer):  # type: ignore[misc]
    last_scraped_at = serializers.SerializerMethodField()

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

    def get_last_scraped_at(self, obj: Provider) -> str | None:
        latest: object = getattr(obj, "latest_scrape", None)
        if latest is None:
            return None
        return str(latest)
