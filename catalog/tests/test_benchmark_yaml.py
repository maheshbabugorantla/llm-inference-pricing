from __future__ import annotations

import unittest

from pydantic import ValidationError

from catalog.services.seed import BenchmarkFileYAML, BenchmarkPointYAML


class BenchmarkYAMLTest(unittest.TestCase):
    def test_benchmark_point_rejects_invalid_tp_size(self) -> None:
        with self.assertRaises(ValidationError):
            BenchmarkPointYAML(
                model="m",
                gpu="g",
                quantization="q",
                tp_size=3,
                batch_size=8,
                context_length=4096,
                prefill_tps_aggregate=1000,
                decode_tps_aggregate=100,
            )

    def test_benchmark_point_rejects_negative_throughput(self) -> None:
        with self.assertRaises(ValidationError):
            BenchmarkPointYAML(
                model="m",
                gpu="g",
                quantization="q",
                tp_size=1,
                batch_size=8,
                context_length=4096,
                prefill_tps_aggregate=-1,
                decode_tps_aggregate=100,
            )

    def test_benchmark_file_yaml_groups_points_under_source(self) -> None:
        payload = {
            "source": {
                "slug": "x",
                "title": "X",
                "url": "https://e.com/x",
                "publisher": "vllm",
                "published_at": "2025-03-12",
                "engine": "vllm",
                "engine_version": "0.7.0",
            },
            "points": [
                {
                    "model": "m",
                    "gpu": "g",
                    "quantization": "q",
                    "tp_size": 1,
                    "batch_size": 8,
                    "context_length": 4096,
                    "prefill_tps_aggregate": 1000,
                    "decode_tps_aggregate": 100,
                }
            ],
        }
        parsed = BenchmarkFileYAML(**payload)
        self.assertEqual(len(parsed.points), 1)
        self.assertEqual(parsed.source.slug, "x")
