# M02 — Benchmarks + Fit Calculation

**Goal.** Land `BenchmarkSource` and `BenchmarkPoint` models, the pure-function VRAM fit calculator in `catalog/services/fit.py`, and the benchmark YAML loader that fans one source file into multiple `BenchmarkPoint` rows on the operating grid. The seeder rejects rows that fail fit (Invariant I2).

**Depends on.** M01 (uses GPU, Model, Quantization).

**Definition of done.** Real benchmark YAML loads cleanly; rows that fail the fit check are dropped with logged warnings; `compute_fit()` math agrees with hand-computed reference cases for Qwen2.5-Coder-32B and Llama 70B; ~30 tests passing.

---

## Background reading

Re-read `docs/PRD.md` §7.1 (VRAM fit calc) and §6.5 (BenchmarkPoint schema) before starting. The math is small but easy to get subtly wrong — especially around MoE total-vs-active params and TP sharding semantics.

---

## Tasks

### M02.T01 — `BenchmarkSource` model

Tiny model, sets up citations.

**RED.** `catalog/tests/test_benchmark_source.py`:

```python
import datetime
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
```

Add `BenchmarkSourceFactory` to `catalog/tests/factories.py`:

```python
class BenchmarkSourceFactory(DjangoModelFactory):
    class Meta:
        model = BenchmarkSource

    slug = factory.Sequence(lambda n: f"src-{n}")
    title = "Test benchmark"
    url = "https://example.com/bench"
    publisher = "vllm"
    published_at = factory.LazyFunction(datetime.date.today)
    engine = "vllm"
    engine_version = "0.7.0"
```

**GREEN.** Append to `catalog/models.py`:

```python
class BenchmarkSource(models.Model):
    PUBLISHER_CHOICES = [
        ("vllm", "vLLM"), ("sglang", "SGLang"), ("nvidia", "NVIDIA"),
        ("anyscale", "Anyscale"), ("mlperf", "MLPerf"), ("other", "Other"),
    ]
    ENGINE_CHOICES = [
        ("vllm", "vLLM"), ("sglang", "SGLang"),
        ("tgi", "TGI"), ("trt-llm", "TensorRT-LLM"),
    ]

    slug = models.SlugField(unique=True, max_length=128)
    title = models.CharField(max_length=256)
    url = models.URLField()
    publisher = models.CharField(max_length=32, choices=PUBLISHER_CHOICES)
    published_at = models.DateField()
    engine = models.CharField(max_length=16, choices=ENGINE_CHOICES)
    engine_version = models.CharField(max_length=32)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-published_at",)

    def __str__(self) -> str:
        return self.title
```

Migrate, test → 2 passing.

---

### M02.T02 — Fit calculation: pure function with hand-verified cases

This is the load-bearing math. Test against worked examples from PRD Appendix A and a couple new ones.

**RED.** `catalog/tests/test_fit.py`:

