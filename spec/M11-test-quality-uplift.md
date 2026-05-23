# M11 — Test quality uplift

**Goal:** Refactor the test files that currently only exercise schema constraints or happy-path
wiring into tests that prove the system behaves correctly under real business conditions. See
`spec/TESTING.md` for the full philosophy this milestone enforces.

**Depends on:** M08 (on-prem merged — establishes the business-scenario test pattern to follow)

---

## Scope

Nine files are below the bar defined in `spec/TESTING.md`. Work them in priority order.

### High priority

| Task | File | Problem |
|---|---|---|
| M11.T01 | `catalog/tests/test_benchmark_point.py` | Constraint-only; no GPU VRAM / fit / TP scenario |
| M11.T02 | `pricing/tests/test_provider.py` | Constraint-only; never tests provider's role in the pipeline |
| M11.T03 | `pricing/tests/test_runpod_mapping.py` | Generic function names; no edge cases or failure modes |
| M11.T04 | `pricing/tests/test_scraper_base.py` | Type coercion tests only; doesn't prove scraper contracts |

### Medium priority

| Task | File | Problem |
|---|---|---|
| M11.T05 | `catalog/tests/test_gpu.py` | No integration scenario (GPU → snapshot → cost cell) |
| M11.T06 | `catalog/tests/test_model.py` | No model → benchmark fit linkage scenario |
| M11.T07 | `catalog/tests/test_seed_catalog_command.py` | Happy path only; no malformed YAML / bad-field rejection |
| M11.T08 | `pricing/tests/test_seed_providers.py` | Happy path only; no failure modes |
| M11.T09 | `pricing/tests/test_aws_scraper.py` | One test checks logger but not whether the row is actually skipped |

---

## Task specs

### M11.T01 — `catalog/tests/test_benchmark_point.py`

**Current state:** Only tests uniqueness constraints and that throughput must be positive.

**Required scenarios:**
- `test_qwen32b_fp8_tp1_on_h100_batch8_fits_and_benchmark_recorded` — create GPU (80 GB VRAM),
  Model (Qwen-32B, fp8, tp=1), BenchmarkPoint at batch=8 context=4k — assert `fits()` returns
  True and the point is queryable by (model, gpu, quant, tp_size)
- `test_benchmark_point_unique_together_blocks_duplicate_operating_point` — attempt to insert
  duplicate (model, gpu, quant, tp_size, batch_size, context_length) → IntegrityError
- `test_aggregate_decode_tps_must_be_positive` — zero tps → ValidationError
- `test_benchmark_point_links_to_correct_gpu_vram` — assert `point.gpu.vram_gb` accessible
  from the benchmark point (proves FK traversal works end-to-end)

### M11.T02 — `pricing/tests/test_provider.py`

**Current state:** Only tests field constraints and `__str__`.

**Required scenarios:**
- `test_cloud_provider_accepts_pricing_snapshot` — create Provider (cloud, realtime_api) + GPU
  + PricingSnapshot → assert snapshot is queryable via `provider.pricingsnapshot_set`
- `test_on_prem_provider_type_accepted` — `provider_type="on_prem"` saves cleanly
- `test_duplicate_provider_slug_raises_integrity_error` — second Provider with same slug → IntegrityError
- `test_inactive_provider_excluded_from_active_filter` — create active + inactive provider,
  filter `is_active=True` → only active returned
- `test_realtime_api_and_scraped_page_tiers_accepted` — both data_source_tier values accepted

### M11.T03 — `pricing/tests/test_runpod_mapping.py`

**Current state:** Tests lookup table entries one by one with function-style names.

**Required scenarios:**
- Rename all existing tests to scenario form:
  `test_h100_sxm_hint_resolves_to_canonical_h100_sxm_80gb_slug`
- `test_multiple_hints_for_same_gpu_resolve_to_same_canonical_slug` — verify two different
  hint strings that should map to the same GPU do so consistently
- `test_unknown_gpu_hint_returns_none` — an unmapped hint returns `None` (not an exception,
  not a default slug)
- `test_all_mapped_hints_produce_non_empty_slug` — parametrize across the full mapping table,
  assert no entry maps to `None` or `""`

### M11.T04 — `pricing/tests/test_scraper_base.py`

