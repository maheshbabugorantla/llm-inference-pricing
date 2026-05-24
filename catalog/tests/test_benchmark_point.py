"""BenchmarkPoint business-scenario tests.

Design: prove that the BenchmarkPoint model correctly captures real inference
benchmarks, enforces the uniqueness invariant (I6), FK traversal to GPU VRAM
works end-to-end, and that context length + TP size govern which operating
points can physically run — because these determine what goes in the cost grid.
"""

from __future__ import annotations

import unittest

from django.db import IntegrityError, transaction
from django.test import TestCase
from pydantic import ValidationError as PydanticValidationError

from catalog.services.fit import compute_fit
from catalog.services.seed import BenchmarkPointYAML
from catalog.tests.factories import BenchmarkPointFactory, GPUFactory, ModelFactory, QuantizationFactory


class BenchmarkPointDBTest(TestCase):
    def test_qwen32b_fp8_tp1_on_h100_batch8_fits_and_benchmark_recorded(self):
        """Qwen2.5-Coder-32B at fp8, tp=1 fits on an 80 GB H100 at batch=8/ctx=4k
        and a BenchmarkPoint recording that run is queryable by its compat-tuple.

        PRD §4.1 reference case — this is the lowest-cost production-realistic setup.
        """
        gpu = GPUFactory(slug="nvidia-h100-sxm-80", vram_gb=80)
        quant = QuantizationFactory(slug="fp8-e4m3", weight_bits=8, kv_cache_bits=8)
        model = ModelFactory(
            slug="qwen-2-5-coder-32b",
            architecture="dense",
            total_params_b=32.5,
            active_params_b=32.5,
            num_layers=64,
            num_kv_heads=8,
            head_dim=128,
            recommended_quant=quant,
            recommended_tp=1,
        )

        fits, _, _ = compute_fit(
            total_params_b=model.total_params_b,
            num_layers=model.num_layers,
            num_kv_heads=model.num_kv_heads,
            head_dim=model.head_dim,
            architecture=model.architecture,
            weight_bits=quant.weight_bits,
            kv_cache_bits=quant.kv_cache_bits,
            tp_size=1,
            batch_size=8,
            context_length=4096,
            gpu_vram_gb=gpu.vram_gb,
        )
        self.assertTrue(fits, "Qwen-32B fp8 tp=1 should fit on H100 80GB at batch=8/ctx=4k")

        point = BenchmarkPointFactory(
            model=model,
            gpu=gpu,
            quantization=quant,
            tp_size=1,
            batch_size=8,
            context_length=4096,
            decode_tps_aggregate=920.0,
        )

        fetched = type(point).objects.get(
            model=model, gpu=gpu, quantization=quant, tp_size=1, batch_size=8, context_length=4096
        )
        self.assertEqual(fetched.decode_tps_aggregate, 920.0)

    def test_benchmark_point_unique_together_blocks_duplicate_operating_point(self):
        """Invariant I6: two BenchmarkPoint rows cannot share the same
        (model, gpu, quantization, tp_size, batch_size, context_length) tuple."""
        bp = BenchmarkPointFactory()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BenchmarkPointFactory(
                    model=bp.model,
                    gpu=bp.gpu,
                    quantization=bp.quantization,
                    tp_size=bp.tp_size,
                    batch_size=bp.batch_size,
                    context_length=bp.context_length,
                )

    def test_benchmark_point_links_to_correct_gpu_vram(self):
        """The FK chain BenchmarkPoint → GPU exposes vram_gb so the cost pipeline
        can read hardware specs without a separate GPU lookup."""
        gpu = GPUFactory(vram_gb=80)
        point = BenchmarkPointFactory(gpu=gpu)
        self.assertEqual(point.gpu.vram_gb, 80)

    def test_prefill_and_decode_tps_stored_as_independent_fields_for_workload_costing(self):
        """Prefill TPS and decode TPS are independently costed — prefill cost drives
        prompt-ingestion pricing while decode TPS drives output-token pricing. Both
        fields must survive a full DB round-trip with their original values intact."""
        point = BenchmarkPointFactory(
            prefill_tps_aggregate=28400.0,
            decode_tps_aggregate=920.0,
        )
        fetched = type(point).objects.get(pk=point.pk)
        self.assertEqual(fetched.prefill_tps_aggregate, 28400.0)
        self.assertEqual(fetched.decode_tps_aggregate, 920.0)
        self.assertNotEqual(fetched.prefill_tps_aggregate, fetched.decode_tps_aggregate)

    def test_benchmark_ttft_ms_is_optional_for_throughput_only_workloads(self):
        """TTFT is only measured in some benchmarks; many publish throughput-only
        numbers without latency. A NULL ttft_ms must be accepted so we can ingest
        the full benchmark dataset, not just the subset that measured latency."""
        point = BenchmarkPointFactory(ttft_ms=None)
        self.assertIsNone(point.ttft_ms)
        fetched = type(point).objects.get(pk=point.pk)
        self.assertIsNone(fetched.ttft_ms)


class BenchmarkPointPureTest(unittest.TestCase):
    def test_qwen32b_fp16_tp1_does_not_fit_h100_at_128k_context_batch8(self):
        """At 128k context with batch=8, KV cache alone exceeds ~275 GB — far beyond
        the 80 GB H100's VRAM. The benchmark grid must not include this operating
        point, or cost cells would be generated for runs that cannot physically execute.

        fp16 weights: 32.5B x 2 bytes = 65 GB
        KV cache: 2 x 64 layers x 8 heads x 128 dim x 2 bytes x 8 batch x 131072 ctx ~274 GB
        Total per GPU >> 80 GB → must not fit.
        """
        fits, _, _ = compute_fit(
            total_params_b=32.5,
            num_layers=64,
            num_kv_heads=8,
            head_dim=128,
            architecture="dense",
            weight_bits=16,
            kv_cache_bits=16,
            tp_size=1,
            batch_size=8,
            context_length=131072,
            gpu_vram_gb=80,
        )
        self.assertFalse(fits, "Qwen-32B fp16 tp=1 must not fit on H100 80GB at ctx=128k batch=8")

    def test_aggregate_decode_tps_must_be_positive(self):
        """Zero or negative decode TPS is rejected at the YAML ingestion boundary,
        preventing benchmark records with physically impossible throughput from
        entering the DB and corrupting cost-cell calculations."""
        with self.assertRaises(PydanticValidationError):
            BenchmarkPointYAML(
                model="qwen-2-5-coder-32b",
                gpu="nvidia-h100-sxm-80",
                quantization="fp8-e4m3",
                tp_size=1,
                batch_size=8,
                context_length=4096,
                prefill_tps_aggregate=1000.0,
                decode_tps_aggregate=0.0,
            )