```python
from decimal import Decimal

import pytest

from catalog.services.fit import (
    compute_fit,
    kv_cache_bytes_per_token,
    per_gpu_total_bytes,
    weight_bytes,
)


def _qwen_25_coder_32b():
    """Reference architecture: 32.5B params, 64 layers, 8 KV heads, head_dim=128, dense."""
    return dict(
        total_params_b=32.5,
        num_layers=64,
        num_kv_heads=8,
        head_dim=128,
        architecture="dense",
    )


def _llama_70b():
    return dict(
        total_params_b=70,
        num_layers=80,
        num_kv_heads=8,
        head_dim=128,
        architecture="dense",
    )


def _deepseek_v3_moe():
    """671B total, 37B active, but weight memory uses TOTAL (all experts loaded)."""
    return dict(
        total_params_b=671,
        num_layers=61,
        num_kv_heads=128,
        head_dim=128,
        architecture="moe",
    )


def test_weight_bytes_qwen32b_fp16_is_65gb():
    """32.5B params × 2 bytes = 65 GB."""
    assert weight_bytes(total_params_b=32.5, weight_bits=16) == 65 * 10**9


def test_weight_bytes_qwen32b_fp8_is_32_5gb():
    assert weight_bytes(total_params_b=32.5, weight_bits=8) == int(32.5 * 10**9)


def test_weight_bytes_moe_uses_total_not_active():
    """DeepSeek-V3 weight memory uses 671B (total), not 37B (active)."""
    fp16 = weight_bytes(total_params_b=671, weight_bits=16)
    assert fp16 == 1342 * 10**9


def test_kv_cache_bytes_qwen32b_fp16():
    """2 (K and V) × 64 layers × 8 kv_heads × 128 head_dim × 2 bytes = 262144 bytes/token."""
    kv = kv_cache_bytes_per_token(num_layers=64, num_kv_heads=8, head_dim=128, kv_cache_bits=16)
    assert kv == 262144


def test_kv_cache_bytes_scales_with_kv_quant():
    fp16 = kv_cache_bytes_per_token(64, 8, 128, kv_cache_bits=16)
    fp8 = kv_cache_bytes_per_token(64, 8, 128, kv_cache_bits=8)
    int4 = kv_cache_bytes_per_token(64, 8, 128, kv_cache_bits=4)
    assert fp8 == fp16 // 2
    assert int4 == fp16 // 4


def test_qwen32b_fp8_fits_on_single_h100_at_batch_8_ctx_32k():
    """Worked example from PRD Appendix A: fits."""
    fits, per_gpu, _ = compute_fit(
        **_qwen_25_coder_32b(),
        weight_bits=8, kv_cache_bits=8,
        tp_size=1, batch_size=8, context_length=32768,
        gpu_vram_gb=80,
    )
    assert fits is True
    assert per_gpu < 80 * 10**9


def test_qwen32b_fp8_does_not_fit_on_single_h100_at_batch_32_ctx_32k():
    """Worked example from PRD Appendix A: 137GB KV cache alone exceeds 80GB VRAM."""
    fits, per_gpu, breakdown = compute_fit(
        **_qwen_25_coder_32b(),
        weight_bits=8, kv_cache_bits=8,
        tp_size=1, batch_size=32, context_length=32768,
        gpu_vram_gb=80,
    )
    assert fits is False
    assert per_gpu > 80 * 10**9


def test_llama70b_fp16_requires_tp_at_least_2_on_h100():
    """70B × 2 bytes = 140 GB; single 80 GB H100 can't hold weights alone."""
    fits_tp1, _, _ = compute_fit(
        **_llama_70b(),
        weight_bits=16, kv_cache_bits=16,
        tp_size=1, batch_size=1, context_length=4096,
        gpu_vram_gb=80,
    )
    fits_tp2, _, _ = compute_fit(
        **_llama_70b(),
        weight_bits=16, kv_cache_bits=16,
        tp_size=2, batch_size=1, context_length=4096,
        gpu_vram_gb=80,
    )
    assert fits_tp1 is False
    assert fits_tp2 is True


def test_deepseek_v3_moe_uses_total_params_for_weight_memory():
    """If we incorrectly used active_params (37B), it would fit too easily.
    With correct total (671B), needs many GPUs."""
    fits_tp8, _, _ = compute_fit(
        **_deepseek_v3_moe(),
        weight_bits=8, kv_cache_bits=8,
        tp_size=8, batch_size=1, context_length=4096,
        gpu_vram_gb=80,
    )
    # 671B × 1 byte / 8 GPUs = 83.875 GB per GPU just for weights — doesn't fit on 80GB H100
    # Either result (fits or not) depends on activations; this test guards against the
    # common bug of using active_params_b for weight memory.
    fits_tp8_active_bug, _, _ = compute_fit(
        total_params_b=37,    # WRONG: using active param count
        num_layers=61, num_kv_heads=128, head_dim=128, architecture="moe",
        weight_bits=8, kv_cache_bits=8,
        tp_size=8, batch_size=1, context_length=4096,
        gpu_vram_gb=80,
    )
    assert fits_tp8 != fits_tp8_active_bug    # the two should differ; if they're equal, weight calc is wrong


@pytest.mark.parametrize("tp_size", [1, 2, 4, 8])
def test_compute_fit_returns_breakdown_dict(tp_size):
    fits, per_gpu, breakdown = compute_fit(
        **_qwen_25_coder_32b(),
        weight_bits=16, kv_cache_bits=16,
        tp_size=tp_size, batch_size=1, context_length=4096,
        gpu_vram_gb=80,
    )
    assert {"weight_bytes", "kv_cache_bytes", "activations_bytes", "per_gpu_bytes"} <= set(breakdown)
```

