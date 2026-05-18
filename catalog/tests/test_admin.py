from __future__ import annotations

import pytest
from django.contrib.admin.sites import site

from catalog.models import GPU, BenchmarkPoint, BenchmarkSource, Model, Quantization


@pytest.mark.django_db
def test_catalog_models_registered_in_admin():
    assert site.is_registered(GPU)
    assert site.is_registered(Model)
    assert site.is_registered(Quantization)
    assert site.is_registered(BenchmarkSource)
    assert site.is_registered(BenchmarkPoint)
