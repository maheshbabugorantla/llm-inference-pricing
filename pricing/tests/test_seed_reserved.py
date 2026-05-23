"""Tests for seed_reserved management command (M09.T05)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError

from pricing.models import ReservedCapacityProduct, ReservedCloudDeployment


@pytest.fixture()
def cloud_provider():
    from pricing.tests.factories import ProviderFactory

    return ProviderFactory(slug="lambda-seed-test", provider_type="cloud")


@pytest.fixture()
def seed_gpu():
    from catalog.tests.factories import GPUFactory

    return GPUFactory(slug="nvidia-h100-sxm-80-seed", vram_gb=80, tdp_watts=700)


def _product_yaml(gpu_slug: str, provider_slug: str, **overrides) -> dict:
    base = {
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


def _deployment_yaml(product_slug: str, provider_slug: str, **overrides) -> dict:
    base = {
        "slug": "lambda-prod-h100-1yr-seed",
        "display_name": "Lambda Production H100 1-yr",
        "product_slug": product_slug,
        "cloud_provider_slug": provider_slug,
        "expected_utilization_pct": "0.700",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path: products and deployments seeded
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_reserved_loads_products(tmp_path, seed_gpu, cloud_provider):
    """Products YAML is loaded and a ReservedCapacityProduct row is created."""
    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    (products_dir / "lambda-h100-1yr.yaml").write_text(
        yaml.dump(_product_yaml(seed_gpu.slug, cloud_provider.slug))
    )
    call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    assert ReservedCapacityProduct.objects.filter(slug="lambda-h100-1yr-seed").exists()


@pytest.mark.django_db
def test_seed_reserved_loads_deployments(tmp_path, seed_gpu, cloud_provider):
    """Deployments YAML is loaded and a ReservedCloudDeployment row is created."""
    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    (products_dir / "p.yaml").write_text(yaml.dump(_product_yaml(seed_gpu.slug, cloud_provider.slug)))
    (deployments_dir / "d.yaml").write_text(
        yaml.dump(_deployment_yaml("lambda-h100-1yr-seed", cloud_provider.slug))
    )
    call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    assert ReservedCloudDeployment.objects.filter(slug="lambda-prod-h100-1yr-seed").exists()


@pytest.mark.django_db
def test_seed_reserved_is_idempotent(tmp_path, seed_gpu, cloud_provider):
    """Running seed_reserved twice must not create duplicate rows."""
    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    (products_dir / "p.yaml").write_text(yaml.dump(_product_yaml(seed_gpu.slug, cloud_provider.slug)))
    call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    assert ReservedCapacityProduct.objects.filter(slug="lambda-h100-1yr-seed").count() == 1


@pytest.mark.django_db
def test_seed_reserved_update_changes_display_name(tmp_path, seed_gpu, cloud_provider):
    """Re-seeding with a changed display_name must update the existing row (true upsert)."""
    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    (products_dir / "p.yaml").write_text(
        yaml.dump(_product_yaml(seed_gpu.slug, cloud_provider.slug, display_name="Original Name"))
    )
    call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    (products_dir / "p.yaml").write_text(
        yaml.dump(_product_yaml(seed_gpu.slug, cloud_provider.slug, display_name="Updated Name"))
    )
    call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    product = ReservedCapacityProduct.objects.get(slug="lambda-h100-1yr-seed")
    assert product.display_name == "Updated Name"


@pytest.mark.django_db
def test_seed_reserved_regenerates_snapshots_once(tmp_path, seed_gpu, cloud_provider):
    """seed_reserved must call regenerate_reserved_cloud_snapshots exactly once at the end."""
    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    (products_dir / "p.yaml").write_text(yaml.dump(_product_yaml(seed_gpu.slug, cloud_provider.slug)))
    (deployments_dir / "d.yaml").write_text(
        yaml.dump(_deployment_yaml("lambda-h100-1yr-seed", cloud_provider.slug))
    )
    target = "pricing.management.commands.seed_reserved.regenerate_reserved_cloud_snapshots"
    with patch(target) as mock_regen:
        mock_regen.return_value = 2
        call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    assert mock_regen.call_count == 1


# ---------------------------------------------------------------------------
# Failure modes: invalid YAML must be rejected loudly
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_reserved_rejects_product_with_invalid_payment_cadence(tmp_path, seed_gpu, cloud_provider):
    """A product YAML with payment_cadence='invalid' must fail with CommandError, no partial write."""
    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    bad = _product_yaml(seed_gpu.slug, cloud_provider.slug, payment_cadence="invalid")
    (products_dir / "bad.yaml").write_text(yaml.dump(bad))
    with pytest.raises(CommandError):
        call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    assert ReservedCapacityProduct.objects.count() == 0


@pytest.mark.django_db
def test_seed_reserved_rejects_product_with_unknown_gpu_slug(tmp_path, cloud_provider):
    """A product YAML referencing a GPU slug that doesn't exist must fail with CommandError."""
    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    bad = _product_yaml("nonexistent-gpu-slug", cloud_provider.slug)
    (products_dir / "bad.yaml").write_text(yaml.dump(bad))
    with pytest.raises(CommandError, match="nonexistent-gpu-slug"):
        call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)
    assert ReservedCapacityProduct.objects.count() == 0


@pytest.mark.django_db
def test_seed_reserved_rejects_deployment_with_unknown_product_slug(tmp_path, seed_gpu, cloud_provider):
    """A deployment YAML referencing an unknown product slug must fail with CommandError."""
    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()
    (products_dir / "p.yaml").write_text(yaml.dump(_product_yaml(seed_gpu.slug, cloud_provider.slug)))
    bad_dep = _deployment_yaml("nonexistent-product-slug", cloud_provider.slug)
    (deployments_dir / "d.yaml").write_text(yaml.dump(bad_dep))
    with pytest.raises(CommandError, match="nonexistent-product-slug"):
        call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)


@pytest.mark.django_db
def test_seed_reserved_rejects_deployment_with_mismatched_cloud_provider(tmp_path, seed_gpu, cloud_provider):
    """A deployment whose cloud_provider differs from its product's cloud_provider must fail with
    CommandError and roll back the whole transaction — the product written earlier must not persist.

    This exercises the full_clean() guard in ReservedCloudDeployment.clean() via the seed command's
    _save_with_validation() helper, and confirms the @transaction.atomic rollback on failure.
    """
    from pricing.tests.factories import ProviderFactory

    other_provider = ProviderFactory(slug="other-cloud-seed", provider_type="cloud")

    products_dir = tmp_path / "products"
    products_dir.mkdir()
    deployments_dir = tmp_path / "deployments"
    deployments_dir.mkdir()

    (products_dir / "p.yaml").write_text(yaml.dump(_product_yaml(seed_gpu.slug, cloud_provider.slug)))
    # Deployment deliberately uses other_provider, which differs from the product's cloud_provider
    bad_dep = _deployment_yaml("lambda-h100-1yr-seed", other_provider.slug)
    (deployments_dir / "d.yaml").write_text(yaml.dump(bad_dep))

    with pytest.raises(CommandError, match="cloud_provider must match"):
        call_command("seed_reserved", products_dir=products_dir, deployments_dir=deployments_dir)

    # Transaction must have rolled back: the product seeded before the bad deployment must be gone
    assert ReservedCapacityProduct.objects.filter(slug="lambda-h100-1yr-seed").count() == 0
    assert ReservedCloudDeployment.objects.count() == 0
