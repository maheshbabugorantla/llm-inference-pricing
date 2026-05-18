# M07 — Ops Hardening

**Goal.** Production-readiness work: a TimescaleDB continuous aggregate for historical price trending, retention policy on raw snapshots, Sentry integration for scraper failures, and a weekly canary CI job that exercises all scrapers against committed fixtures.

**Depends on.** M06.

**Definition of done.** Continuous aggregate `pricing_daily_median` exists and refreshes. Snapshots older than 90 days drop automatically. Scraper failures surface in Sentry. Weekly canary CI workflow runs green. ~10 tests passing.

---

## Tasks

### M07.T01 — Continuous aggregate for daily median

`pricing/migrations/000X_continuous_aggregate.py` — `RunSQL`:

```sql
CREATE MATERIALIZED VIEW pricing_daily_median
WITH (timescaledb.continuous) AS
SELECT
    provider_id,
    gpu_id,
    tier,
    region,
    time_bucket(INTERVAL '1 day', scraped_at) AS day,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY hourly_usd) AS median_hourly_usd,
    COUNT(*) AS snapshot_count
FROM pricing_pricingsnapshot
WHERE available = TRUE
GROUP BY provider_id, gpu_id, tier, region, day
WITH NO DATA;

SELECT add_continuous_aggregate_policy('pricing_daily_median',
    start_offset => INTERVAL '7 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
```

**Note.** Continuous aggregates can't use `percentile_cont` directly in older Timescale versions. If pg16 + Timescale latest supports it natively, great. If not, fall back to a regular materialized view refreshed via Celery (`schedule=crontab(minute=20, hour=*/6)`) — note the divergence from the PRD in a comment and flag in `QUESTIONS.md`.

**Test:**
```python
@pytest.mark.django_db(transaction=True)
def test_daily_median_exists_in_timescaledb_views():
    from django.db import connection
    with connection.cursor() as c:
        c.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates "
            "WHERE view_name = 'pricing_daily_median'"
        )
        assert c.fetchone() is not None
```

---

### M07.T02 — Retention policy on raw snapshots

Add to a migration:

```sql
SELECT add_retention_policy('pricing_pricingsnapshot', INTERVAL '90 days');
```

The continuous aggregate keeps daily medians forever (or as long as the retention on `pricing_daily_median` allows). 90 days of raw snapshots is enough to investigate recent drift without bloating the hypertable.

**Test:**
```python
def test_retention_policy_registered():
    with connection.cursor() as c:
        c.execute(
            "SELECT config FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' "
            "AND hypertable_name = 'pricing_pricingsnapshot'"
        )
        row = c.fetchone()
        assert row is not None
        assert "90 days" in str(row[0])
```

---

### M07.T03 — Sentry integration

- Add `sentry-sdk` to `pyproject.toml`.
- In `pricing_dashboard/settings.py`:

```python
import os
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.celery import CeleryIntegration

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=0.0,    # no perf tracing yet
        send_default_pii=False,
    )
```

- Update Celery scraper tasks to call `sentry_sdk.capture_exception()` before re-raising (already retried via `self.retry`).
- For Tier 2 scraper HTML hash drift specifically: when the parser yields zero prices, raise `ParserDriftError` (new exception class in `pricing/scrapers/base.py`) — the task catches it, sends a Sentry message at `error` level, and does NOT retry (HTML structure changes need human attention, not retries).

**Tests:**
- `test_parser_drift_error_is_raised_on_zero_results`
- `test_celery_task_captures_exception_to_sentry` (uses `sentry_sdk.transport.HttpTransport` mock)

---

### M07.T04 — Weekly canary CI job

`.github/workflows/canary.yml`:

```yaml
name: canary
on:
  schedule:
    - cron: '0 12 * * 1'    # Mondays 12:00 UTC
  workflow_dispatch:

jobs:
  canary:
    runs-on: ubuntu-latest
    services:
      db:
        image: timescale/timescaledb:latest-pg16
        env: { POSTGRES_PASSWORD: postgres }
        ports: [5432:5432]
        options: --health-cmd pg_isready
      redis:
        image: redis:7-alpine
        ports: [6379:6379]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e ".[dev]"
      - run: python manage.py migrate
      - run: python manage.py seed_catalog
      - run: python manage.py seed_providers
      # Run each scraper against its fixture (patched in tests)
      - run: pytest pricing/tests/test_scrape_*_command.py -q
      # Smoke a real RunPod call (no auth needed)
      - run: python manage.py scrape_pricing --provider runpod
        continue-on-error: true   # network flakiness shouldn't fail canary
```

The canary's purpose is to surface scraper drift before users notice. If a scraper's fixture matches but the live API has changed, the smoke step fails and the maintainer is notified.

---

### M07.T05 — Logging configuration

`pricing_dashboard/settings.py`:

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "pricing": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "catalog": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
```

Add `python-json-logger` to deps. Structured logs make Sentry breadcrumbs more useful.

---

## Milestone verification

```bash
python manage.py migrate
pytest -q
# canary workflow runs green on next Monday or via workflow_dispatch
```

Mark M07 done. Stop.

---

## Out of scope

- Metrics export (Prometheus). Phase 2.
- Alerting rules beyond Sentry (PagerDuty, etc.). Phase 2.
- Cost-explorer historical UI. Phase 2.
