from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.management.base import BaseCommand, CommandError

if TYPE_CHECKING:
    from argparse import ArgumentParser

from pricing.scrapers import lambda_labs, nebius, runpod, vast
from pricing.services.scrape_runner import persist_prices

_SCRAPERS = {
    "runpod": (runpod.scrape, runpod.map_runpod_gpu),
    "lambda": (lambda_labs.scrape, lambda_labs.map_lambda_gpu),
    "vast": (vast.scrape, vast.map_vast_gpu),
    "nebius": (nebius.scrape, nebius.map_nebius_gpu),
}


class Command(BaseCommand):
    help = "Run a provider scraper and persist snapshots."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--provider", required=True, choices=sorted(_SCRAPERS))

    def handle(self, *args: Any, **options: Any) -> None:
        provider = str(options["provider"])
        if provider not in _SCRAPERS:
            raise CommandError(f"Unknown provider: {provider!r}")
        scrape_fn, resolver = _SCRAPERS[provider]
        prices = scrape_fn()
        n = persist_prices(prices, gpu_slug_resolver=resolver)
        self.stdout.write(self.style.SUCCESS(f"persisted {n} snapshots"))
