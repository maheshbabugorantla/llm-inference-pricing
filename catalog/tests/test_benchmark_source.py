from __future__ import annotations

from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.tests.factories import BenchmarkSourceFactory


class BenchmarkSourceTest(TestCase):
    def test_benchmark_source_slug_unique(self):
        BenchmarkSourceFactory(slug="vllm-blog-2025-03")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BenchmarkSourceFactory(slug="vllm-blog-2025-03")

    def test_benchmark_source_str_returns_title(self):
        s = BenchmarkSourceFactory(title="vLLM 0.7 FP8 on H100")
        self.assertEqual(str(s), "vLLM 0.7 FP8 on H100")
