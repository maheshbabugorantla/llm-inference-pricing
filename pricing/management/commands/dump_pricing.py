from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.core.management.base import BaseCommand, CommandError

from pricing.scrapers import SCRAPERS
from pricing.services.pricing_artifacts import artifact_from_scraped_prices, write_artifact

if TYPE_CHECKING:
    from argparse import ArgumentParser

logger = logging.getLogger("pricing.management.dump_pricing")

_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "pricing"


class Command(BaseCommand):
    help = "Scrape provider pricing and write JSON artifacts to data/pricing/. Does not touch the database."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--provider",
            required=True,
            metavar="PROVIDER|all",
            help=f"Provider slug or 'all'. Known providers: {sorted(SCRAPERS)}",
        )
        parser.add_argument(
            "--out",
            default=None,
            metavar="DIR",
            help="Output directory (default: data/pricing/)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        provider_arg = str(options["provider"])
        out_dir = Path(options["out"]) if options["out"] else _DATA_DIR

        if provider_arg == "all":
            slugs = sorted(SCRAPERS)
        elif provider_arg in SCRAPERS:
            slugs = [provider_arg]
        else:
            raise CommandError(f"Unknown provider: {provider_arg!r}. Choices: {sorted(SCRAPERS)} or 'all'")

        out_dir.mkdir(parents=True, exist_ok=True)

        failed: list[str] = []
        for slug in slugs:
            entry = SCRAPERS[slug]
            out_path = out_dir / f"{slug}.json"
            try:
                prices = entry.scrape_fn()
                artifact = artifact_from_scraped_prices(slug, entry.source_url, prices)
                write_artifact(out_path, artifact)
                self.stdout.write(self.style.SUCCESS(f"wrote {out_path} ({len(artifact.prices)} prices)"))
            except Exception as exc:
                logger.exception("dump_pricing failed for provider=%s", slug)
                self.stderr.write(f"ERROR [{slug}]: {exc}\n")
                failed.append(slug)

        if failed:
            self.stderr.write(f"failed providers: {failed}\n")
            sys.exit(1)
