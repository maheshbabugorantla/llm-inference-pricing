from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from django.core.management.base import BaseCommand, CommandError
from pydantic import ValidationError

from pricing.models import Provider
from pricing.services.seed import ProviderYAML


class Command(BaseCommand):
    help = "Idempotently seed Provider rows from seeds/providers.yaml."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--seeds-file", default=Path("seeds") / "providers.yaml", type=Path)

    def handle(self, *args: object, **options: object) -> None:
        seeds_file: Path = options["seeds_file"]  # type: ignore[assignment]
        if not seeds_file.exists():
            raise CommandError(f"seeds file not found: {seeds_file}")
        raw = yaml.safe_load(seeds_file.read_text())
        if not raw:
            self.stdout.write("seeds/providers.yaml is empty — nothing to do.")
            return

        created = updated = 0
        for entry in raw:
            try:
                provider = ProviderYAML.model_validate(entry)
            except ValidationError as e:
                raise CommandError(f"YAML schema validation failed: {e}") from e
            _, was_created = Provider.objects.update_or_create(
                slug=provider.slug,
                defaults={
                    "display_name": provider.display_name,
                    "provider_type": provider.provider_type,
                    "data_source_tier": provider.data_source_tier,
                    "pricing_url": provider.pricing_url,
                    "has_api": provider.has_api,
                    "api_endpoint": provider.api_endpoint,
                    "is_active": provider.is_active,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"seed_providers: {created} created, {updated} updated"))
