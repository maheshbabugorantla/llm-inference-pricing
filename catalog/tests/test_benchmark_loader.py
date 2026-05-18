from __future__ import annotations

import logging

import pytest
from django.core.management import call_command

from catalog.models import BenchmarkPoint


@pytest.fixture
def tmp_seeds_with_benchmarks(tmp_seeds_dir):
    """Extends tmp_seeds_dir with one fitting row and one infeasible row."""
    bench_dir = tmp_seeds_dir / "benchmarks"
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
    return tmp_seeds_dir


@pytest.mark.django_db
def test_benchmark_loader_keeps_fitting_rows(tmp_seeds_with_benchmarks):
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_with_benchmarks))
    assert BenchmarkPoint.objects.count() == 1
    bp = BenchmarkPoint.objects.get()
    assert bp.batch_size == 8


@pytest.mark.django_db
def test_benchmark_loader_logs_warning_for_infeasible_rows(tmp_seeds_with_benchmarks, caplog):
    with caplog.at_level(logging.WARNING, logger="catalog.seed"):
        call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_with_benchmarks))
    skipped = [r for r in caplog.records if "fit check failed" in r.message]
    assert len(skipped) == 1
    assert "batch_size=32" in skipped[0].message
