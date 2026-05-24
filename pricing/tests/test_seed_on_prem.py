"""Tests for seed_on_prem management command (M08.T05)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml
from django.core.management import call_command
from django.test import TestCase

from catalog.tests.factories import GPUFactory
from pricing.models import HardwareSKU, OnPremDeployment


class SeedOnPremTest(TestCase):
    def setUp(self) -> None:
        self.gpu = GPUFactory()
        self._tmp = tempfile.mkdtemp()
        self.tmp = Path(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _hw_dir(self, sku_data: dict) -> Path:
        hw = self.tmp / "hardware"
        hw.mkdir(exist_ok=True)
        (hw / "sku.yaml").write_text(yaml.dump(sku_data))
        return hw

    def _dep_dir(self, dep_data: dict | None = None) -> Path:
        dep = self.tmp / "deployments"
        dep.mkdir(exist_ok=True)
        if dep_data:
            (dep / "dep.yaml").write_text(yaml.dump(dep_data))
        return dep

    def _default_sku(self, slug: str = "test-sku") -> dict:
        return {
            "slug": slug,
            "display_name": "Test SKU",
            "vendor": "supermicro",
            "num_gpus": 4,
            "gpu_slug": self.gpu.slug,
            "cpu_model": "Intel Xeon",
            "cpu_sockets": 2,
            "ram_gb": 512,
            "nvme_tb": 10,
            "network_gbps": 200,
            "host_tdp_watts": 800,
        }

    def _default_dep(self, sku_slug: str, dep_slug: str = "test-dep") -> dict:
        return {
            "slug": dep_slug,
            "display_name": "Test Dep",
            "hardware_sku_slug": sku_slug,
            "capex_per_node_usd": "100000.00",
            "power_usd_per_kwh": "0.1000",
            "sysadmin_annual_burdened_usd": "150000.00",
        }

    def test_seed_on_prem_loads_hardware_skus(self) -> None:
        hw = self._hw_dir(self._default_sku())
        dep = self._dep_dir()
        call_command("seed_on_prem", hardware_dir=hw, deployments_dir=dep)
        self.assertTrue(HardwareSKU.objects.filter(slug="test-sku").exists())

    def test_seed_on_prem_idempotent(self) -> None:
        sku_slug = "test-sku-idem"
        hw = self._hw_dir(self._default_sku(sku_slug))
        dep = self._dep_dir()
        call_command("seed_on_prem", hardware_dir=hw, deployments_dir=dep)
        call_command("seed_on_prem", hardware_dir=hw, deployments_dir=dep)
        self.assertEqual(HardwareSKU.objects.filter(slug=sku_slug).count(), 1)

    def test_seed_on_prem_loads_deployments(self) -> None:
        hw = self._hw_dir(self._default_sku("deploy-sku"))
        dep = self._dep_dir(self._default_dep("deploy-sku", "test-dep"))
        call_command("seed_on_prem", hardware_dir=hw, deployments_dir=dep)
        self.assertTrue(OnPremDeployment.objects.filter(slug="test-dep").exists())

    def test_seed_on_prem_regenerates_once_not_per_deployment(self) -> None:
        """Seeding N deployments must call regenerate_on_prem_snapshots exactly once."""
        hw = self.tmp / "hardware"
        hw.mkdir(exist_ok=True)
        dep = self.tmp / "deployments"
        dep.mkdir(exist_ok=True)

        for i in range(2):
            slug = f"batch-sku-{i}"
            (hw / f"sku-{i}.yaml").write_text(
                yaml.dump(
                    {
                        "slug": slug,
                        "display_name": f"Batch SKU {i}",
                        "vendor": "dell",
                        "num_gpus": 4,
                        "gpu_slug": self.gpu.slug,
                        "cpu_model": "Intel Xeon",
                        "cpu_sockets": 2,
                        "ram_gb": 256,
                        "nvme_tb": 5,
                        "network_gbps": 100,
                        "host_tdp_watts": 600,
                    }
                )
            )
            (dep / f"dep-{i}.yaml").write_text(
                yaml.dump(
                    {
                        "slug": f"batch-dep-{i}",
                        "display_name": f"Batch Dep {i}",
                        "hardware_sku_slug": slug,
                        "capex_per_node_usd": "100000.00",
                        "power_usd_per_kwh": "0.1000",
                        "sysadmin_annual_burdened_usd": "150000.00",
                    }
                )
            )

        with patch("pricing.management.commands.seed_on_prem.regenerate_on_prem_snapshots") as mock_regen:
            mock_regen.return_value = 4
            call_command("seed_on_prem", hardware_dir=hw, deployments_dir=dep)

        self.assertEqual(mock_regen.call_count, 1, f"Expected 1 call, got {mock_regen.call_count}")
