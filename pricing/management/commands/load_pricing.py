from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.core.management.base import BaseCommand, CommandError

from pricing.scrapers import SCRAPERS
from pricing.services.pricing_artifacts import read_artifact, scraped_prices_from_artifact
from pricing.services.scrape_runner import persist_prices

if TYPE_CHECKING:
    from argparse import ArgumentParser

logger = logging.getLogger("pricing.management.load_pricing")

_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "pricing"


class Command(BaseCommand):
    help = "Load JSON pricing artifacts from data/pricing/ into the database."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--provider",
            required=True,
            metavar="PROVIDER|all",
            help=f"Provider slug or 'all'. Known providers: {sorted(SCRAPERS)}",
        )
        parser.add_argument(
            "--from",
            dest="from_dir",
            default=None,
            metavar="DIR",
            help="Directory containing artifact JSON files (default: data/pricing/)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        provider_arg = str(options["provider"])
        data_dir = Path(options["from_dir"]) if options["from_dir"] else _DATA_DIR

        if provider_arg == "all":
            artifact_files = sorted(data_dir.glob("*.json"))
        elif provider_arg in SCRAPERS:
            artifact_files = [data_dir / f"{provider_arg}.json"]
        else:
            raise CommandError(f"Unknown provider: {provider_arg!r}. Choices: {sorted(SCRAPERS)} or 'all'")

        for artifact_path in artifact_files:
            slug = artifact_path.stem
            if not artifact_path.exists():
                self.stdout.write(self.style.WARNING(f"skipping {slug}: no artifact file at {artifact_path}"))
                continue
            if slug not in SCRAPERS:
                self.stdout.write(self.style.WARNING(f"skipping {slug}: no scraper entry in SCRAPERS"))
                continue

            try:
                artifact = read_artifact(artifact_path)
                prices = scraped_prices_from_artifact(artifact)
                entry = SCRAPERS[slug]
                n = persist_prices(
                    prices,
                    gpu_slug_resolver=entry.gpu_slug_resolver,
                    scraped_at=artifact.scraped_at,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"loaded {n} snapshots for {slug} (scraped_at={artifact.scraped_at.isoformat()})"
                    )
                )
            except Exception as exc:
                logger.exception("load_pricing failed for provider=%s", slug)
                raise CommandError(f"failed to load {slug}: {exc}") from exc
