# M05 — Tier 2 Page Scrapers (Lambda, Vast, Nebius)

**Goal.** Three more scrapers following the M04 pattern. Lambda and Nebius are HTML scrapers (BeautifulSoup). Vast is a REST endpoint that returns a bundle marketplace; we aggregate per GPU.

**Depends on.** M04 (pattern established).

**Definition of done.** Three new modules under `pricing/scrapers/`, all return `list[ScrapedPrice]`. Fixtures committed for each. The `_SCRAPERS` registry in `scrape_pricing` command is extended. ~30 new tests pass.

---

## Tasks

### M05.T01 — Lambda Labs scraper (HTML)

- File: `pricing/scrapers/lambda_labs.py`
- Endpoint: `https://lambdalabs.com/service/gpu-cloud#pricing` (HTML)
- Approach: `httpx.get` the page, `BeautifulSoup` to find the pricing table.
- Tiers captured: `on_demand` (one price per GPU type). Reserved rates are anchor numbers — capture them as `reserved-1mo` / `reserved-1yr` IF the page surfaces them; otherwise leave to M09 catalog seeding.
- GPU map: similar to RunPod's `RUNPOD_GPU_MAP` — `LAMBDA_GPU_MAP = {"H100 (80 GB SXM5)": "nvidia-h100-sxm-80", ...}`.
- Drift detection: compute `hashlib.sha256(html).hexdigest()` and log INFO with the hash + scrape datetime. CI test asserts that the parser handles the committed fixture without errors and that hash function is called.

**Fixture.** `pricing/tests/fixtures/lambda_pricing.html` — a saved copy of the current page (≤ 50KB, sanitized to remove tracking pixels).

**Tests** (each behavior-named):
- `test_lambda_parse_yields_h100_on_demand_price`
- `test_lambda_parse_returns_decimal_prices`
- `test_lambda_parse_drops_unmapped_gpus`
- `test_lambda_parser_handles_missing_reserved_column_gracefully`
- `test_lambda_html_hash_logged_at_info`

**Robustness:** If the table structure changes and the parser yields zero prices, raise `ParserDriftError`. The scrape_runner converts this to a Sentry alert.

---

### M05.T02 — Vast.ai scraper (REST + aggregation)

- File: `pricing/scrapers/vast.py`
- Endpoint: `https://console.vast.ai/api/v0/bundles/` returns ~hundreds of host bundles.
- Approach: GET, parse JSON, group bundles by GPU name, compute `min`, `p50`, `p90` of `dph_total`. Emit one snapshot per `(gpu, tier)` with `p50` as `hourly_usd` and full distribution in `raw`.
- Tiers: `on_demand`, `interruptible` (Vast splits these in the bundle data via the `is_bid_only` flag).
- GPU map: `VAST_GPU_MAP = {"RTX 4090": "nvidia-rtx-4090", "H100 SXM5": "nvidia-h100-sxm-80", ...}`.

**Fixture.** `pricing/tests/fixtures/vast_bundles_sample.json` — a trimmed sample with ~20 bundles covering 3 GPU types.

**Tests:**
- `test_vast_aggregates_bundles_to_p50_hourly`
- `test_vast_separates_on_demand_and_interruptible`
- `test_vast_drops_bundles_for_unmapped_gpus`
- `test_vast_handles_empty_bundle_list_gracefully`
- `test_vast_raw_payload_contains_full_distribution`

---

### M05.T03 — Nebius scraper (HTML)

- File: `pricing/scrapers/nebius.py`
- Endpoint: Nebius pricing page (verify current URL at scrape time; document the exact URL in module docstring).
- Approach: similar to Lambda. HTML parse.
- Tiers: `on_demand` only. Reserved is negotiated — leave to M09 catalog seeding.
- GPU map: limited initially — H100, H200, L40S, HGX H100 cluster nodes.

**Fixture.** `pricing/tests/fixtures/nebius_pricing.html`.

**Tests:** mirror the Lambda set with Nebius-specific GPU names.

---

### M05.T04 — Register new scrapers in `scrape_pricing` command

Update `_SCRAPERS` dict in `pricing/management/commands/scrape_pricing.py`:

```python
from pricing.scrapers import lambda_labs, nebius, runpod, vast

_SCRAPERS = {
    "runpod":      (runpod.scrape,      runpod.map_runpod_gpu),
    "lambda":      (lambda_labs.scrape, lambda_labs.map_lambda_gpu),
    "vast":        (vast.scrape,        vast.map_vast_gpu),
    "nebius":      (nebius.scrape,      nebius.map_nebius_gpu),
}
```

Each scraper module also needs a `Provider.objects.update_or_create(slug=..., defaults={...})` row created during the M04 verification flow — extend the seeding for M05 to register all four providers.

Better: add a `seed_providers` management command (or extend `seed_catalog`) that idempotently ensures all known Provider rows exist. Add a `seeds/providers.yaml`:

```yaml
- slug: runpod
  display_name: RunPod
  provider_type: cloud
  data_source_tier: realtime_api
  pricing_url: https://www.runpod.io/pricing
  has_api: true
  api_endpoint: https://api.runpod.io/graphql

- slug: lambda
  display_name: Lambda Labs
  provider_type: cloud
  data_source_tier: scraped_page
  pricing_url: https://lambdalabs.com/service/gpu-cloud
  has_api: false

- slug: vast
  display_name: Vast.ai
  provider_type: cloud
  data_source_tier: realtime_api
  pricing_url: https://vast.ai/pricing
  has_api: true
  api_endpoint: https://console.vast.ai/api/v0/bundles/

- slug: nebius
  display_name: Nebius
  provider_type: cloud
  data_source_tier: scraped_page
  pricing_url: https://nebius.com/prices
  has_api: false
```

Add a `ProviderYAML` pydantic schema to `pricing/services/seed.py` (parallel to catalog seeds). Extend `seed_catalog` to load it, OR — cleaner — create a `pricing/management/commands/seed_providers.py` that loads only providers. Run order: `seed_catalog` (catalog tables only), then `seed_providers` (pricing-side).

Document order in `BOOTSTRAP.md` and the milestone verification block below.

---

### M05.T05 — Celery tasks for each scraper

Append to `pricing/tasks.py`:

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def scrape_lambda(self) -> int: ...

@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def scrape_vast(self) -> int: ...

@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def scrape_nebius(self) -> int: ...
```

Wire into `CELERY_BEAT_SCHEDULE`. Per PRD §10.1: Tier 2 scrapes daily (not hourly); update schedule to `crontab(minute=15, hour=6)` for each.

Test mirrors `test_scrape_runpod_task_calls_scrape_runner`.

---

## Milestone verification

```bash
python manage.py seed_catalog
python manage.py seed_providers

for p in runpod lambda vast nebius; do
  python manage.py scrape_pricing --provider $p
done

python manage.py shell -c "
from pricing.models import PricingSnapshot
from collections import Counter
print(Counter(PricingSnapshot.objects.values_list('provider__slug', flat=True)))
"
# expect non-zero counts for all four

pytest pricing/ tests/ -q
ruff check && ruff format --check
mypy catalog pricing
```

Mark M05 done. Stop.

---

## Out of scope

- Hyperscaler scrapers (AWS / GCP / Azure). M05.5.
- Cost-cell view. M06.
- On-prem / reserved-cloud. M08 / M09.
