# spec/TESTING.md — Testing philosophy and patterns

Read before writing any test. The goal is a test suite that proves the system works under
real business conditions, not one that inflates coverage numbers. A test that only verifies
"function A called function B" tells a reader nothing about whether the system is reliable.

---

## Core principle

Every test should answer a question a businessperson could ask:

> "When a customer runs Qwen-32B on a community H100 cluster at 920 tok/s, does the cost
> cell come out at the right $/M tokens — and does it still come out right when the scraper
> returns unexpected data, the network flaps, or utilization changes?"

If you can't articulate the business scenario in the test name, the test is too abstract.

---

## The duress rule

For every service or task, ask:

1. **Happy path** — normal inputs, expected outputs, correct DB state
2. **Failure modes** — network error, bad API response, schema drift — does the system fail
   cleanly and leave no corrupt state behind?
3. **Boundary inputs** — zero utilization, zero GPUs, 100% utilization, 128-char slugs
4. **Ordering and isolation** — does the result change if you run it twice? Does it leave
   ghost rows for the next test?

Tests that only cover (1) pass code review but fail in production. Cover all four.

---

## What "shallow" looks like vs. what we want

### Shallow (do not write this way)

```python
def test_scrape_runpod_calls_persist():
    with patch("pricing.tasks.scrape_runpod_prices") as mock_scrape, \
         patch("pricing.tasks.persist_prices") as mock_persist:
        mock_scrape.return_value = [fake_price]
        scrape_runpod.apply()
    mock_persist.assert_called_once_with([fake_price])
```

This proves nothing about whether `persist_prices` actually works, whether the DB ends up
in the right state, or whether the pipeline handles drift.

### Business scenario (write this way)

```python
@pytest.mark.django_db(transaction=True)
def test_runpod_community_h100_price_lands_in_db(runpod_provider):
    """A RunPod community H100 price scraped from the API persists as a snapshot
    queryable by provider + GPU + tier — the pipeline the cost-cell view reads from."""
    h100 = GPUFactory(slug="h100-sxm")
    scraped = [_scraped("runpod", "H100 SXM", "community", "2.49")]

    with patch("pricing.tasks.scrape_runpod_prices", return_value=scraped):
        scrape_runpod.apply()

    snap = PricingSnapshot.objects.get(provider=runpod_provider, gpu=h100, tier="community")
    assert snap.hourly_usd == Decimal("2.49")
    assert snap.available is True
```

The reader knows exactly which business scenario is being tested and what DB state to expect.

---

## Patterns by test type

### Pure math / service functions (`services/`)

These take model instances and return values. No DB required.

**What to test:**
- Reference values from the PRD with a named comment linking to the relevant section
- Mathematical invariants (capex dominates at green-field, marginal < TCO, cost scales
  linearly with price)
- All `ValueError` guard cases — use the exact match string from the implementation so
  the guard and the test stay coupled

**Pattern:**

```python
def test_qwen32b_community_h100_prd_reference_cost():
    """PRD §6.2: H100 community @ $1.99/hr, tp=1, 920 tok/s → ~$0.60/M tokens."""
    result = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("1.99"),
        tp_size=1,
        tps_aggregate=Decimal("920"),
    )
    assert Decimal("0.55") < result < Decimal("0.65")
```

Name the GPU, the tier, the throughput. Keep the comment anchored to the PRD section.

### DB-backed service functions (`services/` with DB state)

Use `@pytest.mark.django_db`. No `transaction=True` needed unless `on_commit` callbacks
are involved.

**What to test:**
- Write a representative object graph (use factories) → call the service → assert on DB state
- Prove the output is a `Decimal`, not a `float`
- Prove the function handles inactive / deleted objects gracefully

### Celery tasks (`tasks.py`)

Use `@pytest.mark.django_db(transaction=True)` — Celery eager mode runs tasks inline but
`transaction.on_commit()` callbacks only fire when `transaction=True`.

**Critical: mock only at the network boundary.**

```python
# CORRECT — mock the scraper, let persist_prices run against real DB
with patch("pricing.tasks.scrape_runpod_prices", return_value=scraped):
    scrape_runpod.apply()

# WRONG — mocking persist_prices hides the entire persistence layer
with patch("pricing.tasks.persist_prices") as mock:
    ...
```

**Retry and failure tests:**

Celery eager mode with `max_retries=0` does NOT raise `MaxRetriesExceededError` for all
exception types — it re-raises the original exception. Use `apply(throw=False)` instead:

```python
with patch("pricing.tasks.scrape_runpod_prices", side_effect=httpx.ConnectError("down")):
    result = scrape_runpod.apply(throw=False)
assert result.failed()
```

**Drift tests** — simulate a schema change mid-flight by raising `ParserDriftError`:

