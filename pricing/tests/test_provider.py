from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from pricing.tests.factories import ProviderFactory


@pytest.mark.django_db
def test_provider_slug_unique():
    ProviderFactory(slug="runpod")
    with pytest.raises(IntegrityError):
        ProviderFactory(slug="runpod")


@pytest.mark.django_db
@pytest.mark.parametrize("tier", ["realtime_api", "scraped_page", "manual_curation", "synthetic"])
def test_provider_data_source_tier_accepts_valid_values(tier):
    p = ProviderFactory(data_source_tier=tier)
    p.full_clean()


@pytest.mark.django_db
def test_provider_data_source_tier_rejects_invalid():
    p = ProviderFactory(data_source_tier="something-else")
    with pytest.raises(ValidationError):
        p.full_clean()


@pytest.mark.django_db
@pytest.mark.parametrize("ptype", ["cloud", "on_prem"])
def test_provider_type_choices(ptype):
    p = ProviderFactory(provider_type=ptype)
    p.full_clean()
