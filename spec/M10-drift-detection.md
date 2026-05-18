# M10 — Drift Detection (Optional)

**Goal.** Weekly sanity check against ComputePrices.com (or equivalent third-party aggregator) for Tier 3 providers where direct scraping isn't viable. Writes drift alerts to a `PricingDriftAlert` log table; never auto-updates curated YAML. ADR-012 toggle.

**Depends on.** M09 (Tier 3 providers seeded).

**Definition of done.** `PricingDriftAlert` model exists; weekly Celery task runs against a fixture (production deployment subject to TOS verification — disabled by default); admin lists alerts with diff between curated and observed prices. ~10 tests passing.

**Status.** Optional. Skip if TOS review concludes scraping isn't permitted, or if the team decides curated YAML + listing_observed_at is sufficient.

---

## Tasks

### M10.T01 — `PricingDriftAlert` model

```python
class PricingDriftAlert(models.Model):
    SEVERITY_CHOICES = [
        ("info", "Info"),
        ("warning", "Warning"),
        ("critical", "Critical"),
    ]

    provider = models.ForeignKey(Provider, on_delete=models.PROTECT)
    gpu = models.ForeignKey("catalog.GPU", on_delete=models.PROTECT)
    tier = models.CharField(max_length=64)
    curated_usd_per_hour = models.DecimalField(max_digits=8, decimal_places=4)
    observed_usd_per_hour = models.DecimalField(max_digits=8, decimal_places=4)
    drift_pct = models.DecimalField(max_digits=6, decimal_places=3)
    source_url = models.URLField()
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES)
    detected_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-detected_at",)
        indexes = [models.Index(fields=["acknowledged_at", "-detected_at"])]
```

Severity thresholds:
- `info`: |drift| < 5%
- `warning`: 5% ≤ |drift| < 20%
- `critical`: |drift| ≥ 20%

Tests: model invariants, severity calculation, factory.

---

### M10.T02 — Scraper for ComputePrices.com

Same `ScrapedPrice` contract, but a different kind of consumer: results are compared, not persisted as snapshots.

`pricing/scrapers/computeprices.py`:

```python
def fetch_computeprices_table(gpu_slug: str) -> list[dict]:
    """Scrape the per-GPU page. Returns raw rows of {provider, hourly_usd, ...}."""
    ...

def map_computeprices_provider(provider_name: str) -> str | None:
    """Map their provider names to our slugs."""
    ...
```

**TOS reminder.** Before running this in production, verify:
1. ComputePrices.com terms permit automated scraping
2. Whether attribution / rate limiting is required
3. Whether their data is updated frequently enough to be useful

Note these in the module docstring.

---

### M10.T03 — Drift detection service

`pricing/services/drift_detection.py`:

```python
@transaction.atomic
def check_tier3_drift() -> list[PricingDriftAlert]:
    """For each Tier 3 ReservedCapacityProduct, fetch the latest equivalent
    from ComputePrices.com and create a drift alert if prices differ."""
    alerts_created = []

    tier3_products = ReservedCapacityProduct.objects.filter(
        cloud_provider__data_source_tier="manual_curation",
        is_active=True,
    ).select_related("cloud_provider", "gpu")

    for product in tier3_products:
        # Fetch comparable price from aggregator
        observed_rows = fetch_computeprices_table(product.gpu.slug)
        matching = [
            r for r in observed_rows
            if map_computeprices_provider(r["provider"]) == product.cloud_provider.slug
        ]
        if not matching:
            continue

        observed = Decimal(str(matching[0]["hourly_usd"]))
        # Effective committed rate for the product
        # (would need to compute via compute_reserved_cloud_cost on a dummy deployment)
        curated = product.per_active_hour_usd or product.upfront_usd / Decimal(720)  # rough
        drift_pct = ((observed - curated) / curated * Decimal(100)).quantize(Decimal("0.001"))

        if abs(drift_pct) < Decimal("0.5"):
            continue    # noise threshold

        severity = (
            "critical" if abs(drift_pct) >= Decimal(20)
            else "warning" if abs(drift_pct) >= Decimal(5)
            else "info"
        )
        alert = PricingDriftAlert.objects.create(
            provider=product.cloud_provider,
            gpu=product.gpu,
            tier=f"reserved-{product.slug}",
            curated_usd_per_hour=curated,
            observed_usd_per_hour=observed,
            drift_pct=drift_pct,
            source_url="https://computeprices.com/" + product.gpu.slug,
            severity=severity,
        )
        alerts_created.append(alert)

    return alerts_created
```

**Tests** with fixture-based aggregator response:
- `test_drift_detection_creates_alert_when_prices_diverge`
- `test_drift_detection_skips_when_within_noise_threshold`
- `test_severity_classification_at_each_threshold`
- `test_no_alert_when_no_matching_provider_in_aggregator`

---

### M10.T04 — Celery task + Beat entry (disabled by default)

```python
@shared_task
def computeprices_sanity_check() -> int:
    if not os.environ.get("ENABLE_COMPUTEPRICES_DRIFT_CHECK"):
        logger.info("computeprices drift check is disabled (set env var to enable)")
        return 0
    return len(check_tier3_drift())
```

`CELERY_BEAT_SCHEDULE` gains the entry from PRD §10.1:

```python
"computeprices-sanity-check": {
    "task": "pricing.tasks.computeprices_sanity_check",
    "schedule": crontab(minute=0, hour=8, day_of_week=1),    # Mondays 8am
},
```

The env-var guard means even with the Beat schedule live, the task is a no-op until explicitly turned on.

---

### M10.T05 — Admin views

`PricingDriftAlertAdmin`:
- `list_display`: `detected_at`, `provider`, `gpu`, `tier`, `curated_usd_per_hour`, `observed_usd_per_hour`, `drift_pct`, `severity`, `acknowledged_at`.
- `list_filter`: `severity`, `acknowledged_at__isnull`, `provider`.
- Admin action: "Mark selected acknowledged" → sets `acknowledged_at = timezone.now()`.

---

## Milestone verification

```bash
python manage.py migrate
ENABLE_COMPUTEPRICES_DRIFT_CHECK=1 python manage.py shell -c "
from pricing.services.drift_detection import check_tier3_drift
alerts = check_tier3_drift()
print(f'{len(alerts)} drift alerts created')
"

pytest -q
ruff check && ruff format --check
mypy catalog pricing
```

Mark M10 done. Phase 1 is fully complete. Stop.

---

## Out of scope

- UI for managing drift alerts. Phase 2.
- Email/Slack notifications on critical drift. Phase 2 (or integrate with M07's Sentry).
- Automated curated YAML updates from aggregator data. **Explicitly NOT done** — humans approve all curated changes via PR (ADR-012).
- Other aggregator sources beyond ComputePrices.com. Future milestone if needed.

---

## What comes after M10

Phase 2 work (out of scope for this spec system):

- Django REST Framework API exposing `current_cost_cells` and `pricing_daily_median`.
- Angular frontend.
- Authentication / multi-tenancy if going public.
- Per-cost-cell freshness indicator in UI (`data_source_tier` + `listing_observed_at` → warning badge).
- Cost forecasting / scenario builder (what-if utilization sliders).

At this point the spec system has served its purpose: a green-field Django project with twelve entities, four deployment modes, three data tiers, eight scrapers, and a four-way cost comparison. Hand off to Phase 2.
