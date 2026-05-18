from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from pydantic import ValidationError

from catalog.models import GPU, Model, Quantization
from catalog.services.seed import GPUYAML, ModelYAML, QuantizationYAML


class Command(BaseCommand):
    help = "Upsert catalog reference data from YAML seeds. Idempotent."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--seeds-dir", default="seeds", type=Path)

    def handle(self, *args: object, **options: object) -> None:
        seeds_dir: Path = options["seeds_dir"]  # type: ignore[assignment]
        if not seeds_dir.is_dir():
            raise CommandError(f"seeds dir not found: {seeds_dir}")

        try:
            with transaction.atomic():
                self._load_quantizations(seeds_dir / "quantizations.yaml")
                self._load_gpus(seeds_dir / "gpus.yaml")
                self._load_models(seeds_dir / "models")
        except ValidationError as e:
            raise CommandError(f"YAML schema validation failed: {e}") from e

    def _load_quantizations(self, path: Path) -> None:
        if not path.exists():
            return
        for raw in yaml.safe_load(path.read_text()) or []:
            payload = QuantizationYAML(**raw)
            Quantization.objects.update_or_create(
                slug=payload.slug,
                defaults=payload.model_dump(exclude={"slug"}),
            )

    def _load_gpus(self, path: Path) -> None:
        if not path.exists():
            return
        for raw in yaml.safe_load(path.read_text()) or []:
            payload = GPUYAML(**raw)
            GPU.objects.update_or_create(
                slug=payload.slug,
                defaults=payload.model_dump(exclude={"slug"}),
            )

    def _load_models(self, dir_path: Path) -> None:
        if not dir_path.is_dir():
            return
        for yaml_file in sorted(dir_path.glob("*.yaml")):
            for raw in yaml.safe_load(yaml_file.read_text()) or []:
                payload = ModelYAML(**raw)
                quant = Quantization.objects.get(slug=payload.recommended_quant)
                defaults = payload.model_dump(exclude={"slug", "recommended_quant"})
                defaults["recommended_quant"] = quant
                Model.objects.update_or_create(
                    slug=payload.slug,
                    defaults=defaults,
                )
