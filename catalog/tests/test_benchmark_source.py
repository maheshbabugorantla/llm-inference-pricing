from __future__ import annotations

import pytest
from django.db.utils import IntegrityError

from catalog.tests.factories import BenchmarkSourceFactory


@pytest.mark.django_db
def test_benchmark_source_slug_unique():
    BenchmarkSourceFactory(slug="vllm-blog-2025-03")
    with pytest.raises(IntegrityError):
        BenchmarkSourceFactory(slug="vllm-blog-2025-03")


@pytest.mark.django_db
def test_benchmark_source_str_returns_title():
    s = BenchmarkSourceFactory(title="vLLM 0.7 FP8 on H100")
    assert str(s) == "vLLM 0.7 FP8 on H100"
