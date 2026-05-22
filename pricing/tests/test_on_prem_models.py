"""Tests for HardwareSKU and OnPremDeployment models (M08.T01+T02)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError

from pricing.models import HardwareSKU
from pricing.tests.factories import HardwareSKUFactory, OnPremDeploymentFactory


@pytest.mark.django_db
def test_hardware_sku_created():
    sku = HardwareSKUFactory()
    assert HardwareSKU.objects.filter(pk=sku.pk).exists()


@pytest.mark.django_db
def test_hardware_sku_slug_unique():
    HardwareSKUFactory(slug="my-sku")
    with pytest.raises(IntegrityError):
        HardwareSKUFactory(slug="my-sku")


@pytest.mark.django_db
def test_hardware_sku_fk_to_gpu():
    sku = HardwareSKUFactory()
    assert sku.gpu is not None
    assert sku.gpu.pk is not None


@pytest.mark.django_db
def test_on_prem_deployment_defaults():
    d = OnPremDeploymentFactory()
    assert d.salvage_pct == Decimal("0.100")
    assert d.depreciation_years == 4
    assert d.expected_utilization_pct == Decimal("0.700")
    assert d.pue == Decimal("1.400")
    assert d.gpu_count_per_admin == 128
    assert d.is_active is True


@pytest.mark.django_db
def test_on_prem_deployment_slug_unique():
    OnPremDeploymentFactory(slug="unique-deploy")
    with pytest.raises(IntegrityError):
        OnPremDeploymentFactory(slug="unique-deploy")


@pytest.mark.django_db
def test_on_prem_deployment_fk_to_hardware_sku():
    d = OnPremDeploymentFactory()
    assert isinstance(d.hardware_sku, HardwareSKU)


@pytest.mark.django_db
def test_on_prem_deployment_str():
    d = OnPremDeploymentFactory(display_name="My Lab")
    assert str(d) == "My Lab"
