# M06 — `current_cost_cells` Materialized View + Cost Service

**Goal.** Land the materialized view that joins `BenchmarkPoint` × latest `PricingSnapshot` to produce `$/M input tokens` and `$/M output tokens` cost cells. Plus a Python mirror in `pricing/services/cost.py` for unit-testable cost math (the SQL is the production path; the Python mirror is testing/validation).

**Depends on.** M02 (benchmarks), M03 (snapshots), at least one provider's scraper from M04 / M05.

**Definition of done.** `REFRESH MATERIALIZED VIEW pricing_current_cost_cells` succeeds and produces non-null cost cells for every compatibility tuple × operating point × current provider tier. Cost values agree with the Python mirror to within rounding tolerance. ~15 tests passing.

---

## Background math (PRD §7.5)

For a single cost cell:

```
node_hourly_usd        = pricing_snapshot.hourly_usd  (per-GPU $/hr)
tp_size                = benchmark_point.tp_size
prefill_tps_aggregate  = benchmark_point.prefill_tps_aggregate
decode_tps_aggregate   = benchmark_point.decode_tps_aggregate

cost_per_second        = node_hourly_usd * tp_size / 3600
usd_per_m_input        = cost_per_second * 1_000_000 / prefill_tps_aggregate
usd_per_m_output       = cost_per_second * 1_000_000 / decode_tps_aggregate
```

The cost-cell view joins on **latest** snapshot per `(provider, gpu, tier)`. "Latest" via TimescaleDB's `last()` aggregate or a `DISTINCT ON` per the standard Postgres pattern.

---

## Tasks

### M06.T01 — Python cost function (for testing)

`pricing/services/cost.py`:

```python
from __future__ import annotations

from decimal import Decimal


def cost_per_million_tokens(
    *,
    hourly_usd_per_gpu: Decimal,
    tp_size: int,
    tps_aggregate: Decimal,
) -> Decimal:
    """USD per million tokens, given per-GPU hourly rate, TP size, and
    aggregate throughput (tokens/sec across the batch)."""
    if tps_aggregate <= 0:
        raise ValueError("tps_aggregate must be positive")
    seconds_per_million = Decimal(1_000_000) / tps_aggregate
    node_seconds_per_million = seconds_per_million * tp_size
    return (hourly_usd_per_gpu * node_seconds_per_million / Decimal(3600)).quantize(Decimal("0.0001"))
```

**Tests:**
- `test_cost_per_million_qwen32b_h100_fp8_decode_matches_prd_appendix_a` — H100 community @ $1.99/hr, decode_tps_aggregate=920 → expect ~$0.60/M output (PRD §7.6 reference).
- `test_cost_scales_linearly_with_hourly_rate`
- `test_cost_scales_inversely_with_throughput`
- `test_cost_per_million_rejects_zero_throughput`
- `test_cost_returns_decimal_not_float`

---

### M06.T02 — Materialized view SQL migration

`pricing/migrations/000X_current_cost_cells.py`:

```python
from django.db import migrations

VIEW_SQL = """
CREATE MATERIALIZED VIEW pricing_current_cost_cells AS
WITH latest AS (
    SELECT DISTINCT ON (provider_id, gpu_id, tier, region)
        provider_id, gpu_id, tier, region, hourly_usd, scraped_at
    FROM pricing_pricingsnapshot
    WHERE available = TRUE
    ORDER BY provider_id, gpu_id, tier, region, scraped_at DESC
)
SELECT
    bp.id AS benchmark_point_id,
    bp.model_id,
    bp.gpu_id,
    bp.quantization_id,
    bp.tp_size,
    bp.batch_size,
    bp.context_length,
    bp.prefill_tps_aggregate,
    bp.decode_tps_aggregate,
    p.id AS provider_id,
    latest.tier AS pricing_tier,
    latest.region,
    latest.hourly_usd,
    latest.scraped_at AS pricing_scraped_at,
    -- cost math: see pricing.services.cost.cost_per_million_tokens
    (latest.hourly_usd * bp.tp_size * 1000000.0 / (bp.prefill_tps_aggregate * 3600.0))::numeric(10,4)
        AS usd_per_m_input,
    (latest.hourly_usd * bp.tp_size * 1000000.0 / (bp.decode_tps_aggregate * 3600.0))::numeric(10,4)
        AS usd_per_m_output
FROM catalog_benchmarkpoint bp
JOIN latest ON latest.gpu_id = bp.gpu_id
JOIN pricing_provider p ON p.id = latest.provider_id
WHERE bp.prefill_tps_aggregate > 0
  AND bp.decode_tps_aggregate > 0;

CREATE UNIQUE INDEX pricing_cost_cells_uniq
    ON pricing_current_cost_cells (benchmark_point_id, provider_id, pricing_tier, region);
CREATE INDEX pricing_cost_cells_model ON pricing_current_cost_cells (model_id);
CREATE INDEX pricing_cost_cells_gpu ON pricing_current_cost_cells (gpu_id);
"""

DROP_SQL = "DROP MATERIALIZED VIEW IF EXISTS pricing_current_cost_cells;"


class Migration(migrations.Migration):
    dependencies = [("pricing", "0003_constraints")]   # adjust to latest

    operations = [
        migrations.RunSQL(sql=VIEW_SQL, reverse_sql=DROP_SQL),
    ]
```