```python
with patch("pricing.tasks.scrape_runpod_prices",
           side_effect=ParserDriftError("unexpected field")):
    result = scrape_runpod.apply(throw=False)
assert result.failed()
assert PricingSnapshot.objects.filter(provider=runpod_provider).count() == 0
```

The DB assertion is crucial — it proves drift leaves no partial state.

**on_commit race condition:** `transaction.on_commit()` in signals fires after each factory
save in `transaction=True` mode, potentially creating snapshots before your task runs.
Clear stale rows before asserting:

```python
factory_creates_deployment()
PricingSnapshot.objects.all().delete()  # clear signal-created rows
regenerate_on_prem_snapshots_task.apply()
# now assert on known state
```

### Scraper parsers (`scrapers/<provider>.py`)

Parsers are pure functions over fixture JSON. No DB, no mocks needed.

**What to test:**
- Load from fixture file, not inline dict (fixtures survive API changes as documentation)
- Tier exhaustiveness: both `community` and `secure` are present for a known GPU
- Drop behavior: unknown GPU → zero results (not an error, not a ghost row)
- Null price handling: `reserved-1mo` absent from GPU that has no reserved price
- Return type: every `hourly_usd` is `Decimal`, not `float`

**Pattern for unknown-GPU drop:**

```python
def test_parse_drops_unmapped_gpus():
    payload = {"data": {"gpuTypes": [{"displayName": "Unknown GPU X9000", ...}]}}
    assert parse_runpod_response(payload) == []
```

Inline payload is fine here because you're testing the mapping logic, not the API shape.

### Scraper integration (`tasks.py` calling scraper → DB)

Every scraper task needs at least these four scenarios:

| Scenario | What it proves |
|---|---|
| Happy path with known GPU | Price lands in DB with correct tier/region/GPU |
| Unknown GPU in response | No ghost rows created, task succeeds |
| `ParserDriftError` | DB left clean, Sentry alerted (check `capture_exception` called) |
| Network / HTTP error | Task marked failed, DB unchanged |

### Management commands (`management/commands/`)

Use `call_command()` from pytest-django, not `subprocess`. Capture stdout with
`StringIO`. Assert on DB state, not stdout parsing.

For commands that trigger signals, test that the signal fires **once** after the
full batch, not once per object:

```python
with patch("...regenerate_on_prem_snapshots") as mock_regen:
    call_command("seed_on_prem")
mock_regen.assert_called_once()  # not N times for N deployments
```

---

## Naming convention

Test names must read as a business scenario, not as a function name:

| Bad | Good |
|---|---|
| `test_compute_on_prem_cost` | `test_lambda_echelon_4xh100_green_field_per_gpu_hourly_tco` |
| `test_scrape_runpod` | `test_runpod_h100_community_and_secure_tiers_land_in_db` |
| `test_drift_raises` | `test_runpod_scraper_drift_raises_alert_and_leaves_db_clean` |
| `test_zero_util` | `test_zero_utilization_raises_value_error` |

For ValueError boundary tests, the exception name in the test name is acceptable — they
read as constraints, not scenarios. But keep them specific (`test_zero_num_gpus_raises`)
not generic (`test_invalid_input_raises`).

---

## Factories

Use `factory-boy` factories (`pricing/tests/factories.py`, `catalog/tests/factories.py`).

- Factories provide **safe defaults**. A test should only specify the fields that matter
  to that particular scenario. If a test sets `hardware_sku__num_gpus=4`, it's telling the
  reader "num_gpus is the variable that matters here."
- Don't build factories inline in test bodies — that obscures what's under test.
- For cross-field consistency (e.g., `high_util` shares the same SKU as `low_util`), pass
  the FK object explicitly:
  ```python
  high_util = OnPremDeploymentFactory(
      hardware_sku=low_util.hardware_sku,
      expected_utilization_pct=Decimal("0.900"),
  )
  ```

---

## Fixtures

Recorded API responses live in `pricing/tests/fixtures/`. Every scraper gets one.

- Name fixture files after the API endpoint, not the test: `runpod_gputypes_response.json`
- If the API response changes, update the fixture and re-run the parser tests — the diff
  tells you exactly what broke
- Never hard-code API response structure as dicts inside parser tests when a fixture exists

---

## What NOT to assert

- Don't assert call counts unless the count is the business invariant (e.g., "regenerate
  fires once not N times"). Call counts test implementation, not behavior.
- Don't assert on exact datetime values — use `assertAlmostEqual` or `>= before_call`.
- Don't assert on `.pk` or `.id` values — those are DB internals.
- Don't assert on `str(instance)` unless `__str__` is explicitly specified in the PRD.

---

## Before you commit a test, ask

1. Could I delete the implementation and have this test catch the deletion? (If not, the
   test is too shallow.)
2. Does the test name describe a business scenario a non-engineer could understand?
3. Does the test cover at least one failure mode, not just the happy path?
4. Does a DB test assert on DB state, not just return values?
5. Are the only mocked things external I/O (network calls, cloud APIs)?