**GREEN.** `catalog/services/fit.py`:

```python
from __future__ import annotations

from typing import TypedDict


class FitBreakdown(TypedDict):
    weight_bytes: int
    kv_cache_bytes: int
    activations_bytes: int
    per_gpu_bytes: int


_OVERHEAD = 1.15


def weight_bytes(total_params_b: float, weight_bits: float) -> int:
    """Total weight memory across all GPUs, before TP sharding.

    For MoE models, total_params_b is the FULL parameter count (all experts).
    All experts must be loaded to GPU memory; only routing decides which
    experts compute per token. This is a common source of subtle bugs.
    """
    return int(total_params_b * 1e9 * (weight_bits / 8))


def kv_cache_bytes_per_token(
    num_layers: int, num_kv_heads: int, head_dim: int, kv_cache_bits: float
) -> int:
    """Per-token KV cache size: 2 (K and V) × layers × kv_heads × head_dim × bytes/element."""
    return int(2 * num_layers * num_kv_heads * head_dim * (kv_cache_bits / 8))


def _activations_bytes(batch_size: int) -> int:
    """Rough activations estimate. Production-realistic; not exact per vLLM allocator."""
    return int((1.0 + 0.05 * batch_size) * 1e9)


def per_gpu_total_bytes(
    *,
    total_params_b: float,
    num_layers: int,
    num_kv_heads: int,
    head_dim: int,
    weight_bits: float,
    kv_cache_bits: float,
    tp_size: int,
    batch_size: int,
    context_length: int,
) -> tuple[int, FitBreakdown]:
    """Compute per-GPU memory after TP sharding. Returns (bytes, breakdown)."""
    wb = weight_bytes(total_params_b, weight_bits)
    kv_per_token = kv_cache_bytes_per_token(num_layers, num_kv_heads, head_dim, kv_cache_bits)
    kv_total = kv_per_token * batch_size * context_length
    acts = _activations_bytes(batch_size)

    # Weights and KV cache shard across TP ranks; activations don't (per-GPU local).
    per_gpu = int(((wb + kv_total) / tp_size + acts) * _OVERHEAD)

    return per_gpu, {
        "weight_bytes": wb,
        "kv_cache_bytes": kv_total,
        "activations_bytes": acts,
        "per_gpu_bytes": per_gpu,
    }


def compute_fit(
    *,
    total_params_b: float,
    num_layers: int,
    num_kv_heads: int,
    head_dim: int,
    architecture: str,    # informational; doesn't affect calc — see weight_bytes docstring
    weight_bits: float,
    kv_cache_bits: float,
    tp_size: int,
    batch_size: int,
    context_length: int,
    gpu_vram_gb: int,
) -> tuple[bool, int, FitBreakdown]:
    """Returns (fits, per_gpu_bytes, breakdown)."""
    per_gpu, breakdown = per_gpu_total_bytes(
        total_params_b=total_params_b,
        num_layers=num_layers,
        num_kv_heads=num_kv_heads,
        head_dim=head_dim,
        weight_bits=weight_bits,
        kv_cache_bits=kv_cache_bits,
        tp_size=tp_size,
        batch_size=batch_size,
        context_length=context_length,
    )
    fits = per_gpu <= gpu_vram_gb * 10**9
    return fits, per_gpu, breakdown
```

