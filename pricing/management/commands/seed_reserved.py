from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db.models.signals import post_save

from catalog.models import GPU
from pricing.generators.reserved_cloud import regenerate_reserved_cloud_snapshots
from pricing.models import (
    Provider,
    ReservedCapacityProduct,
    ReservedCloudDeployment,
    _regenerate_reserved_on_save,
)
from pricing.services.seed import ReservedCapacityProductYAML, ReservedCloudDeploymentYAML


class Command(BaseCommand):
    help = "Idempotently seed ReservedCapacityProduct and ReservedCloudDeployment rows."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--products-dir", default=Path("seeds") / "reserved" / "products", type=Path)
        parser.add_argument(
            "--deployments-dir", default=Path("seeds") / "reserved" / "deployments", type=Path
        )

    def handle(self, *args: object, **options: object) -> None:
        products_dir: Path = options["products_dir"]  # type: ignore[assignment]
        deployments_dir: Path = options["deployments_dir"]  # type: ignore[assignment]

        prod_created = prod_updated = 0
        for yaml_file in sorted(products_dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_file.read_text())
            try:
                data = ReservedCapacityProductYAML.model_validate(raw)
            except Exception as exc:
                raise CommandError(f"{yaml_file.name}: {exc}") from exc

            try:
                gpu = GPU.objects.get(slug=data.gpu_slug)
            except GPU.DoesNotExist as exc:
                raise CommandError(f"{yaml_file.name}: GPU slug '{data.gpu_slug}' not found") from exc

            try:
                provider = Provider.objects.get(slug=data.cloud_provider_slug)
            except Provider.DoesNotExist as exc:
                raise CommandError(
                    f"{yaml_file.name}: Provider slug '{data.cloud_provider_slug}' not found"
                ) from exc

            _, was_created = ReservedCapacityProduct.objects.update_or_create(
                slug=data.slug,
                defaults={
                    "display_name": data.display_name,
                    "cloud_provider": provider,
                    "gpu": gpu,
                    "gpus_per_node": data.gpus_per_node,
                    "payment_cadence": data.payment_cadence,
                    "term_months": data.term_months,
                    "upfront_usd": data.upfront_usd,
                    "monthly_recurring_usd": data.monthly_recurring_usd,
                    "per_active_hour_usd": data.per_active_hour_usd,
                    "capacity_block_total_usd": data.capacity_block_total_usd,
                    "minimum_utilization_floor_pct": data.minimum_utilization_floor_pct,
                    "on_demand_reference_usd_per_hour": data.on_demand_reference_usd_per_hour,
                    "listing_observed_at": data.listing_observed_at,
                    "notes": data.notes,
                    "is_active": data.is_active,
                },
            )
            if was_created:
                prod_created += 1
            else:
                prod_updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"ReservedCapacityProduct: {prod_created} created, {prod_updated} updated")
        )

        dep_created = dep_updated = 0
        post_save.disconnect(_regenerate_reserved_on_save, sender=ReservedCloudDeployment)
        try:
            for yaml_file in sorted(deployments_dir.glob("*.yaml")):
                raw = yaml.safe_load(yaml_file.read_text())
                try:
                    data_dep = ReservedCloudDeploymentYAML.model_validate(raw)
                except Exception as exc:
                    raise CommandError(f"{yaml_file.name}: {exc}") from exc

                try:
                    product = ReservedCapacityProduct.objects.get(slug=data_dep.product_slug)
                except ReservedCapacityProduct.DoesNotExist as exc:
                    raise CommandError(
                        f"{yaml_file.name}: ReservedCapacityProduct slug '{data_dep.product_slug}' not found"
                    ) from exc

                try:
                    provider = Provider.objects.get(slug=data_dep.cloud_provider_slug)
                except Provider.DoesNotExist as exc:
                    raise CommandError(
                        f"{yaml_file.name}: Provider slug '{data_dep.cloud_provider_slug}' not found"
                    ) from exc

                _, was_created = ReservedCloudDeployment.objects.update_or_create(
                    slug=data_dep.slug,
                    defaults={
                        "display_name": data_dep.display_name,
                        "product": product,
                        "cloud_provider": provider,
                        "region": data_dep.region,
                        "num_nodes": data_dep.num_nodes,
                        "expected_utilization_pct": data_dep.expected_utilization_pct,
                        "upfront_override_usd": data_dep.upfront_override_usd,
                        "monthly_recurring_override_usd": data_dep.monthly_recurring_override_usd,
                        "per_hour_override_usd": data_dep.per_hour_override_usd,
                        "is_active": data_dep.is_active,
                        "notes": data_dep.notes,
                    },
                )
                if was_created:
                    dep_created += 1
                else:
                    dep_updated += 1
        finally:
            post_save.connect(_regenerate_reserved_on_save, sender=ReservedCloudDeployment)

        self.stdout.write(
            self.style.SUCCESS(f"ReservedCloudDeployment: {dep_created} created, {dep_updated} updated")
        )
        count = regenerate_reserved_cloud_snapshots()
        self.stdout.write(self.style.SUCCESS(f"Regenerated {count} reserved-cloud snapshots"))
