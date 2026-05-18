from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

import yaml
from django.core.management.base import BaseCommand, CommandError
from pydantic import ValidationError

from catalog.services.seed import GPUYAML, ModelYAML, QuantizationYAML


class Command(BaseCommand):
    help = "Schema-validate all catalog YAML files without touching the DB."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--seeds-dir", default="seeds", type=Path)

    def handle(self, *args: object, **options: object) -> None:
        seeds_dir: Path = options["seeds_dir"]  # type: ignore[assignment]
        errors: list[str] = []

        for path, schema in [
            (seeds_dir / "quantizations.yaml", QuantizationYAML),
            (seeds_dir / "gpus.yaml", GPUYAML),
        ]:
            if not path.exists():
                continue
            for i, raw in enumerate(yaml.safe_load(path.read_text()) or []):
                try:
                    schema(**raw)
                except ValidationError as e:
                    errors.append(f"{path}[{i}]: {e}")

        models_dir = seeds_dir / "models"
        if models_dir.is_dir():
            for yaml_file in sorted(models_dir.glob("*.yaml")):
                for i, raw in enumerate(yaml.safe_load(yaml_file.read_text()) or []):
                    try:
                        ModelYAML(**raw)
                    except ValidationError as e:
                        errors.append(f"{yaml_file}[{i}]: {e}")

        if errors:
            raise CommandError("Validation failed:\n" + "\n".join(errors))
        self.stdout.write(self.style.SUCCESS("All catalog YAML files valid."))
