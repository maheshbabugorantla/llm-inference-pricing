from __future__ import annotations

from django.test import TestCase

from catalog.tests.factories import GPUFactory, ModelFactory


class FactoriesTest(TestCase):
    def test_gpu_factory_creates_unique_slugs(self) -> None:
        g1 = GPUFactory()
        g2 = GPUFactory()
        self.assertNotEqual(g1.slug, g2.slug)

    def test_model_factory_attaches_recommended_quant(self) -> None:
        m = ModelFactory()
        self.assertIsNotNone(m.recommended_quant_id)
