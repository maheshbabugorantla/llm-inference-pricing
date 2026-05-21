from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from django.core.management.base import BaseCommand, CommandError

from catalog.models import GPU
from pricing.models import HardwareSKU, OnPremDeployment
from pricing.services.seed import HardwareSKUYAML, OnPremDeploymentYAML


class Command(BaseCommand):
    help = (
        "Idempotently seed HardwareSKU and OnPremDeployment rows from seeds/hardware/ and seeds/deployments/."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--hardware-dir", default=Path("seeds") / "hardware", type=Path)
        parser.add_argument("--deployments-dir", default=Path("seeds") / "deployments", type=Path)

    def handle(self, *args: object, **options: object) -> None:
        hardware_dir: Path = options["hardware_dir"]  # type: ignore[assignment]
        deployments_dir: Path = options["deployments_dir"]  # type: ignore[assignment]

        sku_created = sku_updated = 0
        for yaml_file in sorted(hardware_dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_file.read_text())
            sku_data = HardwareSKUYAML.model_validate(raw)
            try:
                gpu = GPU.objects.get(slug=sku_data.gpu_slug)
            except GPU.DoesNotExist as exc:
                raise CommandError(f"{yaml_file.name}: GPU slug '{sku_data.gpu_slug}' not found") from exc

            _, was_created = HardwareSKU.objects.update_or_create(
                slug=sku_data.slug,
                defaults={
                    "display_name": sku_data.display_name,
                    "vendor": sku_data.vendor,
                    "num_gpus": sku_data.num_gpus,
                    "gpu": gpu,
                    "cpu_model": sku_data.cpu_model,
                    "cpu_sockets": sku_data.cpu_sockets,
                    "ram_gb": sku_data.ram_gb,
                    "nvme_tb": sku_data.nvme_tb,
                    "network_gbps": sku_data.network_gbps,
                    "host_tdp_watts": sku_data.host_tdp_watts,
                    "reference_msrp_usd": sku_data.reference_msrp_usd,
                    "notes": sku_data.notes,
                },
            )
            if was_created:
                sku_created += 1
            else:
                sku_updated += 1

        self.stdout.write(self.style.SUCCESS(f"HardwareSKU: {sku_created} created, {sku_updated} updated"))

        dep_created = dep_updated = 0
        for yaml_file in sorted(deployments_dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_file.read_text())
            dep_data = OnPremDeploymentYAML.model_validate(raw)
            try:
                sku = HardwareSKU.objects.get(slug=dep_data.hardware_sku_slug)
            except HardwareSKU.DoesNotExist as exc:
                raise CommandError(
                    f"{yaml_file.name}: HardwareSKU slug '{dep_data.hardware_sku_slug}' not found"
                ) from exc

            _, was_created = OnPremDeployment.objects.update_or_create(
                slug=dep_data.slug,
                defaults={
                    "display_name": dep_data.display_name,
                    "hardware_sku": sku,
                    "num_nodes": dep_data.num_nodes,
                    "capex_per_node_usd": dep_data.capex_per_node_usd,
                    "salvage_pct": dep_data.salvage_pct,
                    "depreciation_years": dep_data.depreciation_years,
                    "expected_utilization_pct": dep_data.expected_utilization_pct,
                    "power_usd_per_kwh": dep_data.power_usd_per_kwh,
                    "pue": dep_data.pue,
                    "monthly_colo_usd": dep_data.monthly_colo_usd,
                    "monthly_bandwidth_usd": dep_data.monthly_bandwidth_usd,
                    "sysadmin_annual_burdened_usd": dep_data.sysadmin_annual_burdened_usd,
                    "gpu_count_per_admin": dep_data.gpu_count_per_admin,
                    "is_active": dep_data.is_active,
                    "notes": dep_data.notes,
                },
            )
            if was_created:
                dep_created += 1
            else:
                dep_updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"OnPremDeployment: {dep_created} created, {dep_updated} updated")
        )
