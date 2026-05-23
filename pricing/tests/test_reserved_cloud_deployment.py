"""Tests for ReservedCloudDeployment model (M09.T02)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from catalog.tests.factories import GPUFactory
from pricing.models import ReservedCapacityProduct, ReservedCloudDeployment
from pricing.tests.factories import ProviderFactory


def _make_product(slug_suffix: str = "") -> ReservedCapacityProduct:
    gpu = GPUFactory(slug=f"nvidia-h100-sxm-80-rcd{slug_suffix}", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(slug=f"lambda-rcd{slug_suffix}", provider_type="cloud")
    return ReservedCapacityProduct.objects.create(
        slug=f"lambda-h100-1yr-rcd{slug_suffix}",
        display_name="Lambda Reserved H100 1-yr",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="all_upfront",
        term_months=12,
        upfront_usd=Decimal("500000.00"),
        listing_observed_at="2025-01-15",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reserved_cloud_deployment_saves_with_required_fields():
    """A deployment linked to a ReservedCapacityProduct saves cleanly with defaults."""
    product = _make_product("a")
    dep = ReservedCloudDeployment.objects.create(
        slug="lambda-prod-h100-1yr-a",
        display_name="Lambda Production H100 1-yr",
        product=product,
        cloud_provider=product.cloud_provider,
        num_nodes=2,
        expected_utilization_pct=Decimal("0.700"),
    )
    assert dep.pk is not None
    assert dep.expected_utilization_pct == Decimal("0.700")


@pytest.mark.django_db
def test_reserved_cloud_deployment_overrides_are_nullable():
    """Override fields (upfront, monthly, per_hour) are optional — None by default."""
    product = _make_product("b")
    dep = ReservedCloudDeployment.objects.create(
        slug="lambda-prod-h100-1yr-b",
        display_name="Lambda Production H100 1-yr",
        product=product,
        cloud_provider=product.cloud_provider,
    )
    assert dep.upfront_override_usd is None
    assert dep.monthly_recurring_override_usd is None
    assert dep.per_hour_override_usd is None


@pytest.mark.django_db
def test_reserved_cloud_deployment_with_negotiated_override_saves_cleanly():
    """A deployment with all three override fields set is valid (negotiated deal scenario)."""
    product = _make_product("c")
    dep = ReservedCloudDeployment.objects.create(
        slug="lambda-prod-h100-1yr-override-c",
        display_name="Lambda Production H100 1-yr (negotiated)",
        product=product,
        cloud_provider=product.cloud_provider,
        expected_utilization_pct=Decimal("0.800"),
        upfront_override_usd=Decimal("450000.00"),
        monthly_recurring_override_usd=Decimal("0"),
        per_hour_override_usd=Decimal("0"),
    )
    assert dep.upfront_override_usd == Decimal("450000.00")


@pytest.mark.django_db
def test_reserved_cloud_deployment_fk_to_product_is_protected():
    """Deleting a ReservedCapacityProduct that has deployments is blocked (PROTECT)."""
    from django.db import transaction as db_tx

    product = _make_product("d")
    ReservedCloudDeployment.objects.create(
        slug="lambda-prod-h100-1yr-d",
        display_name="Lambda Production H100 1-yr",
        product=product,
        cloud_provider=product.cloud_provider,
    )
    from django.db.models import ProtectedError

    with pytest.raises(ProtectedError):
        with db_tx.atomic():
            product.delete()


@pytest.mark.django_db
def test_reserved_cloud_deployment_str_returns_display_name():
    """__str__ must return display_name per SHARED.md convention."""
    product = _make_product("e")
    dep = ReservedCloudDeployment.objects.create(
        slug="lambda-prod-h100-1yr-e",
        display_name="Lambda Production H100 1-yr",
        product=product,
        cloud_provider=product.cloud_provider,
    )
    assert str(dep) == "Lambda Production H100 1-yr"


@pytest.mark.django_db
def test_duplicate_deployment_slug_raises_integrity_error():
    """Two ReservedCloudDeployment rows with the same slug must be rejected."""
    product = _make_product("f")
    ReservedCloudDeployment.objects.create(
        slug="lambda-prod-h100-1yr-dup",
        display_name="Lambda Production H100 1-yr",
        product=product,
        cloud_provider=product.cloud_provider,
    )
    with pytest.raises(IntegrityError):
        ReservedCloudDeployment.objects.create(
            slug="lambda-prod-h100-1yr-dup",
            display_name="Lambda Production H100 1-yr Dup",
            product=product,
            cloud_provider=product.cloud_provider,
        )


@pytest.mark.django_db
def test_mismatched_cloud_provider_fails_full_clean():
    """full_clean() must reject a deployment whose cloud_provider differs from product.cloud_provider.

    Django does not call clean() on objects.create()/save(), so this invariant is enforced
    only when full_clean() is called (e.g. via the seed command or admin).
    """
    product = _make_product("g")
    other_provider = ProviderFactory(slug="other-cloud-g", provider_type="cloud")
    dep = ReservedCloudDeployment(
        slug="lambda-prod-h100-mismatch-g",
        display_name="Lambda Production H100 1-yr (mismatch)",
        product=product,
        cloud_provider=other_provider,
    )
    with pytest.raises(ValidationError, match="cloud_provider must match"):
        dep.full_clean()
