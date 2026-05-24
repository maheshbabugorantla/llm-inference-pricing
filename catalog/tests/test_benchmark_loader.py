from __future__ import annotations

import logging
import shutil

from django.core.management import call_command
from django.test import TestCase

from catalog.models import BenchmarkPoint
from catalog.tests.seed_helpers import make_tmp_seeds_dir


def _add_benchmarks(seeds_dir):
    """Extend a seeds dir with one fitting row and one infeasible row."""
    bench_dir = seeds_dir / "benchmarks"
    bench_dir.mkdir()
    (bench_dir / "src.yaml").write_text("""\
source:
  slug: test-src
  title: Test source
  url: https://example.com/x
  publisher: vllm
  published_at: 2025-03-12
  engine: vllm
  engine_version: 0.7.0

points:
  - model: qwen-2-5-coder-32b
    gpu: nvidia-h100-sxm-80
    quantization: fp8-e4m3
    tp_size: 1
    batch_size: 8
    context_length: 32768
    prefill_tps_aggregate: 28400
    decode_tps_aggregate: 920
  # this one fails fit per PRD Appendix A:
  - model: qwen-2-5-coder-32b
    gpu: nvidia-h100-sxm-80
    quantization: fp8-e4m3
    tp_size: 1
    batch_size: 32
    context_length: 32768
    prefill_tps_aggregate: 50000
    decode_tps_aggregate: 1800
""")
    return seeds_dir


class BenchmarkLoaderTest(TestCase):
    def setUp(self):
        self.seeds_dir = _add_benchmarks(make_tmp_seeds_dir())

    def tearDown(self):
        shutil.rmtree(self.seeds_dir, ignore_errors=True)

    def test_benchmark_loader_keeps_fitting_rows(self):
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        self.assertEqual(BenchmarkPoint.objects.count(), 1)
        bp = BenchmarkPoint.objects.get()
        self.assertEqual(bp.batch_size, 8)

    def test_benchmark_loader_logs_warning_for_infeasible_rows(self):
        with self.assertLogs("catalog.seed", level=logging.WARNING) as log_ctx:
            call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        skipped = [msg for msg in log_ctx.output if "fit check failed" in msg]
        self.assertEqual(len(skipped), 1)
        self.assertIn("batch_size=32", skipped[0])
