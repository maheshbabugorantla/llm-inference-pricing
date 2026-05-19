from __future__ import annotations

import pytest
from django.core.management import call_command

from pricing.models import Provider


@pytest.mark.django_db
def test_seed_providers_creates_all_known_providers() -> None:
    call_command("seed_providers")
    slugs = set(Provider.objects.values_list("slug", flat=True))
    assert {"runpod", "lambda", "vast", "nebius", "gcp"}.issubset(slugs)


@pytest.mark.django_db
def test_seed_providers_is_idempotent() -> None:
    call_command("seed_providers")
    count_a = Provider.objects.count()
    call_command("seed_providers")
    count_b = Provider.objects.count()
    assert count_a == count_b


@pytest.mark.django_db
def test_seed_providers_sets_correct_data_source_tier() -> None:
    call_command("seed_providers")
    runpod = Provider.objects.get(slug="runpod")
    assert runpod.data_source_tier == "realtime_api"
    lambda_p = Provider.objects.get(slug="lambda")
    assert lambda_p.data_source_tier == "scraped_page"