Run tests → all passing. If `test_qwen32b_fp8_fits_on_single_h100_at_batch_8_ctx_32k` fails, the math is wrong — re-read PRD §7.1 and the worked example in Appendix A.

**REFACTOR.** Consider whether the function signature is too long. It is, but each parameter is meaningful; keyword-only enforced. Acceptable.

---

### M02.T03 — `BenchmarkPoint` model + unique-together constraint

**RED.** `catalog/tests/test_benchmark_point.py`:

```python
import pytest
from django.db.utils import IntegrityError

from catalog.tests.factories import BenchmarkPointFactory


@pytest.mark.django_db
def test_benchmark_point_unique_compat_tuple_and_op_point():
    """Invariant I6: no two BenchmarkPoint rows share the same
    (model, gpu, quantization, tp_size, batch_size, context_length)."""
    bp = BenchmarkPointFactory()
    with pytest.raises(IntegrityError):
        BenchmarkPointFactory(
            model=bp.model, gpu=bp.gpu, quantization=bp.quantization,
            tp_size=bp.tp_size, batch_size=bp.batch_size, context_length=bp.context_length,
        )


@pytest.mark.django_db
def test_benchmark_point_throughputs_positive():
    bp = BenchmarkPointFactory(prefill_tps_aggregate=1000, decode_tps_aggregate=100)
    assert bp.prefill_tps_aggregate > 0
    assert bp.decode_tps_aggregate > 0
```

Add `BenchmarkPointFactory` to factories.

**GREEN.** Append to `catalog/models.py`:

```python
class BenchmarkPoint(models.Model):
    model = models.ForeignKey(Model, on_delete=models.PROTECT)
    gpu = models.ForeignKey(GPU, on_delete=models.PROTECT)
    quantization = models.ForeignKey(Quantization, on_delete=models.PROTECT)
    tp_size = models.PositiveSmallIntegerField()
    batch_size = models.PositiveSmallIntegerField()
    context_length = models.PositiveIntegerField()

    prefill_tps_aggregate = models.FloatField(help_text="Input tokens/sec across the batch")
    decode_tps_aggregate = models.FloatField(help_text="Output tokens/sec across the batch")
    ttft_ms = models.FloatField(null=True, blank=True)

    source = models.ForeignKey(BenchmarkSource, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("model", "gpu", "quantization", "tp_size", "batch_size", "context_length")
        constraints = [
            models.UniqueConstraint(
                fields=["model", "gpu", "quantization", "tp_size",
                        "batch_size", "context_length"],
                name="unique_benchmark_point",
            )
        ]
        indexes = [
            models.Index(fields=["model", "gpu"]),
            models.Index(fields=["gpu", "quantization"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.model.slug} on {self.gpu.slug} @ {self.quantization.slug} "
            f"tp{self.tp_size} batch{self.batch_size} ctx{self.context_length}"
        )
```

Migrate, test → 2 passing.

---

### M02.T04 — Benchmark YAML schema

**RED.** `catalog/tests/test_benchmark_yaml.py`:

```python
import pytest
from pydantic import ValidationError

from catalog.services.seed import BenchmarkSourceYAML, BenchmarkPointYAML, BenchmarkFileYAML


def test_benchmark_point_rejects_invalid_tp_size():
    with pytest.raises(ValidationError):
        BenchmarkPointYAML(
            model="m", gpu="g", quantization="q",
            tp_size=3, batch_size=8, context_length=4096,
            prefill_tps_aggregate=1000, decode_tps_aggregate=100,
        )


def test_benchmark_point_rejects_negative_throughput():
    with pytest.raises(ValidationError):
        BenchmarkPointYAML(
            model="m", gpu="g", quantization="q",
            tp_size=1, batch_size=8, context_length=4096,
            prefill_tps_aggregate=-1, decode_tps_aggregate=100,
        )


def test_benchmark_file_yaml_groups_points_under_source():
    payload = {
        "source": {
            "slug": "x", "title": "X", "url": "https://e.com/x",
            "publisher": "vllm", "published_at": "2025-03-12",
            "engine": "vllm", "engine_version": "0.7.0",
        },
        "points": [
            {
                "model": "m", "gpu": "g", "quantization": "q",
                "tp_size": 1, "batch_size": 8, "context_length": 4096,
                "prefill_tps_aggregate": 1000, "decode_tps_aggregate": 100,
            }
        ],
    }
    parsed = BenchmarkFileYAML(**payload)
    assert len(parsed.points) == 1
    assert parsed.source.slug == "x"
```

