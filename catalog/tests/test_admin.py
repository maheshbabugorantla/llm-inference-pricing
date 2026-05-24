from __future__ import annotations

from django.contrib.admin.sites import site
from django.test import TestCase

from catalog.models import GPU, BenchmarkPoint, BenchmarkSource, Model, Quantization


class CatalogAdminRegistrationTest(TestCase):
    def test_catalog_models_registered_in_admin(self):
        self.assertTrue(site.is_registered(GPU))
        self.assertTrue(site.is_registered(Model))
        self.assertTrue(site.is_registered(Quantization))
        self.assertTrue(site.is_registered(BenchmarkSource))
        self.assertTrue(site.is_registered(BenchmarkPoint))
