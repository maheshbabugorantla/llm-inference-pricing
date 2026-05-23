"""Tests for reserved cloud snapshot generator (M09.T04)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from catalog.tests.factories import GPUFactory
from pricing.generators.reserved_cloud import regenerate_reserved_cloud_snapshots
from pricing.models import PricingSnapshot, ReservedCapacityProduct, ReservedCloudDeployment
from pricing.tests.factories import ProviderFactory


def _make_scenario(slug_suffix: str = "") -> tuple[ReservedCloudDeployment, str]:
    """Create a ReservedCapacityProduct + ReservedCloudDeployment; return (deployment, provider_slug)."""
    gpu = GPUFactory(slug=f"nvidia-h100-sxm-80-gen{slug_suffix}", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(slug=f"lambda-gen{slug_suffix}", provider_type="cloud")
    product = ReservedCapacityProduct.objects.create(
        slug=f"lambda-h100-1yr-gen{slug_suffix}",
        display_name="Lambda Reserved H100 1-yr",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="all_upfront",
        term_months=12,
        upfront_usd=Decimal("500000.00"),
        listing_observed_at="2025-01-15",
    )
    deployment = ReservedCloudDeployment.objects.create(
        slug=f"lambda-prod-h100-1yr-gen{slug_suffix}",
        display_name="Lambda Production H100 1-yr",
        product=product,
        cloud_provider=provider,
        expected_utilization_pct=Decimal("0.700"),
    )
    return deployment, provider.slug


@pytest.mark.django_db(transaction=True)
def test_generator_emits_both_committed_and_marginal_snapshots():
    """Each active deployment produces exactly two snapshots: reserved-{slug} and reserved-marginal-{slug}."""
    deployment, _ = _make_scenario("a")
    PricingSnapshot.objects.all().delete()

    written = regenerate_reserved_cloud_snapshots()

    assert written == 2
    tiers = set(PricingSnapshot.objects.values_list("tier", flat=True))
    assert f"reserved-{deployment.slug}" in tiers
    assert f"reserved-marginal-{deployment.slug}" in tiers


@pytest.mark.django_db(transaction=True)
def test_generator_reuses_cloud_provider_not_synthetic_one():
    """ADR-010: reserved snapshots attach to the existing cloud Provider, not a new synthetic one.

    The provider slug in the snapshot must equal the cloud_provider slug from the deployment,
    not a fabricated 'lambda-reserved-...' slug.
    """
    _deployment, provider_slug = _make_scenario("b")
    PricingSnapshot.objects.all().delete()

    regenerate_reserved_cloud_snapshots()

    snaps = PricingSnapshot.objects.filter(tier__startswith="reserved-")
    assert snaps.exists()
    for snap in snaps:
        assert snap.provider.slug == provider_slug, (
            f"Expected cloud provider slug '{provider_slug}', got '{snap.provider.slug}'"
        )


@pytest.mark.django_db(transaction=True)
def test_tier_string_is_composite_with_deployment_slug():
    """Tier must be 'reserved-<slug>' and 'reserved-marginal-<slug>' for traceability."""
    deployment, _ = _make_scenario("c")
    PricingSnapshot.objects.all().delete()

    regenerate_reserved_cloud_snapshots()

    snaps = PricingSnapshot.objects.all()
    tier_strings = {s.tier for s in snaps}
    assert f"reserved-{deployment.slug}" in tier_strings
    assert f"reserved-marginal-{deployment.slug}" in tier_strings


@pytest.mark.django_db(transaction=True)
def test_generator_snapshots_have_correct_gpu():
    """Each generated snapshot must reference the GPU from the product, not any other GPU."""
    deployment, _ = _make_scenario("d")
    expected_gpu_slug = deployment.product.gpu.slug
    PricingSnapshot.objects.all().delete()

    regenerate_reserved_cloud_snapshots()

    for snap in PricingSnapshot.objects.all():
        assert snap.gpu.slug == expected_gpu_slug


@pytest.mark.django_db(transaction=True)
def test_generator_skips_inactive_deployments():
    """Inactive ReservedCloudDeployment rows must not produce snapshots."""
    deployment, _ = _make_scenario("e")
    deployment.is_active = False
    deployment.save()
    PricingSnapshot.objects.all().delete()

    written = regenerate_reserved_cloud_snapshots()

    assert written == 0
    assert PricingSnapshot.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_generator_raw_payload_contains_deployment_and_product_slug():
    """raw_payload must record deployment_slug and product_slug for auditability."""
    deployment, _ = _make_scenario("f")
    PricingSnapshot.objects.all().delete()

    regenerate_reserved_cloud_snapshots()

    snap = PricingSnapshot.objects.first()
    assert snap is not None
    assert snap.raw_payload["deployment_slug"] == deployment.slug
    assert snap.raw_payload["product_slug"] == deployment.product.slug


@pytest.mark.django_db(transaction=True)
def test_generator_returns_count_equal_to_two_per_active_deployment():
    """With N active deployments, regenerate_reserved_cloud_snapshots returns 2*N."""
    _make_scenario("g1")
    _make_scenario("g2")
    _make_scenario("g3")
    PricingSnapshot.objects.all().delete()

    written = regenerate_reserved_cloud_snapshots()

    assert written == 6
    assert PricingSnapshot.objects.count() == 6


@pytest.mark.django_db(transaction=True)
def test_post_save_signal_regenerates_snapshots_on_deployment_create():
    """Creating a ReservedCloudDeployment triggers the post_save signal → snapshots generated."""
    gpu = GPUFactory(slug="nvidia-h100-sxm-80-sig", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(slug="lambda-sig", provider_type="cloud")
    product = ReservedCapacityProduct.objects.create(
        slug="lambda-h100-1yr-sig",
        display_name="Lambda Reserved H100 1-yr",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="all_upfront",
        term_months=12,
        upfront_usd=Decimal("500000.00"),
        listing_observed_at="2025-01-15",
    )
    PricingSnapshot.objects.all().delete()

    ReservedCloudDeployment.objects.create(
        slug="lambda-sig-dep",
        display_name="Lambda Signal Test",
        product=product,
        cloud_provider=provider,
        expected_utilization_pct=Decimal("0.700"),
    )

    # Signal fires on_commit → 2 snapshots created
    assert PricingSnapshot.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_post_save_signal_retires_snapshots_on_deactivation():
    """Setting is_active=False on an existing deployment marks its snapshot tiers available=False.

    The signal fires _retire() via on_commit, which must set available=False on the two
    reserved tiers for that deployment slug. The snapshots must still exist (not deleted),
    so historical data is preserved, but they must no longer be visible in pricing queries.
    """
    deployment, _ = _make_scenario("sig2")
    # Clear any snapshots from the creation signal, then generate a clean set
    PricingSnapshot.objects.all().delete()
    regenerate_reserved_cloud_snapshots()
    tiers = [f"reserved-{deployment.slug}", f"reserved-marginal-{deployment.slug}"]
    assert PricingSnapshot.objects.filter(tier__in=tiers, available=True).count() == 2

    # Deactivate the deployment — signal should retire the snapshots via on_commit
    deployment.is_active = False
    deployment.save()

    # Snapshots still exist but must now be available=False
    assert PricingSnapshot.objects.filter(tier__in=tiers).count() == 2
    assert PricingSnapshot.objects.filter(tier__in=tiers, available=False).count() == 2
    assert PricingSnapshot.objects.filter(tier__in=tiers, available=True).count() == 0


@pytest.mark.django_db(transaction=True)
def test_post_save_signal_does_not_regenerate_when_created_inactive():
    """Creating a deployment with is_active=False must NOT trigger snapshot generation.

    An inactive deployment has no active cost tiers; emitting snapshots for it would
    pollute cost cells and then immediately require cleanup.
    """
    gpu = GPUFactory(slug="nvidia-h100-sxm-80-inact", vram_gb=80, tdp_watts=700)
    provider = ProviderFactory(slug="lambda-inact", provider_type="cloud")
    product = ReservedCapacityProduct.objects.create(
        slug="lambda-h100-1yr-inact",
        display_name="Lambda Reserved H100 1-yr (inactive)",
        cloud_provider=provider,
        gpu=gpu,
        gpus_per_node=8,
        payment_cadence="all_upfront",
        term_months=12,
        upfront_usd=Decimal("500000.00"),
        listing_observed_at="2025-01-15",
    )
    PricingSnapshot.objects.all().delete()

    ReservedCloudDeployment.objects.create(
        slug="lambda-inact-dep",
        display_name="Lambda Inactive Deployment",
        product=product,
        cloud_provider=provider,
        is_active=False,
        expected_utilization_pct=Decimal("0.700"),
    )

    assert PricingSnapshot.objects.count() == 0, (
        "Creating an inactive deployment must not emit any snapshot rows"
    )
