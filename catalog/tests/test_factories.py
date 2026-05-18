from __future__ import annotations

import pytest

from catalog.tests.factories import GPUFactory, ModelFactory


@pytest.mark.django_db
def test_gpu_factory_creates_unique_slugs() -> None:
    g1 = GPUFactory()
    g2 = GPUFactory()
    assert g1.slug != g2.slug


@pytest.mark.django_db
def test_model_factory_attaches_recommended_quant() -> None:
    m = ModelFactory()
    assert m.recommended_quant_id is not None
