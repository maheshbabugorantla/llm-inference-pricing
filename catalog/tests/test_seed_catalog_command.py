from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from catalog.models import GPU, Model, Quantization


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
