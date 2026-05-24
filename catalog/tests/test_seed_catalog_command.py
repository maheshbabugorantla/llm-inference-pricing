from __future__ import annotations

import shutil

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from catalog.models import GPU, Model, Quantization
from catalog.tests.seed_helpers import MINIMAL_MODELS_YAML, make_tmp_seeds_dir


class SeedCatalogCommandTest(TestCase):
    def setUp(self):
        self.seeds_dir = make_tmp_seeds_dir()

    def tearDown(self):
        shutil.rmtree(self.seeds_dir, ignore_errors=True)

    def test_seed_catalog_idempotent(self):
        """Invariant I8. Running the command twice produces zero changes."""
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        counts_before = (GPU.objects.count(), Model.objects.count(), Quantization.objects.count())
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        counts_after = (GPU.objects.count(), Model.objects.count(), Quantization.objects.count())
        self.assertEqual(counts_before, counts_after)

    def test_seed_catalog_creates_expected_counts(self):
        """One H100, one Qwen-Coder-32B, one fp16."""
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        self.assertTrue(GPU.objects.filter(slug="nvidia-h100-sxm-80").exists())
        self.assertTrue(Model.objects.filter(slug="qwen-2-5-coder-32b").exists())
        self.assertTrue(Quantization.objects.filter(slug="fp16").exists())

    def test_seed_catalog_updates_existing_row(self):
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        gpu = GPU.objects.get(slug="nvidia-h100-sxm-80")
        original_tflops = gpu.fp16_tflops
        gpus_yaml = self.seeds_dir / "gpus.yaml"
        gpus_yaml.write_text(
            gpus_yaml.read_text().replace(
                f"fp16_tflops: {original_tflops}",
                f"fp16_tflops: {original_tflops + 1}",
            )
        )
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        gpu.refresh_from_db()
        self.assertEqual(gpu.fp16_tflops, original_tflops + 1)

    def test_seed_catalog_rejects_invalid_yaml(self):
        (self.seeds_dir / "gpus.yaml").write_text("- slug: bad\n  vendor: intel\n")
        with self.assertRaises(CommandError):
            call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))

    def test_seed_catalog_rejects_gpu_yaml_with_missing_slug_field(self):
        (self.seeds_dir / "gpus.yaml").write_text(
            "- display_name: Mystery GPU\n"
            "  vendor: nvidia\n"
            "  architecture: hopper\n"
            "  vram_gb: 80\n"
            "  memory_bandwidth_gbs: 3350\n"
            "  fp16_tflops: 989.0\n"
            "  tdp_watts: 700\n"
            "  interconnect: nvlink\n"
        )
        with self.assertRaisesRegex(CommandError, "YAML schema validation failed"):
            call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        self.assertEqual(GPU.objects.count(), 0)

    def test_seed_catalog_rejects_negative_tdp_watts(self):
        original = (self.seeds_dir / "gpus.yaml").read_text()
        (self.seeds_dir / "gpus.yaml").write_text(original.replace("tdp_watts: 700", "tdp_watts: -1"))
        with self.assertRaisesRegex(CommandError, "YAML schema validation failed"):
            call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        self.assertEqual(GPU.objects.count(), 0)

    def test_seed_catalog_update_preserves_unchanged_fields(self):
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        gpu_before = GPU.objects.get(slug="nvidia-h100-sxm-80")
        original_vram = gpu_before.vram_gb
        original_vendor = gpu_before.vendor
        original = (self.seeds_dir / "gpus.yaml").read_text()
        (self.seeds_dir / "gpus.yaml").write_text(
            original.replace("fp16_tflops: 989.0", "fp16_tflops: 999.0")
        )
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        gpu_after = GPU.objects.get(slug="nvidia-h100-sxm-80")
        self.assertEqual(gpu_after.fp16_tflops, 999.0)
        self.assertEqual(gpu_after.vram_gb, original_vram)
        self.assertEqual(gpu_after.vendor, original_vendor)

    def test_seed_catalog_rejects_quantization_with_weight_bits_not_in_valid_set(self):
        (self.seeds_dir / "quantizations.yaml").write_text(
            "- slug: bad-quant\n  display_name: Bad Quant\n  weight_bits: 12\n  kv_cache_bits: 8\n"
        )
        with self.assertRaisesRegex(CommandError, "YAML schema validation failed"):
            call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        self.assertEqual(Quantization.objects.count(), 0)

    def test_seed_catalog_gpu_without_optional_fp8_tflops_saves_as_null(self):
        (self.seeds_dir / "gpus.yaml").write_text(
            "- slug: nvidia-v100-16gb\n"
            "  display_name: NVIDIA V100 16GB\n"
            "  vendor: nvidia\n"
            "  architecture: volta\n"
            "  vram_gb: 16\n"
            "  memory_bandwidth_gbs: 900\n"
            "  fp16_tflops: 125.0\n"
            "  tdp_watts: 300\n"
            "  interconnect: nvlink\n"
        )
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        gpu = GPU.objects.get(slug="nvidia-v100-16gb")
        self.assertIsNone(gpu.fp8_tflops)

    def test_seed_catalog_rejects_model_referencing_unknown_quant_slug(self):
        (self.seeds_dir / "models" / "qwen.yaml").write_text(
            MINIMAL_MODELS_YAML.replace("recommended_quant: fp8-e4m3", "recommended_quant: nonexistent-quant")
        )
        with self.assertRaisesRegex(CommandError, "nonexistent-quant"):
            call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        self.assertEqual(Model.objects.count(), 0)
