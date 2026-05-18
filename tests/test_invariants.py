from __future__ import annotations

import pytest
from django.core.management import call_command

from catalog.models import GPU, Model, Quantization


@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i3_recommended_quant_always_exists():
    """I3: every Model row has a non-null FK to an existing Quantization."""
    call_command("seed_catalog")
    for model in Model.objects.all():
        assert model.recommended_quant_id is not None
        Quantization.objects.get(pk=model.recommended_quant_id)


@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i8_seeds_idempotent():
    """I8: running seed_catalog twice produces identical counts."""
    call_command("seed_catalog")
    counts_a = {
        "gpus": GPU.objects.count(),
        "models": Model.objects.count(),
        "quants": Quantization.objects.count(),
    }
    call_command("seed_catalog")
    counts_b = {
        "gpus": GPU.objects.count(),
        "models": Model.objects.count(),
        "quants": Quantization.objects.count(),
    }
    assert counts_a == counts_b
