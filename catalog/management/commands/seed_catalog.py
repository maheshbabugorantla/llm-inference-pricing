from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from pydantic import ValidationError

from catalog.models import GPU, BenchmarkPoint, BenchmarkSource, Model, Quantization
from catalog.services.fit import compute_fit
from catalog.services.seed import GPUYAML, BenchmarkFileYAML, BenchmarkPointYAML, ModelYAML, QuantizationYAML

logger = logging.getLogger("catalog.seed")


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
                self._load_benchmarks(seeds_dir / "benchmarks")
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

    def _load_benchmarks(self, dir_path: Path) -> None:
        if not dir_path.is_dir():
            return
        for yaml_file in sorted(dir_path.glob("*.yaml")):
            payload = BenchmarkFileYAML(**yaml.safe_load(yaml_file.read_text()))
            source, _ = BenchmarkSource.objects.update_or_create(
                slug=payload.source.slug,
                defaults=payload.source.model_dump(exclude={"slug"}),
            )
            for point in payload.points:
                self._persist_benchmark_point(point, source)

    def _persist_benchmark_point(self, point: BenchmarkPointYAML, source: BenchmarkSource) -> None:
        model = Model.objects.get(slug=point.model)
        gpu = GPU.objects.get(slug=point.gpu)
        quant = Quantization.objects.get(slug=point.quantization)

        fits, _, _ = compute_fit(
            total_params_b=model.total_params_b,
            num_layers=model.num_layers,
            num_kv_heads=model.num_kv_heads,
            head_dim=model.head_dim,
            architecture=model.architecture,
            weight_bits=quant.weight_bits,
            kv_cache_bits=quant.kv_cache_bits,
            tp_size=point.tp_size,
            batch_size=point.batch_size,
            context_length=point.context_length,
            gpu_vram_gb=gpu.vram_gb,
        )
        if not fits:
            logger.warning(
                "fit check failed; skipping benchmark point "
                "model=%s gpu=%s quant=%s tp_size=%d batch_size=%d context_length=%d",
                model.slug,
                gpu.slug,
                quant.slug,
                point.tp_size,
                point.batch_size,
                point.context_length,
            )
            return

        BenchmarkPoint.objects.update_or_create(
            model=model,
            gpu=gpu,
            quantization=quant,
            tp_size=point.tp_size,
            batch_size=point.batch_size,
            context_length=point.context_length,
            defaults={
                "prefill_tps_aggregate": point.prefill_tps_aggregate,
                "decode_tps_aggregate": point.decode_tps_aggregate,
                "ttft_ms": point.ttft_ms,
                "source": source,
            },
        )
