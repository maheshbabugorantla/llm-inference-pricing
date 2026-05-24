"""Tests for HardwareSKU and OnPremDeployment models (M08.T01+T02)."""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from pricing.models import HardwareSKU
from pricing.tests.factories import HardwareSKUFactory, OnPremDeploymentFactory


class HardwareSKUTest(TestCase):
    def test_hardware_sku_created(self):
        sku = HardwareSKUFactory()
        self.assertTrue(HardwareSKU.objects.filter(pk=sku.pk).exists())

    def test_hardware_sku_slug_unique(self):
        HardwareSKUFactory(slug="my-sku")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HardwareSKUFactory(slug="my-sku")

    def test_hardware_sku_fk_to_gpu(self):
        sku = HardwareSKUFactory()
        self.assertIsNotNone(sku.gpu)
        self.assertIsNotNone(sku.gpu.pk)


class OnPremDeploymentTest(TestCase):
    def test_on_prem_deployment_defaults(self):
        d = OnPremDeploymentFactory()
        self.assertEqual(d.salvage_pct, Decimal("0.100"))
        self.assertEqual(d.depreciation_years, 4)
        self.assertEqual(d.expected_utilization_pct, Decimal("0.700"))
        self.assertEqual(d.pue, Decimal("1.400"))
        self.assertEqual(d.gpu_count_per_admin, 128)
        self.assertTrue(d.is_active)

    def test_on_prem_deployment_slug_unique(self):
        OnPremDeploymentFactory(slug="unique-deploy")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OnPremDeploymentFactory(slug="unique-deploy")

    def test_on_prem_deployment_fk_to_hardware_sku(self):
        d = OnPremDeploymentFactory()
        self.assertIsInstance(d.hardware_sku, HardwareSKU)

    def test_on_prem_deployment_str(self):
        d = OnPremDeploymentFactory(display_name="My Lab")
        self.assertEqual(str(d), "My Lab")
