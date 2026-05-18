# M01 — Catalog Foundations

**Goal.** Land the static-ish reference data: `GPU`, `Model`, `Quantization` Django models in the `catalog` app, the `seed_catalog` and `validate_catalog` management commands, the YAML schemas via pydantic, and the initial seed YAML covering 12 GPUs, 5 models, and 8 quantizations.

**Depends on.** M00.

**Definition of done.** `python manage.py seed_catalog` loads 12 GPUs / 5 models / 8 quants idempotently. Running it twice in a row produces zero changes. `pytest catalog/tests/ -q` shows ~20 passing tests covering schema, validation, and idempotency.

---

## Task list

The tasks are ordered. Don't skip ahead.

### M01.T01 — Test fixtures + factories

Set up `catalog/tests/factories.py` and `catalog/tests/conftest.py` before any model code. This forces test thinking first.

**RED.** Create `catalog/tests/test_factories.py`:

```python
import pytest

from catalog.tests.factories import GPUFactory, ModelFactory, QuantizationFactory


@pytest.mark.django_db
def test_gpu_factory_creates_unique_slugs():
    g1 = GPUFactory()
    g2 = GPUFactory()
    assert g1.slug != g2.slug


@pytest.mark.django_db
def test_model_factory_attaches_recommended_quant():
    m = ModelFactory()
    assert m.recommended_quant_id is not None
```