**GREEN.** Append to `catalog/services/seed.py`:

```python
import datetime as _dt


class BenchmarkSourceYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    title: str
    url: str
    publisher: str
    published_at: _dt.date
    engine: str
    engine_version: str
    notes: str = ""


class BenchmarkPointYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    gpu: str
    quantization: str
    tp_size: int
    batch_size: int
    context_length: int
    prefill_tps_aggregate: float
    decode_tps_aggregate: float
    ttft_ms: float | None = None

    @field_validator("tp_size")
    @classmethod
    def _tp_valid(cls, v: int) -> int:
        if v not in VALID_TP_SIZES:
            raise ValueError(f"tp_size must be one of {VALID_TP_SIZES}")
        return v

    @field_validator("prefill_tps_aggregate", "decode_tps_aggregate")
    @classmethod
    def _tps_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("throughput must be positive")
        return v


class BenchmarkFileYAML(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: BenchmarkSourceYAML
    points: list[BenchmarkPointYAML]
```

Test → 3 passing.

---

### M02.T05 — Benchmark loader with fit-check rejection

The loader walks `seeds/benchmarks/*.yaml`, runs each row through `compute_fit`, and skips rows that don't fit. Logs warnings for skipped rows.

**RED.** `catalog/tests/test_benchmark_loader.py`:

```python
import logging
import pytest
from django.core.management import call_command

from catalog.models import BenchmarkPoint


@pytest.fixture
def tmp_seeds_with_benchmarks(tmp_seeds_dir):
    """Extends tmp_seeds_dir with one benchmark file containing one fitting row + one infeasible row."""
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
    # exactly one row should land — the fitting one
    assert BenchmarkPoint.objects.count() == 1
    bp = BenchmarkPoint.objects.get()
    assert bp.batch_size == 8


@pytest.mark.django_db
def test_benchmark_loader_logs_warning_for_infeasible_rows(
    tmp_seeds_with_benchmarks, caplog
):
    with caplog.at_level(logging.WARNING, logger="catalog.seed"):
        call_command("seed_catalog", "--seeds-dir", str(tmp_seeds_with_benchmarks))
    skipped = [r for r in caplog.records if "fit check failed" in r.message]
    assert len(skipped) == 1
    assert "batch_size=32" in skipped[0].message
```

**GREEN.** Extend `catalog/management/commands/seed_catalog.py` with benchmark loading after models load. Pseudo-code:

```python
import logging
from catalog.services.fit import compute_fit
from catalog.services.seed import BenchmarkFileYAML

logger = logging.getLogger("catalog.seed")


def _load_benchmarks(self, dir_path: Path) -> None:
    if not dir_path.is_dir():
        return
    for yaml_file in sorted(dir_path.glob("*.yaml")):
        payload = BenchmarkFileYAML(**yaml.safe_load(yaml_file.read_text()))
        source, _ = BenchmarkSource.objects.update_or_create(
            slug=payload.source.slug,
            defaults=payload.source.model_dump(exclude={"slug"}),
        )
        for point in payload.points:
            model = Model.objects.get(slug=point.model)
            gpu = GPU.objects.get(slug=point.gpu)
            quant = Quantization.objects.get(slug=point.quantization)
            fits, _, _ = compute_fit(
                total_params_b=model.total_params_b,
                num_layers=model.num_layers,
                num_kv_heads=model.num_kv_heads,
                head_dim=model.head_dim,
                architecture=model.architecture,
                weight_bits=quant.weight_bits,
                kv_cache_bits=quant.kv_cache_bits,
                tp_size=point.tp_size,
                batch_size=point.batch_size,
                context_length=point.context_length,
                gpu_vram_gb=gpu.vram_gb,
            )
            if not fits:
                logger.warning(
                    "fit check failed; skipping benchmark point "
                    "model=%s gpu=%s quant=%s tp_size=%d batch_size=%d context_length=%d",
                    model.slug, gpu.slug, quant.slug,
                    point.tp_size, point.batch_size, point.context_length,
                )
                continue
            BenchmarkPoint.objects.update_or_create(
                model=model, gpu=gpu, quantization=quant,
                tp_size=point.tp_size,
                batch_size=point.batch_size,
                context_length=point.context_length,
                defaults={
                    "prefill_tps_aggregate": point.prefill_tps_aggregate,
                    "decode_tps_aggregate": point.decode_tps_aggregate,
                    "ttft_ms": point.ttft_ms,
                    "source": source,
                },
            )

# call self._load_benchmarks(seeds_dir / "benchmarks") inside handle(), inside the transaction
```

