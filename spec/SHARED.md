# spec/SHARED.md — Shared language, invariants, conventions

Read every session. The goal is one ubiquitous language between you, the codebase, the PRD, and future engineers reading commits. If a concept here has a name, **use that exact name** in code (variables, classes, files), in commits, and in docs.

---

## Domain language

| Term | Meaning | Code uses |
|---|---|---|
| **Operating point** | A `(batch_size, context_length)` pair. Twelve in our grid: `{1, 8, 32, 64} × {4k, 32k, 128k}`. | `op_point`, `(batch_size, context_length)` |
| **Compatibility tuple** | A `(model, GPU, quantization, tp_size)` combo. Either fits (passes VRAM check) or doesn't. | `compat_tuple`, `BenchmarkPoint`'s unique-together fields |
| **Cost cell** | A single `$/M token` value at one compatibility tuple × operating point × provider tier. | `cost_cell`, the row of `current_cost_cells` |
| **Fit** | Per-GPU memory (weights + KV + activations) ≤ GPU VRAM, given TP. | `fits()`, `check_fit()` |
| **Tier** | Pricing class within a provider. Cloud: `on_demand`, `community`, `secure`, `interruptible`, `spot`. On-prem: `tco`, `marginal`. Reserved: `reserved-{slug}`, `reserved-marginal-{slug}`. | `tier` (string field) |
| **Snapshot** | One row in `PricingSnapshot` — `(provider, gpu, tier, region, scraped_at) → $/hr`. Both scraped and synthetic rows are snapshots. | `PricingSnapshot` |
| **Aggregate decode TPS** | Output tokens/sec summed across the batch (vLLM convention). Not per-request. | `decode_tps_aggregate` |
| **Recommended op point** | Per-model `(quant, tp)` flagged as production-realistic in YAML. | `recommended_quant`, `recommended_tp` |
| **Hardware SKU** | A buyable server config — e.g. *Supermicro AS-8125GS-TNHR (8× MI300X)*. | `HardwareSKU` |
| **Deployment** | An on-prem or reserved-cloud scenario instance with cost inputs. | `OnPremDeployment`, `ReservedCloudDeployment` |
| **TCO** | Green-field total cost of ownership: `(capex × (1 − salvage) / depreciable_active_hours) + opex_per_active_hour`. | `node_hourly_tco`, `per_gpu_hourly_tco` |
| **Marginal cost** | Incremental opex per active hour with capex sunk. | `node_hourly_marginal`, `per_gpu_hourly_marginal` |
| **PUE** | Power Usage Effectiveness: ratio of total facility power to IT power. Hyperscale ≈ 1.1, enterprise ≈ 1.4–1.6. | `pue` |
| **Utilization** | Fraction of GPU-hours actually serving inference. Denominator for amortization and per-active-hour fixed costs. | `utilization_pct`, `expected_utilization_pct` |
| **Burdened salary** | Annual comp × ~1.3 (benefits, payroll tax, equipment). | `sysadmin_annual_burdened_usd` |
| **Payment cadence** | Reserved-cloud commitment shape: `all_upfront`, `partial_upfront`, `no_upfront`, `capacity_block`. | `payment_cadence` |
| **Minimum utilization floor** | Use-it-or-lose-it threshold for reserved capacity. Lambda Reserved ~0.70. AWS RIs 0.0. Capacity Blocks 1.0. | `minimum_utilization_floor_pct` |
| **Committed rate** | Effective $/active-hour during a reservation, accounting for upfront + recurring + metered. | `node_hourly_committed`, `per_gpu_hourly_committed` |
| **Reservation marginal** | Per-active-hour metered rate only; commitment treated as sunk. | `node_hourly_reservation_marginal` |
| **Data source tier** | `realtime_api` / `scraped_page` / `manual_curation` / `synthetic`. Drives Beat scheduling and UI freshness warnings. | `Provider.data_source_tier` |
| **Implicit discount** | Display-only: `1 − (committed_rate / on_demand_reference_rate)`. Never used in cost math. | `implicit_discount_pct()` |
| **Op-grid fan-out** | Generating up to 12 `BenchmarkPoint` rows per (model, GPU, quant, TP) tuple from a single YAML source entry. | `expand_to_op_grid()` |

---

## Code conventions

### Layout

```
project_root/
├── manage.py
├── pyproject.toml                  # ruff, mypy, pytest config
├── docker-compose.yml              # Postgres+TimescaleDB+Redis for dev
├── conftest.py                     # pytest-django setup
├── pricing_dashboard/              # Django project (settings, urls, wsgi)
│   ├── settings.py
│   ├── urls.py
│   └── celery.py
├── catalog/                        # Django app: static-ish reference data
│   ├── models.py
│   ├── admin.py
│   ├── services/
│   │   ├── fit.py                  # pure functions
│   │   └── seed.py
│   ├── management/commands/
│   │   ├── seed_catalog.py
│   │   └── validate_catalog.py
│   ├── migrations/
│   └── tests/
│       ├── conftest.py
│       ├── test_models.py
│       └── test_fit.py
├── pricing/                        # Django app: fast-moving scraped/synthetic data
│   ├── models.py
│   ├── admin.py
│   ├── services/
│   │   ├── on_prem_cost.py
│   │   ├── reserved_cloud_cost.py
│   │   └── scrape_runner.py
│   ├── scrapers/
│   │   ├── base.py                 # ScrapedPrice pydantic dataclass
│   │   ├── runpod.py
│   │   ├── lambda_labs.py
│   │   ├── vast.py
│   │   ├── nebius.py
│   │   ├── aws.py
│   │   ├── gcp.py
│   │   └── azure.py
│   ├── generators/
│   │   ├── on_prem.py
│   │   └── reserved_cloud.py
│   ├── tasks.py                    # Celery tasks
│   ├── management/commands/
│   ├── migrations/
│   └── tests/
├── seeds/                          # YAML data (PR-reviewed source of truth)
│   ├── gpus.yaml
│   ├── quantizations.yaml
│   ├── models/                     # one file per family
│   ├── hardware/
│   ├── deployments/
│   ├── reserved/
│   │   ├── products/
│   │   └── deployments/
│   └── benchmarks/                 # one file per source
├── docs/
│   ├── PRD.md
│   └── adr/
└── spec/                           # this directory
```