The unique index is required for `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

---

### M06.T03 — Refresh service + Celery task

`pricing/services/cost.py` (extend):

```python
from django.db import connection


def refresh_cost_cells(*, concurrently: bool = True) -> None:
    """Refresh the materialized view. CONCURRENTLY requires the unique index."""
    sql = "REFRESH MATERIALIZED VIEW {}{}".format(
        "CONCURRENTLY " if concurrently else "",
        "pricing_current_cost_cells",
    )
    with connection.cursor() as c:
        c.execute(sql)
```

`pricing/tasks.py`:

```python
@shared_task
def refresh_current_cost_cells() -> None:
    from pricing.services.cost import refresh_cost_cells
    refresh_cost_cells()
```

`CELERY_BEAT_SCHEDULE` gains:
```python
"refresh-cost-cells": {
    "task": "pricing.tasks.refresh_current_cost_cells",
    "schedule": crontab(minute=10),
},
```

---

### M06.T04 — Integration test: end-to-end cost cell

`pricing/tests/test_cost_cells_integration.py`:

```python
import pytest
from decimal import Decimal
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from catalog.tests.factories import (
    BenchmarkPointFactory, GPUFactory, ModelFactory, QuantizationFactory,
)
from pricing.tests.factories import PricingSnapshotFactory, ProviderFactory


@pytest.mark.django_db(transaction=True)    # transaction=True needed for MV refresh
def test_cost_cell_produced_from_benchmark_and_snapshot():
    quant = QuantizationFactory(slug="fp8")
    gpu = GPUFactory(slug="nvidia-h100-sxm-80", vram_gb=80)
    model = ModelFactory(slug="qwen", recommended_quant=quant)
    bp = BenchmarkPointFactory(
        model=model, gpu=gpu, quantization=quant,
        tp_size=1, batch_size=8, context_length=32768,
        prefill_tps_aggregate=28400, decode_tps_aggregate=920,
    )
    provider = ProviderFactory(slug="runpod")
    PricingSnapshotFactory(
        provider=provider, gpu=gpu, tier="community",
        hourly_usd=Decimal("1.99"), scraped_at=timezone.now(),
    )

    from pricing.services.cost import refresh_cost_cells
    refresh_cost_cells(concurrently=False)    # first refresh must be non-concurrent

    with connection.cursor() as c:
        c.execute("""
            SELECT usd_per_m_input, usd_per_m_output
            FROM pricing_current_cost_cells
            WHERE benchmark_point_id = %s AND provider_id = %s AND pricing_tier = 'community'
        """, [bp.id, provider.id])
        row = c.fetchone()

    assert row is not None
    usd_in, usd_out = row
    # PRD §7.6: ≈ $0.019/M input, ≈ $0.60/M output for these inputs
    assert Decimal("0.01") < usd_in < Decimal("0.05")
    assert Decimal("0.55") < usd_out < Decimal("0.65")


@pytest.mark.django_db(transaction=True)
def test_cost_cell_picks_latest_snapshot():
    """When multiple snapshots exist for the same (provider, gpu, tier), the
    view uses the most recent."""
    # setup ... insert two snapshots 1 hour apart with different prices ...
    # confirm view returns the newer price.
```

---

### M06.T05 — Invariant I5 enforcement

```python
# tests/test_invariants.py — append

@pytest.mark.django_db(transaction=True)
@pytest.mark.smoke
def test_invariant_i5_no_nulls_in_cost_cells():
    from pricing.services.cost import refresh_cost_cells
    refresh_cost_cells(concurrently=False)
    from django.db import connection
    with connection.cursor() as c:
        c.execute("""
            SELECT COUNT(*) FROM pricing_current_cost_cells
            WHERE usd_per_m_input IS NULL OR usd_per_m_output IS NULL
        """)
        assert c.fetchone()[0] == 0
```

---

## Milestone verification

```bash
python manage.py migrate
python manage.py seed_catalog
python manage.py seed_providers
python manage.py scrape_pricing --provider runpod    # need at least one snapshot
python manage.py shell -c "
from pricing.services.cost import refresh_cost_cells
refresh_cost_cells(concurrently=False)
from django.db import connection
with connection.cursor() as c:
    c.execute('SELECT COUNT(*) FROM pricing_current_cost_cells')
    print('cost cells:', c.fetchone()[0])
"
# expect non-zero

pytest -q
ruff check && ruff format --check
mypy catalog pricing
```

Mark M06 done. Stop.

---

## Out of scope

- TimescaleDB continuous aggregate for historical trending. M07.
- Retention policy. M07.
- Phase 2 REST API exposing this view. Phase 2.