Test → 2 passing.

**REFACTOR.** Loader is getting long. Extract the fit-check + upsert into `_persist_benchmark_point()`. Keep the loop body to ~5 lines.

---

### M02.T06 — Real benchmark seeds for at least one source

Curate `seeds/benchmarks/vllm-blog-2025-03-h100-fp8.yaml` with real-ish numbers for Qwen2.5-Coder-32B on H100 at FP8. Cover at least 6 of the 12 op-points (batch=1,8,32 × ctx=4k,32k — the higher-batch / higher-ctx combos likely fail fit and that's OK).

Numbers can be sourced from vLLM's public benchmark posts; cite the URL in `source.url`. **Be honest about which numbers are placeholders** by including `notes:` text noting where verification is still TODO.

After committing, run `python manage.py seed_catalog` and confirm:
```
BenchmarkPoint.objects.count() >= 4
```

---

### M02.T07 — Invariant I2 enforcement test

```python
# tests/test_invariants.py — append

@pytest.mark.django_db
@pytest.mark.smoke
def test_invariant_i2_every_benchmark_point_fits():
    call_command("seed_catalog")
    from catalog.services.fit import compute_fit
    for bp in BenchmarkPoint.objects.select_related("model", "gpu", "quantization"):
        fits, _, _ = compute_fit(
            total_params_b=bp.model.total_params_b,
            num_layers=bp.model.num_layers,
            num_kv_heads=bp.model.num_kv_heads,
            head_dim=bp.model.head_dim,
            architecture=bp.model.architecture,
            weight_bits=bp.quantization.weight_bits,
            kv_cache_bits=bp.quantization.kv_cache_bits,
            tp_size=bp.tp_size,
            batch_size=bp.batch_size,
            context_length=bp.context_length,
            gpu_vram_gb=bp.gpu.vram_gb,
        )
        assert fits, f"benchmark {bp} fails fit check"
```

---

### M02.T08 — Admin registration

Mirror M01.T09 — register `BenchmarkSource` and `BenchmarkPoint` as read-only admin. Brief test confirms registration.

---

## Milestone verification

```bash
python manage.py migrate
python manage.py seed_catalog
python manage.py seed_catalog                          # idempotent
python manage.py shell -c "
from catalog.models import BenchmarkPoint, BenchmarkSource
print('sources:', BenchmarkSource.objects.count())
print('points:', BenchmarkPoint.objects.count())
"
# expect at least 1 source and 4 points

pytest catalog/ tests/ -q                              # ~50 tests total now
ruff check && ruff format --check
mypy catalog
python manage.py makemigrations --check
```

Mark M02 done. Stop.

---

## Out of scope for M02

- Pricing app and any provider/snapshot logic. M03+.
- Cost calculation. M06 (needs both benchmarks and pricing).
- Closed-API pricing. Phase 2.
- Per-token model fit (KV per token only modeled in vLLM style; we ignore other inference engines' specific layouts).