### Naming

- Modules and packages: `snake_case`.
- Classes: `PascalCase`. Django models: singular noun (`GPU`, `BenchmarkPoint`, never `Gpus`).
- Functions: `snake_case`, verb-first (`compute_kv_cache_bytes`, not `kv_cache_bytes_computation`).
- Test functions: `test_<thing>_<expectation>`, e.g. `test_gpu_slug_must_be_unique`, `test_kv_cache_matches_published_qwen_numbers`.
- Pydantic dataclasses for scraper outputs and YAML schemas live next to where they're used, named `<Thing>Schema` for YAML, `Scraped<Thing>` for scraper outputs.

### Models

- Always include `created_at = models.DateTimeField(auto_now_add=True)` and `updated_at = models.DateTimeField(auto_now=True)` on every persistent model except `PricingSnapshot` (which has `scraped_at` instead) and synthetic/aggregate views.
- `Meta.ordering` is mandatory on every model. Default to `("-created_at",)` if no domain order applies.
- Use `db_index=True` for any field appearing in WHERE clauses. Composite indexes via `Meta.indexes`.
- `__str__` returns `self.display_name` if present, else `self.slug`.

### Services

- Business logic lives in `<app>/services/<topic>.py` as **module-level pure functions**, not on model classes.
- Functions take model *instances* (already loaded) and return primitive values or new instances. They never query the DB themselves — caller is responsible for `select_related`/`prefetch_related`.
- Type-annotated inputs and outputs. `Decimal` for money. No `Any`.

### Tests

- One test per behavior. If you find yourself with multiple `assert` statements testing different things, split the test.
- Use `factory_boy` factories from `<app>/tests/factories.py` for setup.
- Parametrize with `@pytest.mark.parametrize` over `for` loops in test bodies.
- Test names describe behavior, not implementation: `test_h100_fits_qwen32b_fp8_at_batch_8_ctx_32k` not `test_fit_function_returns_true_case_3`.
- Hitting the DB requires `@pytest.mark.django_db`. Default to *not* hitting the DB; prefer pure-function tests for service modules.

### Decimal handling

- `from decimal import Decimal` always.
- YAML loader casts numeric strings to `Decimal` explicitly via pydantic field validators.
- Never mix `Decimal` and `float` in a single expression — Python permits it but loses precision.
- Quantize where display matters: `cost.quantize(Decimal("0.0001"))` for $/M tokens, `Decimal("0.01")` for $/hour.

### Pre-commit hooks (set up in `BOOTSTRAP.md`)

```
ruff check
ruff format --check
mypy catalog pricing
pytest -q --no-cov            # quick run, no coverage; full coverage in CI
```

---

## Invariants the codebase must maintain

These are tested in `tests/test_invariants.py` (created in M01 alongside `tests/conftest.py`):

- **I1.** Every `PricingSnapshot.hourly_usd` is `>= 0`.
- **I2.** Every `BenchmarkPoint` passes the fit check at its `(batch_size, context_length)`. Failing rows must not exist; the seeder rejects them.
- **I3.** Every `Model.recommended_quant` matches a `Quantization` row that exists.
- **I4.** Every `Provider.data_source_tier == "synthetic"` corresponds to an `OnPremDeployment` or `ReservedCloudDeployment` via either provider slug or `cloud_provider` FK.
- **I5.** `current_cost_cells` materialized view never has `NULL` in `usd_per_m_input` or `usd_per_m_output`. If either is NULL, the underlying benchmark or pricing row is malformed.
- **I6.** No two `BenchmarkPoint` rows share the same `(model, gpu, quantization, tp_size, batch_size, context_length)`.
- **I7.** `Quantization.weight_bits ∈ {16, 8, 4}`; `kv_cache_bits ∈ {16, 8, 4}`.
- **I8.** YAML seed is idempotent: running `seed_catalog` twice in a row results in zero changes the second time.

If a slice would require violating an invariant, you've misunderstood the slice — re-read and ask.

---

## Standard imports

For consistency across the codebase:

```python
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import models, transaction
from django.utils import timezone
```

Avoid wildcard imports. Group: stdlib, third-party, first-party, with blank lines between groups (ruff enforces this).

---

## What to do when a SKILL.md applies

If creating Word docs, PDFs, slides, spreadsheets, or anything matching a SKILL.md at `/mnt/skills/public/<name>/SKILL.md`, read the skill **before** writing code. The skills encode environment-specific best practices.

For this project specifically, no public skill applies to backend Django work. The PRD itself is your skill.
