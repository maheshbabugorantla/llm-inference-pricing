"""Tests for seed_on_prem management command (M08.T05)."""

from __future__ import annotations

import pytest
import yaml
from django.core.management import call_command

from pricing.models import HardwareSKU, OnPremDeployment


@pytest.mark.django_db
def test_seed_on_prem_loads_hardware_skus(tmp_path, catalog_gpu):
    hw_dir = tmp_path / "hardware"
    hw_dir.mkdir()
    (hw_dir / "test-sku.yaml").write_text(
        yaml.dump(
            {
                "slug": "test-sku",
                "display_name": "Test SKU",
                "vendor": "supermicro",
                "num_gpus": 4,
                "gpu_slug": catalog_gpu.slug,
                "cpu_model": "Intel Xeon",
                "cpu_sockets": 2,
                "ram_gb": 512,
                "nvme_tb": 10,
                "network_gbps": 200,
                "host_tdp_watts": 800,
            }
        )
    )
    dep_dir = tmp_path / "deployments"
    dep_dir.mkdir()
    call_command("seed_on_prem", hardware_dir=hw_dir, deployments_dir=dep_dir)
    assert HardwareSKU.objects.filter(slug="test-sku").exists()


@pytest.mark.django_db
def test_seed_on_prem_idempotent(tmp_path, catalog_gpu):
    hw_dir = tmp_path / "hardware"
    hw_dir.mkdir()
    (hw_dir / "test-sku.yaml").write_text(
        yaml.dump(
            {
                "slug": "test-sku-idem",
                "display_name": "Test SKU",
                "vendor": "dell",
                "num_gpus": 8,
                "gpu_slug": catalog_gpu.slug,
                "cpu_model": "AMD EPYC",
                "cpu_sockets": 2,
                "ram_gb": 1024,
                "nvme_tb": 20,
                "network_gbps": 400,
                "host_tdp_watts": 1200,
            }
        )
    )
    dep_dir = tmp_path / "deployments"
    dep_dir.mkdir()
    call_command("seed_on_prem", hardware_dir=hw_dir, deployments_dir=dep_dir)
    call_command("seed_on_prem", hardware_dir=hw_dir, deployments_dir=dep_dir)
    assert HardwareSKU.objects.filter(slug="test-sku-idem").count() == 1


@pytest.mark.django_db
def test_seed_on_prem_loads_deployments(tmp_path, catalog_gpu):
    hw_dir = tmp_path / "hardware"
    hw_dir.mkdir()
    (hw_dir / "sku.yaml").write_text(
        yaml.dump(
            {
                "slug": "deploy-sku",
                "display_name": "Deploy SKU",
                "vendor": "dell",
                "num_gpus": 4,
                "gpu_slug": catalog_gpu.slug,
                "cpu_model": "Intel",
                "cpu_sockets": 1,
                "ram_gb": 256,
                "nvme_tb": 5,
                "network_gbps": 100,
                "host_tdp_watts": 600,
            }
        )
    )
    dep_dir = tmp_path / "deployments"
    dep_dir.mkdir()
    (dep_dir / "dep.yaml").write_text(
        yaml.dump(
            {
                "slug": "test-dep",
                "display_name": "Test Dep",
                "hardware_sku_slug": "deploy-sku",
                "capex_per_node_usd": "100000.00",
                "power_usd_per_kwh": "0.1000",
                "sysadmin_annual_burdened_usd": "150000.00",
            }
        )
    )
    call_command("seed_on_prem", hardware_dir=hw_dir, deployments_dir=dep_dir)
    assert OnPremDeployment.objects.filter(slug="test-dep").exists()


@pytest.fixture()
def catalog_gpu():
    from catalog.tests.factories import GPUFactory

    return GPUFactory()
