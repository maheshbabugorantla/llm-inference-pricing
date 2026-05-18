from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from catalog.tests.factories import QuantizationFactory


@pytest.mark.django_db
def test_quantization_slug_must_be_unique() -> None:
    QuantizationFactory(slug="fp8-e4m3")
    with pytest.raises(IntegrityError):
        QuantizationFactory(slug="fp8-e4m3")


@pytest.mark.django_db
def test_quantization_str_returns_display_name() -> None:
    q = QuantizationFactory(display_name="FP8 (E4M3)")
    assert str(q) == "FP8 (E4M3)"


@pytest.mark.django_db
def test_quantization_weight_bits_constrained_to_valid_values() -> None:
    """Invariant I7."""
    q = QuantizationFactory(weight_bits=16)
    q.full_clean()
    q.weight_bits = 7
    with pytest.raises(ValidationError):
        q.full_clean()