Run: `pytest catalog/tests/test_factories.py -q` → expect ImportError (factories don't exist yet) and ModuleNotFoundError on `catalog.tests`.

**GREEN.** Create `catalog/tests/__init__.py`, `catalog/tests/conftest.py` (empty for now), `catalog/tests/factories.py`:

```python
import factory
from factory.django import DjangoModelFactory

from catalog.models import GPU, Model, Quantization


class QuantizationFactory(DjangoModelFactory):
    class Meta:
        model = Quantization

    slug = factory.Sequence(lambda n: f"fp16-{n}")
    display_name = "FP16"
    weight_bits = 16
    kv_cache_bits = 16


class GPUFactory(DjangoModelFactory):
    class Meta:
        model = GPU

    slug = factory.Sequence(lambda n: f"nvidia-test-gpu-{n}")
    display_name = "Test GPU"
    vendor = "nvidia"
    architecture = "hopper"
    vram_gb = 80
    memory_bandwidth_gbs = 3350
    fp16_tflops = 989
    tdp_watts = 700
    interconnect = "nvlink"


class ModelFactory(DjangoModelFactory):
    class Meta:
        model = Model

    slug = factory.Sequence(lambda n: f"test-model-{n}")
    display_name = "Test Model"
    family = "test"
    architecture = "dense"
    total_params_b = 32.5
    active_params_b = 32.5
    num_layers = 64
    num_attention_heads = 40
    num_kv_heads = 8
    head_dim = 128
    max_context = 32768
    hf_repo = "test/test"
    license = "apache-2.0"
    is_coding_specialist = True
    recommended_quant = factory.SubFactory(QuantizationFactory)
    recommended_tp = 1
```

This test won't pass yet because the models don't exist. That's expected — proceed to T02.

**REFACTOR.** Nothing yet.

---

### M01.T02 — `Quantization` model

Smallest model, no FKs. Build first to unblock factories.

**RED.** `catalog/tests/test_quantization.py`:

```python
import pytest
from django.db.utils import IntegrityError

from catalog.tests.factories import QuantizationFactory


@pytest.mark.django_db
def test_quantization_slug_must_be_unique():
    QuantizationFactory(slug="fp8-e4m3")
    with pytest.raises(IntegrityError):
        QuantizationFactory(slug="fp8-e4m3")


@pytest.mark.django_db
def test_quantization_str_returns_display_name():
    q = QuantizationFactory(display_name="FP8 (E4M3)")
    assert str(q) == "FP8 (E4M3)"


@pytest.mark.django_db
def test_quantization_weight_bits_constrained_to_valid_values():
    """Invariant I7."""
    q = QuantizationFactory(weight_bits=16)
    q.full_clean()    # no raise
    q.weight_bits = 7    # not in {16, 8, 4}
    with pytest.raises(Exception):    # ValidationError
        q.full_clean()
```

Run: `pytest catalog/tests/test_quantization.py -q` → expect import errors on `catalog.models.Quantization`.

**GREEN.** `catalog/models.py`:

```python
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

VALID_QUANT_BITS = {4, 8, 16}


class Quantization(models.Model):
    slug = models.SlugField(unique=True, max_length=64)
    display_name = models.CharField(max_length=64)
    weight_bits = models.FloatField()
    kv_cache_bits = models.FloatField()
    requires_nvidia_arch = models.JSONField(default=list, blank=True)
    requires_amd_arch = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("weight_bits",)

    def __str__(self) -> str:
        return self.display_name

    def clean(self) -> None:
        if self.weight_bits not in VALID_QUANT_BITS:
            raise ValidationError({"weight_bits": f"must be one of {VALID_QUANT_BITS}"})
        if self.kv_cache_bits not in VALID_QUANT_BITS:
            raise ValidationError({"kv_cache_bits": f"must be one of {VALID_QUANT_BITS}"})
```

Generate migration: `python manage.py makemigrations catalog`.

Run: `pytest catalog/tests/test_quantization.py -q` → 3 passing.

**REFACTOR.** Extract `VALID_QUANT_BITS` into a module-level constant (already done). Confirm `factories.py` still imports cleanly.

---

### M01.T03 — `GPU` model

**RED.** `catalog/tests/test_gpu.py`:

```python
import pytest
from django.db.utils import IntegrityError

from catalog.tests.factories import GPUFactory


@pytest.mark.django_db
def test_gpu_slug_must_be_unique():
    GPUFactory(slug="nvidia-h100-sxm-80")
    with pytest.raises(IntegrityError):
        GPUFactory(slug="nvidia-h100-sxm-80")


@pytest.mark.django_db
def test_gpu_str_returns_display_name():
    g = GPUFactory(display_name="NVIDIA H100 SXM5 80GB")
    assert str(g) == "NVIDIA H100 SXM5 80GB"


@pytest.mark.django_db
def test_gpu_vendor_choices_enforced():
    g = GPUFactory(vendor="intel")
    with pytest.raises(Exception):    # ValidationError on full_clean
        g.full_clean()


@pytest.mark.django_db
def test_gpu_tdp_watts_required_and_positive():
    with pytest.raises(IntegrityError):
        GPUFactory(tdp_watts=None)
```

**GREEN.** Append to `catalog/models.py`:

```python
class GPU(models.Model):
    VENDOR_CHOICES = [("nvidia", "NVIDIA"), ("amd", "AMD")]

    slug = models.SlugField(unique=True, max_length=64)
    display_name = models.CharField(max_length=128)
    vendor = models.CharField(max_length=16, choices=VENDOR_CHOICES)
    architecture = models.CharField(max_length=32)
    vram_gb = models.PositiveIntegerField()
    memory_bandwidth_gbs = models.PositiveIntegerField()
    fp16_tflops = models.FloatField()
    fp8_tflops = models.FloatField(null=True, blank=True)
    int8_tops = models.FloatField(null=True, blank=True)
    tdp_watts = models.PositiveIntegerField()
    interconnect = models.CharField(max_length=32)
    nvlink_bandwidth_gbs = models.PositiveIntegerField(null=True, blank=True)
    supports_fp8_native = models.BooleanField(default=False)
    supports_fp4_native = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("vendor", "slug")

    def __str__(self) -> str:
        return self.display_name
```

Migrate. Run tests → all 4 passing.

**REFACTOR.** Consider extracting `VENDOR_CHOICES` to module level if reused.

---

### M01.T04 — `Model` (the LLM model) model

**RED.** `catalog/tests/test_model.py`:

```python
import pytest
from django.db.utils import IntegrityError

from catalog.tests.factories import ModelFactory, QuantizationFactory


@pytest.mark.django_db
def test_model_slug_must_be_unique():
    ModelFactory(slug="deepseek-coder-v3")
    with pytest.raises(IntegrityError):
        ModelFactory(slug="deepseek-coder-v3")


@pytest.mark.django_db
def test_model_recommended_tp_must_be_in_valid_set():
    """tp_size ∈ {1, 2, 4, 8}"""
    for tp in (1, 2, 4, 8):
        m = ModelFactory(recommended_tp=tp)
        m.full_clean()
    bad = ModelFactory(recommended_tp=3)
    with pytest.raises(Exception):
        bad.full_clean()


@pytest.mark.django_db
def test_moe_model_total_params_separate_from_active():
    moe = ModelFactory(
        architecture="moe",
        total_params_b=671,
        active_params_b=37,
    )
    assert moe.total_params_b > moe.active_params_b


@pytest.mark.django_db
def test_dense_model_total_equals_active():
    dense = ModelFactory(architecture="dense", total_params_b=32.5, active_params_b=32.5)
    dense.full_clean()
    bad = ModelFactory(architecture="dense", total_params_b=32.5, active_params_b=10)
    with pytest.raises(Exception):
        bad.full_clean()


@pytest.mark.django_db
def test_recommended_quant_required():
    q = QuantizationFactory()
    m = ModelFactory(recommended_quant=q)
    assert m.recommended_quant_id == q.id
```

**GREEN.** Append to `catalog/models.py`:

```python
VALID_TP_SIZES = {1, 2, 4, 8}


class Model(models.Model):
    ARCH_CHOICES = [("dense", "Dense"), ("moe", "Mixture of Experts")]

    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    family = models.CharField(max_length=64)
    architecture = models.CharField(max_length=16, choices=ARCH_CHOICES)
    total_params_b = models.FloatField()
    active_params_b = models.FloatField()
    num_layers = models.PositiveIntegerField()
    num_attention_heads = models.PositiveIntegerField()
    num_kv_heads = models.PositiveIntegerField()
    head_dim = models.PositiveIntegerField()
    max_context = models.PositiveIntegerField()
    hf_repo = models.CharField(max_length=128)
    license = models.CharField(max_length=64)
    is_coding_specialist = models.BooleanField(default=False)
    recommended_quant = models.ForeignKey(
        Quantization, on_delete=models.PROTECT, related_name="+"
    )
    recommended_tp = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("family", "slug")

    def __str__(self) -> str:
        return self.display_name

    def clean(self) -> None:
        if self.recommended_tp not in VALID_TP_SIZES:
            raise ValidationError({"recommended_tp": f"must be one of {VALID_TP_SIZES}"})
        if self.architecture == "dense" and self.total_params_b != self.active_params_b:
            raise ValidationError(
                {"active_params_b": "dense models must have active_params_b == total_params_b"}
            )
```

Migrate. Run tests → 5 passing. Run factory tests from T01 → those should pass now too.

**REFACTOR.** Sanity-check that `Meta.ordering` is on all three models.

---

### M01.T05 — YAML schemas via pydantic

These are the contract between hand-curated YAML and the DB. Stop bugs early.

**RED.** `catalog/tests/test_yaml_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from catalog.services.seed import GPUYAML, ModelYAML, QuantizationYAML


def test_gpu_yaml_rejects_invalid_vendor():
    with pytest.raises(ValidationError):
        GPUYAML(
            slug="x", display_name="X", vendor="intel", architecture="x",
            vram_gb=80, memory_bandwidth_gbs=3000, fp16_tflops=900, tdp_watts=700,
            interconnect="pcie",
        )


def test_gpu_yaml_accepts_valid_payload():
    gpu = GPUYAML(
        slug="nvidia-h100-sxm-80", display_name="NVIDIA H100 SXM5 80GB",
        vendor="nvidia", architecture="hopper", vram_gb=80,
        memory_bandwidth_gbs=3350, fp16_tflops=989, tdp_watts=700,
        interconnect="nvlink", supports_fp8_native=True,
    )
    assert gpu.slug == "nvidia-h100-sxm-80"


def test_model_yaml_rejects_dense_with_mismatched_params():
    with pytest.raises(ValidationError):
        ModelYAML(
            slug="m", display_name="M", family="x", architecture="dense",
            total_params_b=32.5, active_params_b=10,
            num_layers=64, num_attention_heads=40, num_kv_heads=8, head_dim=128,
            max_context=32768, hf_repo="x/x", license="apache-2.0",
            is_coding_specialist=True, recommended_quant="fp16", recommended_tp=1,
        )


def test_quantization_yaml_rejects_invalid_bits():
    with pytest.raises(ValidationError):
        QuantizationYAML(slug="x", display_name="X", weight_bits=7, kv_cache_bits=16)
```

**GREEN.** `catalog/services/__init__.py` empty; `catalog/services/seed.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


VALID_QUANT_BITS = {4, 8, 16}
VALID_TP_SIZES = {1, 2, 4, 8}


class QuantizationYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    weight_bits: float
    kv_cache_bits: float
    requires_nvidia_arch: list[str] = Field(default_factory=list)
    requires_amd_arch: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("weight_bits", "kv_cache_bits")
    @classmethod
    def _bits_must_be_valid(cls, v: float) -> float:
        if v not in VALID_QUANT_BITS:
            raise ValueError(f"bits must be one of {VALID_QUANT_BITS}")
        return v


class GPUYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    vendor: str
    architecture: str
    vram_gb: int
    memory_bandwidth_gbs: int
    fp16_tflops: float
    fp8_tflops: float | None = None
    int8_tops: float | None = None
    tdp_watts: int
    interconnect: str
    nvlink_bandwidth_gbs: int | None = None
    supports_fp8_native: bool = False
    supports_fp4_native: bool = False

    @field_validator("vendor")
    @classmethod
    def _vendor_valid(cls, v: str) -> str:
        if v not in {"nvidia", "amd"}:
            raise ValueError("vendor must be 'nvidia' or 'amd'")
        return v


class ModelYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    display_name: str
    family: str
    architecture: str
    total_params_b: float
    active_params_b: float
    num_layers: int
    num_attention_heads: int
    num_kv_heads: int
    head_dim: int
    max_context: int
    hf_repo: str
    license: str
    is_coding_specialist: bool
    recommended_quant: str    # slug reference
    recommended_tp: int

    @field_validator("architecture")
    @classmethod
    def _arch_valid(cls, v: str) -> str:
        if v not in {"dense", "moe"}:
            raise ValueError("architecture must be 'dense' or 'moe'")
        return v

    @field_validator("recommended_tp")
    @classmethod
    def _tp_valid(cls, v: int) -> int:
        if v not in VALID_TP_SIZES:
            raise ValueError(f"recommended_tp must be one of {VALID_TP_SIZES}")
        return v

    @model_validator(mode="after")
    def _dense_total_equals_active(self) -> ModelYAML:
        if self.architecture == "dense" and self.total_params_b != self.active_params_b:
            raise ValueError("dense models must have total_params_b == active_params_b")
        return self
```

Run tests → 4 passing.

**REFACTOR.** The duplication of `VALID_QUANT_BITS` and `VALID_TP_SIZES` between `models.py` and `services/seed.py` is acceptable for now (they're constraints, not behavior). Note in a comment.

---

### M01.T06 — `seed_catalog` management command

The command reads YAML files from `seeds/` and upserts them into the DB. Idempotent.

**RED.** `catalog/tests/test_seed_catalog_command.py`:

```python
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
    # mutate YAML, re-run, expect new value picked up
    (tmp_seeds_dir / "gpus.yaml").write_text(
        (tmp_seeds_dir / "gpus.yaml").read_text().replace(
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
```

Add the `tmp_seeds_dir` fixture to `catalog/tests/conftest.py`:

```python
import pytest

MINIMAL_GPUS_YAML = """\
- slug: nvidia-h100-sxm-80
  display_name: NVIDIA H100 SXM5 80GB
  vendor: nvidia
  architecture: hopper
  vram_gb: 80
  memory_bandwidth_gbs: 3350
  fp16_tflops: 989
  fp8_tflops: 1979
  tdp_watts: 700
  interconnect: nvlink
  nvlink_bandwidth_gbs: 900
  supports_fp8_native: true
"""

MINIMAL_QUANTS_YAML = """\
- slug: fp16
  display_name: FP16
  weight_bits: 16
  kv_cache_bits: 16
- slug: fp8-e4m3
  display_name: FP8 (E4M3)
  weight_bits: 8
  kv_cache_bits: 8
  requires_nvidia_arch: [hopper, ada, blackwell]
"""

MINIMAL_MODELS_YAML = """\
- slug: qwen-2-5-coder-32b
  display_name: Qwen2.5-Coder-32B-Instruct
  family: qwen
  architecture: dense
  total_params_b: 32.5
  active_params_b: 32.5
  num_layers: 64
  num_attention_heads: 40
  num_kv_heads: 8
  head_dim: 128
  max_context: 131072
  hf_repo: Qwen/Qwen2.5-Coder-32B-Instruct
  license: apache-2.0
  is_coding_specialist: true
  recommended_quant: fp8-e4m3
  recommended_tp: 1
"""


@pytest.fixture
def tmp_seeds_dir(tmp_path):
    (tmp_path / "gpus.yaml").write_text(MINIMAL_GPUS_YAML)
    (tmp_path / "quantizations.yaml").write_text(MINIMAL_QUANTS_YAML)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "qwen.yaml").write_text(MINIMAL_MODELS_YAML)
    return tmp_path
```

**GREEN.** `catalog/management/__init__.py` (empty), `catalog/management/commands/__init__.py` (empty), `catalog/management/commands/seed_catalog.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from pydantic import ValidationError

from catalog.models import GPU, Model, Quantization
from catalog.services.seed import GPUYAML, ModelYAML, QuantizationYAML


class Command(BaseCommand):
    help = "Upsert catalog reference data from YAML seeds. Idempotent."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--seeds-dir", default="seeds", type=Path)

    def handle(self, *args, **options) -> None:
        seeds_dir: Path = options["seeds_dir"]
        if not seeds_dir.is_dir():
            raise CommandError(f"seeds dir not found: {seeds_dir}")

        try:
            with transaction.atomic():
                self._load_quantizations(seeds_dir / "quantizations.yaml")
                self._load_gpus(seeds_dir / "gpus.yaml")
                self._load_models(seeds_dir / "models")
        except ValidationError as e:
            raise CommandError(f"YAML schema validation failed: {e}") from e

    def _load_quantizations(self, path: Path) -> None:
        if not path.exists():
            return
        for raw in yaml.safe_load(path.read_text()) or []:
            payload = QuantizationYAML(**raw)
            Quantization.objects.update_or_create(
                slug=payload.slug,
                defaults=payload.model_dump(exclude={"slug"}),
            )

    def _load_gpus(self, path: Path) -> None:
        if not path.exists():
            return
        for raw in yaml.safe_load(path.read_text()) or []:
            payload = GPUYAML(**raw)
            GPU.objects.update_or_create(
                slug=payload.slug,
                defaults=payload.model_dump(exclude={"slug"}),
            )

    def _load_models(self, dir_path: Path) -> None:
        if not dir_path.is_dir():
            return
        for yaml_file in sorted(dir_path.glob("*.yaml")):
            for raw in yaml.safe_load(yaml_file.read_text()) or []:
                payload = ModelYAML(**raw)
                quant = Quantization.objects.get(slug=payload.recommended_quant)
                defaults = payload.model_dump(exclude={"slug", "recommended_quant"})
                defaults["recommended_quant"] = quant
                Model.objects.update_or_create(
                    slug=payload.slug,
                    defaults=defaults,
                )
```

Run tests → 4 passing.

**REFACTOR.** The three `_load_*` methods follow the same shape. Acceptable duplication for clarity. Consider DRY in M02 when benchmark loader joins.

---

### M01.T07 — `validate_catalog` management command (read-only)

For CI: schema-validate YAML without touching the DB. Catches errors before migrations.

**RED.** `catalog/tests/test_validate_catalog_command.py`:

```python
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_validate_catalog_passes_on_good_yaml(tmp_seeds_dir):
    call_command("validate_catalog", "--seeds-dir", str(tmp_seeds_dir))


def test_validate_catalog_rejects_bad_yaml(tmp_seeds_dir):
    (tmp_seeds_dir / "quantizations.yaml").write_text("- slug: bad\n  weight_bits: 7\n  kv_cache_bits: 16\n  display_name: X\n")
    with pytest.raises(CommandError):
        call_command("validate_catalog", "--seeds-dir", str(tmp_seeds_dir))
```

**GREEN.** `catalog/management/commands/validate_catalog.py`:

```python
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand, CommandError
from pydantic import ValidationError

from catalog.services.seed import GPUYAML, ModelYAML, QuantizationYAML


class Command(BaseCommand):
    help = "Schema-validate all catalog YAML files without touching the DB."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--seeds-dir", default="seeds", type=Path)

    def handle(self, *args, **options) -> None:
        seeds_dir: Path = options["seeds_dir"]
        errors: list[str] = []

        for path, schema in [
            (seeds_dir / "quantizations.yaml", QuantizationYAML),
            (seeds_dir / "gpus.yaml", GPUYAML),
        ]:
            if not path.exists():
                continue
            for i, raw in enumerate(yaml.safe_load(path.read_text()) or []):
                try:
                    schema(**raw)
                except ValidationError as e:
                    errors.append(f"{path}[{i}]: {e}")

        models_dir = seeds_dir / "models"
        if models_dir.is_dir():
            for yaml_file in sorted(models_dir.glob("*.yaml")):
                for i, raw in enumerate(yaml.safe_load(yaml_file.read_text()) or []):
                    try:
                        ModelYAML(**raw)
                    except ValidationError as e:
                        errors.append(f"{yaml_file}[{i}]: {e}")

        if errors:
            raise CommandError("Validation failed:\n" + "\n".join(errors))
        self.stdout.write(self.style.SUCCESS("All catalog YAML files valid."))
```

Run tests → 2 passing.

**REFACTOR.** Both commands now have parallel loading logic. Note for M02 — once we add benchmarks, factor shared loading into `catalog/services/seed.py`.

---

### M01.T08 — Real seed YAML for 12 GPUs / 5 models / 8 quants

Now populate `seeds/` with real data. This is curation work, not code, but it gets tested by `seed_catalog`.

Files:
- `seeds/quantizations.yaml` — 8 entries: fp16, bf16, fp8-e4m3, fp8-e5m2, int8-awq, int4-awq, int4-gptq, fp4-nvfp4.
- `seeds/gpus.yaml` — 12 entries: nvidia-h100-sxm-80, nvidia-h100-pcie-80, nvidia-h200, nvidia-a100-sxm-80, nvidia-a100-sxm-40, nvidia-l40s, nvidia-l4, nvidia-rtx-4090, nvidia-rtx-6000-ada, nvidia-b200, amd-mi300x, amd-mi250x. Use NVIDIA spec sheets and AMD product pages for ground-truth values.
- `seeds/models/deepseek.yaml`, `seeds/models/qwen.yaml`, `seeds/models/llama.yaml`, `seeds/models/codestral.yaml`, `seeds/models/kimi.yaml` — 5 models total across these files. Use HuggingFace `config.json` for `num_layers`, `num_kv_heads`, `head_dim`. **Verify each model's config.json on HF before committing — these values matter for fit math in M02.**

After files are in place, run `python manage.py seed_catalog` and confirm:

```
GPU.objects.count() == 12
Model.objects.count() == 5
Quantization.objects.count() == 8
```

**Verification.** New integration test:

```python
@pytest.mark.django_db
def test_real_seeds_load():
    """Smoke test against the actual seeds/ directory."""
    from django.core.management import call_command
    call_command("seed_catalog")    # default --seeds-dir=seeds
    from catalog.models import GPU, Model, Quantization
    assert GPU.objects.count() == 12
    assert Model.objects.count() == 5
    assert Quantization.objects.count() == 8
```

Mark this test `@pytest.mark.smoke` so it can be excluded from fast unit runs but included in CI.

---

### M01.T09 — Django admin registration

Read-only admin for catalog tables (per ADR-006 / convention).

**RED.** `catalog/tests/test_admin.py`:

```python
import pytest
from django.contrib.admin.sites import site

from catalog.models import GPU, Model, Quantization


@pytest.mark.django_db
def test_catalog_models_registered_in_admin():
    assert site.is_registered(GPU)
    assert site.is_registered(Model)
    assert site.is_registered(Quantization)
```

**GREEN.** `catalog/admin.py`:

```python
from django.contrib import admin

from catalog.models import GPU, Model, Quantization


class _ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(GPU)
class GPUAdmin(_ReadOnlyAdmin):
    list_display = ("slug", "display_name", "vendor", "vram_gb", "is_active")
    search_fields = ("slug", "display_name")
    list_filter = ("vendor", "architecture")


@admin.register(Model)
class ModelAdmin(_ReadOnlyAdmin):
    list_display = ("slug", "display_name", "family", "architecture", "total_params_b")
    search_fields = ("slug", "display_name")
    list_filter = ("family", "architecture", "is_coding_specialist")


@admin.register(Quantization)
class QuantizationAdmin(_ReadOnlyAdmin):
    list_display = ("slug", "display_name", "weight_bits", "kv_cache_bits")
```

Run tests → 1 passing.

**REFACTOR.** Confirm read-only behavior actually works manually: `python manage.py createsuperuser`, log in to `/admin/`, confirm "Add" buttons are absent on catalog pages.

---

### M01.T10 — Invariants test file

Capture invariants I3, I7, I8 from `SHARED.md` as enforced tests. I1, I2, I4–I6 wait for later milestones.

**RED.** `tests/test_invariants.py` (at repo root, not in an app):

```python
import pytest
from django.core.management import call_command

from catalog.models import Model, Quantization


@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i3_recommended_quant_always_exists():
    call_command("seed_catalog")
    for model in Model.objects.all():
        assert model.recommended_quant_id is not None
        Quantization.objects.get(pk=model.recommended_quant_id)


@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i8_seeds_idempotent():
    call_command("seed_catalog")
    counts_a = {
        "gpus": GPU.objects.count(),
        "models": Model.objects.count(),
        "quants": Quantization.objects.count(),
    }
    call_command("seed_catalog")
    counts_b = {
        "gpus": GPU.objects.count(),
        "models": Model.objects.count(),
        "quants": Quantization.objects.count(),
    }
    assert counts_a == counts_b
```

Tests pass after M01.T08 has placed real seeds.

---

## Milestone verification

```bash
# clean DB
docker compose exec db psql -U postgres -c "DROP DATABASE IF EXISTS pricing; CREATE DATABASE pricing;"
python manage.py migrate
python manage.py seed_catalog                                  # exits 0
python manage.py seed_catalog                                  # exits 0, zero changes
python manage.py validate_catalog                              # exits 0
python manage.py shell -c "from catalog.models import *; print(GPU.objects.count(), Model.objects.count(), Quantization.objects.count())"
# expect: 12 5 8

pytest catalog/ tests/ -q                                      # all pass, ~20 tests
ruff check && ruff format --check
mypy catalog
python manage.py makemigrations --check                        # no pending migrations
```

Mark M01 done in `spec/INDEX.md`. Stop.

---

## Out of scope for M01

- Benchmarks. M02.
- Fit calculation. M02 needs the model fields M01 provided.
- Any `pricing` app concerns. M03+.
- Reserved-cloud or on-prem models. M08/M09.
