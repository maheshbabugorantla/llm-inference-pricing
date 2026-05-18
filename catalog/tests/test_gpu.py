from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from catalog.tests.factories import GPUFactory


@pytest.mark.django_db
def test_gpu_slug_must_be_unique() -> None:
    GPUFactory(slug="nvidia-h100-sxm-80")
    with pytest.raises(IntegrityError):
        GPUFactory(slug="nvidia-h100-sxm-80")


@pytest.mark.django_db
def test_gpu_str_returns_display_name() -> None:
    g = GPUFactory(display_name="NVIDIA H100 SXM5 80GB")
    assert str(g) == "NVIDIA H100 SXM5 80GB"


@pytest.mark.django_db
def test_gpu_vendor_choices_enforced() -> None:
    g = GPUFactory(vendor="intel")
    with pytest.raises(ValidationError):
        g.full_clean()


@pytest.mark.django_db
def test_gpu_tdp_watts_required_and_positive() -> None:
    with pytest.raises(IntegrityError):
        GPUFactory(tdp_watts=None)
