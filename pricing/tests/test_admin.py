from __future__ import annotations

import pytest
from django.contrib.admin.sites import site

from pricing.models import PricingSnapshot, Provider


@pytest.mark.django_db
def test_pricing_models_registered_in_admin() -> None:
    assert site.is_registered(Provider)
    assert site.is_registered(PricingSnapshot)
