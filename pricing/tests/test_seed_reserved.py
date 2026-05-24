"""Tests for seed_reserved management command (M09.T05)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from catalog.tests.factories import GPUFactory
from pricing.models import ReservedCapacityProduct, ReservedCloudDeployment
from pricing.tests.factories import ProviderFactory


def _product_yaml(gpu_slug: str, provider_slug: str, **overrides: object) -> dict:
    base: dict = {
        "slug": "lambda-h100-1yr-seed",
        "display_name": "Lambda Reserved H100 1-yr",
        "cloud_provider_slug": provider_slug,
        "gpu_slug": gpu_slug,
        "gpus_per_node": 8,
        "payment_cadence": "all_upfront",
        "term_months": 12,
        "upfront_usd": "500000.00",
        "listing_observed_at": "2025-01-15",
    }
    base.update(overrides)
    return base


def _deployment_yaml(product_slug: str, provider_slug: str, **overrides: object) -> dict:
    base: dict = {
        "slug": "lambda-prod-h100-1yr-seed",
        "display_name": "Lambda Production H100 1-yr",
        "product_slug": product_slug,
        "cloud_provider_slug": provider_slug,
        "expected_utilization_pct": "0.700",
    }
    base.update(overrides)
    return base


class SeedReservedTest(TestCase):
    def setUp(self) -> None:
        self.gpu = GPUFactory(slug="nvidia-h100-sxm-80-seed", vram_gb=80, tdp_watts=700)
        self.provider = ProviderFactory(slug="lambda-seed-test", provider_type="cloud")
        self._tmp = tempfile.mkdtemp()
        self.tmp = Path(self._tmp)
        self.products_dir = self.tmp / "products"
        self.products_dir.mkdir()
        self.deployments_dir = self.tmp / "deployments"
        self.deployments_dir.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_product(self, **overrides: object) -> None:
        (self.products_dir / "p.yaml").write_text(
            yaml.dump(_product_yaml(self.gpu.slug, self.provider.slug, **overrides))
        )

    def _write_deployment(self, product_slug: str = "lambda-h100-1yr-seed", **overrides: object) -> None:
        (self.deployments_dir / "d.yaml").write_text(
            yaml.dump(_deployment_yaml(product_slug, self.provider.slug, **overrides))
        )

    def test_seed_reserved_loads_products(self) -> None:
        """Products YAML is loaded and a ReservedCapacityProduct row is created."""
        self._write_product()
        call_command("seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir)
        self.assertTrue(ReservedCapacityProduct.objects.filter(slug="lambda-h100-1yr-seed").exists())

    def test_seed_reserved_loads_deployments(self) -> None:
        """Deployments YAML is loaded and a ReservedCloudDeployment row is created."""
        self._write_product()
        self._write_deployment()
        call_command("seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir)
        self.assertTrue(ReservedCloudDeployment.objects.filter(slug="lambda-prod-h100-1yr-seed").exists())

    def test_seed_reserved_is_idempotent(self) -> None:
        """Running seed_reserved twice must not create duplicate rows."""
        self._write_product()
        call_command("seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir)
        call_command("seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir)
        self.assertEqual(ReservedCapacityProduct.objects.filter(slug="lambda-h100-1yr-seed").count(), 1)

    def test_seed_reserved_update_changes_display_name(self) -> None:
        """Re-seeding with changed display_name updates the DB row (true upsert)."""
        self._write_product(display_name="Original Name")
        call_command("seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir)
        self._write_product(display_name="Updated Name")
        call_command("seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir)
        product = ReservedCapacityProduct.objects.get(slug="lambda-h100-1yr-seed")
        self.assertEqual(product.display_name, "Updated Name")

    def test_seed_reserved_regenerates_snapshots_once(self) -> None:
        """seed_reserved must call regenerate_reserved_cloud_snapshots exactly once."""
        self._write_product()
        self._write_deployment()
        target = "pricing.management.commands.seed_reserved.regenerate_reserved_cloud_snapshots"
        with patch(target) as mock_regen:
            mock_regen.return_value = 2
            call_command(
                "seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir
            )
        self.assertEqual(mock_regen.call_count, 1)

    def test_seed_reserved_rejects_product_with_invalid_payment_cadence(self) -> None:
        """A product YAML with payment_cadence='invalid' must fail with CommandError."""
        (self.products_dir / "bad.yaml").write_text(
            yaml.dump(_product_yaml(self.gpu.slug, self.provider.slug, payment_cadence="invalid"))
        )
        with self.assertRaises(CommandError):
            call_command(
                "seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir
            )
        self.assertEqual(ReservedCapacityProduct.objects.count(), 0)

    def test_seed_reserved_rejects_product_with_unknown_gpu_slug(self) -> None:
        """A product YAML referencing a non-existent GPU slug must fail with CommandError."""
        (self.products_dir / "bad.yaml").write_text(
            yaml.dump(_product_yaml("nonexistent-gpu-slug", self.provider.slug))
        )
        with self.assertRaisesRegex(CommandError, "nonexistent-gpu-slug"):
            call_command(
                "seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir
            )
        self.assertEqual(ReservedCapacityProduct.objects.count(), 0)

    def test_seed_reserved_rejects_deployment_with_unknown_product_slug(self) -> None:
        """A deployment YAML referencing an unknown product slug must fail with CommandError."""
        self._write_product()
        (self.deployments_dir / "d.yaml").write_text(
            yaml.dump(_deployment_yaml("nonexistent-product-slug", self.provider.slug))
        )
        with self.assertRaisesRegex(CommandError, "nonexistent-product-slug"):
            call_command(
                "seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir
            )

    def test_seed_reserved_rejects_deployment_with_mismatched_cloud_provider(self) -> None:
        """A deployment whose cloud_provider differs from its product's must fail and roll back."""
        other_provider = ProviderFactory(slug="other-cloud-seed", provider_type="cloud")
        self._write_product()
        (self.deployments_dir / "d.yaml").write_text(
            yaml.dump(_deployment_yaml("lambda-h100-1yr-seed", other_provider.slug))
        )
        with self.assertRaisesRegex(CommandError, "cloud_provider must match"):
            call_command(
                "seed_reserved", products_dir=self.products_dir, deployments_dir=self.deployments_dir
            )
        self.assertEqual(ReservedCapacityProduct.objects.filter(slug="lambda-h100-1yr-seed").count(), 0)
        self.assertEqual(ReservedCloudDeployment.objects.count(), 0)
