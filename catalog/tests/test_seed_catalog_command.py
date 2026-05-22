from __future__ import annotations

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.core.management import call_command
from django.core.management.base import CommandError

from catalog.models import GPU, Model, Quantization
from catalog.tests.conftest import MINIMAL_MODELS_YAML


@pytest.mark.django_db
def test_seed_catalog_idempotent(tmp_seeds_dir):
    """Invariant I8. Running the command twice produces zero changes."""
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    counts_before = (GPU.objects.count(), Model.objects.count(), Quantization.objects.count())
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    counts_after = (GPU.objects.count(), Model.objects.count(), Quantization.objects.count())
    assert counts_before == counts_after


@pytest.mark.django_db
def test_seed_catalog_creates_expected_counts(tmp_seeds_dir):
    """One H100, one Qwen-Coder-32B, one fp16."""
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    assert GPU.objects.filter(slug="nvidia-h100-sxm-80").exists()
    assert Model.objects.filter(slug="qwen-2-5-coder-32b").exists()
    assert Quantization.objects.filter(slug="fp16").exists()


@pytest.mark.django_db
def test_seed_catalog_updates_existing_row(tmp_seeds_dir):
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    gpu = GPU.objects.get(slug="nvidia-h100-sxm-80")
    original_tflops = gpu.fp16_tflops
    (tmp_seeds_dir / "gpus.yaml").write_text(
        (tmp_seeds_dir / "gpus.yaml")
        .read_text()
        .replace(
            f"fp16_tflops: {original_tflops}",
            f"fp16_tflops: {original_tflops + 1}",
        )
    )
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    gpu.refresh_from_db()
    assert gpu.fp16_tflops == original_tflops + 1


@pytest.mark.django_db
def test_seed_catalog_rejects_invalid_yaml(tmp_seeds_dir):
    (tmp_seeds_dir / "gpus.yaml").write_text("- slug: bad\n  vendor: intel\n")
    with pytest.raises(CommandError):
        call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))


@pytest.mark.django_db
def test_seed_catalog_rejects_gpu_yaml_with_missing_slug_field(tmp_seeds_dir):
    """A GPU entry without a slug field must fail loudly and leave no partial
    write — a missing slug would create an orphaned row with no join key."""
    (tmp_seeds_dir / "gpus.yaml").write_text(
        "- display_name: Mystery GPU\n"
        "  vendor: nvidia\n"
        "  architecture: hopper\n"
        "  vram_gb: 80\n"
        "  memory_bandwidth_gbs: 3350\n"
        "  fp16_tflops: 989.0\n"
        "  tdp_watts: 700\n"
        "  interconnect: nvlink\n"
    )
    with pytest.raises(CommandError, match="YAML schema validation failed"):
        call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    assert GPU.objects.count() == 0


@pytest.mark.django_db
def test_seed_catalog_rejects_negative_tdp_watts(tmp_seeds_dir):
    """Negative tdp_watts would produce negative power costs in on-prem TCO math.
    The seed command must reject it at ingest time, not silently store it."""
    original = (tmp_seeds_dir / "gpus.yaml").read_text()
    (tmp_seeds_dir / "gpus.yaml").write_text(original.replace("tdp_watts: 700", "tdp_watts: -1"))
    with pytest.raises(CommandError, match="YAML schema validation failed"):
        call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    assert GPU.objects.count() == 0


@pytest.mark.django_db
def test_seed_catalog_update_preserves_unchanged_fields(tmp_seeds_dir):
    """When re-seeding with one changed field, all other fields must retain
    their original values — update must be a true upsert, not a full replace."""
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    gpu_before = GPU.objects.get(slug="nvidia-h100-sxm-80")
    original_vram = gpu_before.vram_gb
    original_vendor = gpu_before.vendor

    original = (tmp_seeds_dir / "gpus.yaml").read_text()
    (tmp_seeds_dir / "gpus.yaml").write_text(original.replace("fp16_tflops: 989.0", "fp16_tflops: 999.0"))
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))

    gpu_after = GPU.objects.get(slug="nvidia-h100-sxm-80")
    assert gpu_after.fp16_tflops == 999.0
    assert gpu_after.vram_gb == original_vram
    assert gpu_after.vendor == original_vendor


@pytest.mark.django_db
def test_seed_catalog_rejects_quantization_with_weight_bits_not_in_valid_set(tmp_seeds_dir):
    """weight_bits must be one of {4, 8, 16} — those are the only precisions
    vLLM/SGLang support. An invalid value like 12 must be rejected at ingest so
    no Quantization row enters the DB with an unsupported bit-width."""
    (tmp_seeds_dir / "quantizations.yaml").write_text(
        "- slug: bad-quant\n  display_name: Bad Quant\n  weight_bits: 12\n  kv_cache_bits: 8\n"
    )
    with pytest.raises(CommandError, match="YAML schema validation failed"):
        call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    assert Quantization.objects.count() == 0


@pytest.mark.django_db
def test_seed_catalog_gpu_without_optional_fp8_tflops_saves_as_null(tmp_seeds_dir):
    """fp8_tflops is optional — older GPUs like V100 don't support fp8 natively.
    Omitting the field in the YAML must result in a NULL DB value, not a 0 or error."""
    (tmp_seeds_dir / "gpus.yaml").write_text(
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
    call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    gpu = GPU.objects.get(slug="nvidia-v100-16gb")
    assert gpu.fp8_tflops is None


@pytest.mark.django_db
def test_seed_catalog_rejects_model_referencing_unknown_quant_slug(tmp_seeds_dir):
    """A model YAML referencing a quant slug not present in the seeds must fail
    loudly — the transaction rolls back so no partial model row is written.
    Silently saving an unresolvable FK would corrupt the cost-cell join."""
    (tmp_seeds_dir / "models" / "qwen.yaml").write_text(
        MINIMAL_MODELS_YAML.replace("recommended_quant: fp8-e4m3", "recommended_quant: nonexistent-quant")
    )
    with pytest.raises(ObjectDoesNotExist):
        call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_dir))
    assert Model.objects.count() == 0