**Current state:** Tests Pydantic immutability and type coercion only.

**Required scenarios:**
- `test_scraped_price_preserves_raw_payload_exactly` — construct with arbitrary `raw` dict,
  assert `price.raw` equals the original dict (proves nothing is dropped)
- `test_scraped_price_with_empty_region_is_valid` — region="" is acceptable (Lambda, Nebius
  don't have regions)
- `test_scraped_price_available_defaults_to_true` — omit `available`, assert it defaults True
- `test_scraped_price_is_immutable` — keep existing test, rename to match scenario convention

### M11.T05 — `catalog/tests/test_gpu.py`

**Current state:** Uniqueness and field constraint tests only.

**Required scenarios:**
- `test_h100_sxm_80gb_slug_is_referenceable_from_pricing_snapshot` — create GPU, create
  PricingSnapshot pointing at it, assert `snapshot.gpu.slug == "nvidia-h100-sxm-80"`
- `test_gpu_with_zero_vram_raises_validation_error` — `vram_gb=0` → ValidationError or
  IntegrityError (whichever the model enforces)
- `test_gpu_tdp_watts_required_for_on_prem_cost_math` — GPU without `tdp_watts` set raises
  ValidationError (proves the on-prem cost path has what it needs)
- `test_two_gpus_same_slug_raises_integrity_error` — keep existing uniqueness test, rename

### M11.T06 — `catalog/tests/test_model.py`

**Current state:** Architecture (dense vs MoE) parameter validation; no pipeline linkage.

**Required scenarios:**
- `test_moe_model_fit_uses_active_params_not_total` — create MoE model (32B total, 6.7B active),
  GPU (80 GB VRAM), assert `fits()` uses active params; a GPU that couldn't fit 32B dense
  should fit 6.7B active
- `test_recommended_quant_links_to_valid_quantization_object` — create Model with
  `recommended_quant`, assert `model.recommended_quant.slug` is accessible
- `test_model_context_length_drives_kv_cache_fit_calculation` — same model at context=4k
  fits on smaller GPU; at context=128k does not (boundary scenario)

### M11.T07 — `catalog/tests/test_seed_catalog_command.py`

**Current state:** Idempotency, expected counts, update logic tested. No error paths.

**Required scenarios:**
- `test_seed_catalog_rejects_gpu_yaml_with_missing_slug_field` — YAML missing required `slug`
  → command fails with a message identifying the bad row (does not silently continue)
- `test_seed_catalog_rejects_negative_tdp_watts` — `tdp_watts: -1` → validation error, loud
  failure, no partial write
- `test_seed_catalog_update_preserves_unchanged_fields` — run seed twice with one field changed;
  assert only that field updated, others unchanged (proves update is a true upsert not replace)

### M11.T08 — `pricing/tests/test_seed_providers.py`

**Current state:** Idempotency and count tests. No failure modes.

**Required scenarios:**
- `test_seed_providers_rejects_yaml_with_invalid_provider_type` — YAML with `provider_type:
  "invalid"` → command fails loudly, no partial write
- `test_seed_providers_is_idempotent` — keep existing test, ensure it asserts on DB count not
  just "no exception"
- `test_seed_providers_update_changes_display_name` — seed with `display_name: "A"`, re-seed
  with `display_name: "B"` → DB has "B" (proves update path works)

### M11.T09 — `pricing/tests/test_aws_scraper.py`

**Current state:** One test checks `logger.info` was called for an unmapped instance type, but
doesn't verify the row is absent from results.

**Required fix:**
- `test_unmapped_aws_instance_type_produces_no_snapshot` — inject a payload with one known
  instance (p4d.24xlarge → H100) and one unmapped (p99.huge) → assert `len(prices) == 1`
  and the unknown instance is not in any result's `gpu_slug_hint`
- Remove or merge the logger-only assertion into this scenario

---

## Definition of done

```
pytest catalog/tests/ pricing/tests/ -q        # all green
pytest -q                                       # full suite still green
ruff check && ruff format --check               # clean
mypy catalog pricing                            # clean
python manage.py makemigrations --check         # no pending migrations
```

Every test added must:
1. Have a name that reads as a business scenario
2. Assert on DB state or computed values — not on call counts (unless testing "fires once")
3. Cover at least one failure mode per file (not just happy path)
