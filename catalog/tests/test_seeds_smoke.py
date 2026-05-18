from __future__ import annotations

import pytest
from django.core.management import call_command

from catalog.models import GPU, Model, Quantization


@pytest.mark.django_db
@pytest.mark.smoke
def test_real_seeds_load():
    """Smoke test: seed_catalog loads 12 GPUs, 5 models, 8 quantizations."""
    call_command("seed_catalog")
    assert GPU.objects.count() == 12
    assert Model.objects.count() == 5
    assert Quantization.objects.count() == 8
