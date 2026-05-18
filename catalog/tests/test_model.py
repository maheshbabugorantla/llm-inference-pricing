from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from catalog.tests.factories import ModelFactory, QuantizationFactory


@pytest.mark.django_db
def test_model_slug_must_be_unique() -> None:
    ModelFactory(slug="deepseek-coder-v3")
    with pytest.raises(IntegrityError):
        ModelFactory(slug="deepseek-coder-v3")


@pytest.mark.django_db
def test_model_recommended_tp_must_be_in_valid_set() -> None:
    """tp_size ∈ {1, 2, 4, 8}"""
    for tp in (1, 2, 4, 8):
        m = ModelFactory(recommended_tp=tp)
        m.full_clean()
    bad = ModelFactory(recommended_tp=3)
    with pytest.raises(ValidationError):
        bad.full_clean()


@pytest.mark.django_db
def test_moe_model_total_params_separate_from_active() -> None:
    moe = ModelFactory(
        architecture="moe",
        total_params_b=671,
        active_params_b=37,
    )
    assert moe.total_params_b > moe.active_params_b


@pytest.mark.django_db
def test_dense_model_total_equals_active() -> None:
    dense = ModelFactory(architecture="dense", total_params_b=32.5, active_params_b=32.5)
    dense.full_clean()
    bad = ModelFactory(architecture="dense", total_params_b=32.5, active_params_b=10)
    with pytest.raises(ValidationError):
        bad.full_clean()


@pytest.mark.django_db
def test_recommended_quant_required() -> None:
    q = QuantizationFactory()
    m = ModelFactory(recommended_quant=q)
    assert m.recommended_quant_id == q.id
