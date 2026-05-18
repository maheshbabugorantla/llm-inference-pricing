from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.management.base import BaseCommand, CommandError

from pricing.scrapers import SCRAPERS
from pricing.services.scrape_runner import persist_prices

if TYPE_CHECKING:
    from argparse import ArgumentParser


class Command(BaseCommand):
    help = "Run a provider scraper and persist snapshots."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--provider", required=True, choices=sorted(SCRAPERS))

    def handle(self, *args: Any, **options: Any) -> None:
        provider = str(options["provider"])
        if provider not in SCRAPERS:
            raise CommandError(f"Unknown provider: {provider!r}")
        entry = SCRAPERS[provider]
        prices = entry.scrape_fn()
        n = persist_prices(prices, gpu_slug_resolver=entry.gpu_slug_resolver)
        self.stdout.write(self.style.SUCCESS(f"persisted {n} snapshots"))
