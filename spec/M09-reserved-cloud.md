# M09 — Reserved Cloud

**Goal.** Model reserved cloud commitments as a separate axis from on-demand. `ReservedCapacityProduct` is the buyable thing (e.g. "AWS p5.48xlarge 14-day capacity block", "Lambda Reserved 1yr H100"); `ReservedCloudDeployment` is a scenario instance with utilization assumptions and optional negotiated override. Generator emits committed-rate and reservation-marginal synthetic snapshots, reusing the existing cloud `Provider` (ADR-010).

**Depends on.** M08 (synthetic generator pattern), M03 (PricingSnapshot), M06 (cost cells pick up new snapshots).

**Definition of done.** Both models live; `ReservedCapacityProduct` covers four payment cadences (all_upfront, partial_upfront, no_upfront, capacity_block). Generator produces committed and marginal snapshots that pass the four-way Appendix A comparison. ~30 tests passing.

---

## Background math (PRD §7.4)

```
useful_active_hours = term_hours * billable_utilization_pct
  where billable_utilization_pct = max(expected_utilization_pct, minimum_utilization_floor_pct)

total_commitment_cost =
    upfront_usd
  + monthly_recurring_usd * term_months
  + per_active_hour_usd * useful_active_hours
  + capacity_block_total_usd       # for one-shot blocks

node_hourly_committed = total_commitment_cost / useful_active_hours

# Reservation-marginal: treat commitment as sunk; only metered cost remains.
node_hourly_reservation_marginal = per_active_hour_usd

per_gpu_*  = node_hourly_* / gpus_per_node
```

Four cadence patterns reduce to the same formula via zeros:
- **all_upfront:** monthly = 0, per_hour = 0.
- **partial_upfront:** upfront > 0, monthly > 0, per_hour = 0.
- **no_upfront:** upfront = 0, monthly > 0, per_hour optional.
- **capacity_block:** capacity_block_total_usd > 0, others = 0.

---

## Tasks

### M09.T01 — `ReservedCapacityProduct` model

Per PRD §6.11. Key fields:

```python
class ReservedCapacityProduct(models.Model):
    PAYMENT_CADENCE_CHOICES = [
        ("all_upfront", "All Upfront"),
        ("partial_upfront", "Partial Upfront"),
        ("no_upfront", "No Upfront"),
        ("capacity_block", "Capacity Block"),
    ]

    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    cloud_provider = models.ForeignKey(Provider, on_delete=models.PROTECT,
                                        limit_choices_to={"provider_type": "cloud"})
    gpu = models.ForeignKey("catalog.GPU", on_delete=models.PROTECT)
    gpus_per_node = models.PositiveSmallIntegerField()

    payment_cadence = models.CharField(max_length=16, choices=PAYMENT_CADENCE_CHOICES)
    term_months = models.PositiveSmallIntegerField()

    upfront_usd = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    monthly_recurring_usd = models.DecimalField(max_digits=10, decimal_places=2,
                                                 default=Decimal("0"))
    per_active_hour_usd = models.DecimalField(max_digits=8, decimal_places=4,
                                               default=Decimal("0"))
    capacity_block_total_usd = models.DecimalField(max_digits=12, decimal_places=2,
                                                    default=Decimal("0"))

    minimum_utilization_floor_pct = models.DecimalField(max_digits=4, decimal_places=3,
                                                         default=Decimal("0.000"))
    on_demand_reference_usd_per_hour = models.DecimalField(max_digits=8, decimal_places=4,
                                                            null=True, blank=True,
                                                            help_text="display-only; never used in math")

    listing_observed_at = models.DateField(help_text="when this product's pricing was last verified")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("cloud_provider__slug", "term_months", "slug")

    def clean(self):
        """Cadence-specific validation per ADR-011."""
        if self.payment_cadence == "all_upfront":
            if self.monthly_recurring_usd != 0 or self.per_active_hour_usd != 0:
                raise ValidationError("all_upfront requires monthly=0 and per_hour=0")
            if self.upfront_usd <= 0:
                raise ValidationError("all_upfront requires upfront_usd > 0")
        elif self.payment_cadence == "capacity_block":
            if self.capacity_block_total_usd <= 0:
                raise ValidationError("capacity_block requires capacity_block_total_usd > 0")
        # add equivalent checks for partial_upfront and no_upfront

    def implicit_discount_pct(self) -> Decimal | None:
        """Display-only. Compares committed rate vs on-demand reference."""
        if self.on_demand_reference_usd_per_hour is None:
            return None
        # caller computes committed_rate via the math service
        ...
```

Tests cover each `clean()` branch, slug-unique, FK constraints.

---

### M09.T02 — `ReservedCloudDeployment` model

Per PRD §6.12. The deployment carries utilization assumption + optional overrides:

```python
class ReservedCloudDeployment(models.Model):
    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    product = models.ForeignKey(ReservedCapacityProduct, on_delete=models.PROTECT)
    cloud_provider = models.ForeignKey(Provider, on_delete=models.PROTECT)
    region = models.CharField(max_length=64, blank=True)
    num_nodes = models.PositiveIntegerField(default=1)

    expected_utilization_pct = models.DecimalField(max_digits=4, decimal_places=3,
                                                     default=Decimal("0.700"))

    # Optional overrides — when the user has a negotiated deal that differs
    # from the public reference numbers in the product
    upfront_override_usd = models.DecimalField(max_digits=12, decimal_places=2,
                                                 null=True, blank=True)
    monthly_recurring_override_usd = models.DecimalField(max_digits=10, decimal_places=2,
                                                          null=True, blank=True)
    per_hour_override_usd = models.DecimalField(max_digits=8, decimal_places=4,
                                                  null=True, blank=True)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("slug",)
```

Tests: factory, FKs, overrides nullable.

---

### M09.T03 — Cost math service

`pricing/services/reserved_cloud_cost.py`:

```python
def _effective_costs(d):
    """Returns (upfront, monthly_recurring, per_hour) using overrides if set."""
    p = d.product
    return (
        d.upfront_override_usd          or p.upfront_usd,
        d.monthly_recurring_override_usd or p.monthly_recurring_usd,
        d.per_hour_override_usd          or p.per_active_hour_usd,
    )


def compute_reserved_cloud_cost(deployment) -> CostBreakdown:
    p = deployment.product
    billable_util = max(
        deployment.expected_utilization_pct,
        p.minimum_utilization_floor_pct,
    )
    term_hours = Decimal(p.term_months) * Decimal(730)
    useful_active_hours = term_hours * billable_util
    if useful_active_hours <= 0:
        raise ValueError("useful_active_hours must be positive")

    upfront, monthly, per_hour = _effective_costs(deployment)

    total_commitment = (
        upfront
        + monthly * Decimal(p.term_months)
        + per_hour * useful_active_hours
        + p.capacity_block_total_usd
    )
    node_committed = total_commitment / useful_active_hours
    node_marginal = per_hour     # commitment treated as sunk

    return {
        "useful_active_hours": useful_active_hours,
        "total_commitment_usd": total_commitment.quantize(Decimal("0.01")),
        "node_hourly_committed": node_committed.quantize(Decimal("0.0001")),
        "node_hourly_reservation_marginal": node_marginal.quantize(Decimal("0.0001")),
        "per_gpu_hourly_committed": (node_committed / p.gpus_per_node).quantize(Decimal("0.0001")),
        "per_gpu_hourly_reservation_marginal": (node_marginal / p.gpus_per_node).quantize(Decimal("0.0001")),
        "billable_utilization_pct": billable_util,
    }
```

**Hand-verified test cases:**
- `test_aws_capacity_block_p5_14_day_committed_rate` — using AWS Capacity Block pricing example from PRD Appendix A.
- `test_lambda_reserved_1yr_committed_rate_around_1_89_per_gpu_hour`
- `test_all_upfront_cadence_amortizes_correctly`
- `test_no_upfront_cadence_with_high_per_hour_metered_cost`
- `test_floor_kicks_in_when_expected_below_minimum` — e.g. Lambda Reserved with 50% expected but 70% floor → billable = 70%.
- `test_override_takes_precedence_over_product_price` — set `per_hour_override_usd`, confirm math uses it.
- `test_reservation_marginal_equals_per_hour` — sanity check.

---

### M09.T04 — Synthetic snapshot generator

`pricing/generators/reserved_cloud.py`:

```python
@transaction.atomic
def regenerate_reserved_cloud_snapshots() -> int:
    """For each active ReservedCloudDeployment, emit two snapshots
    (reserved-{slug}, reserved-marginal-{slug}) under the EXISTING cloud Provider
    (per ADR-010 — reuse, don't create synthetic providers)."""
    now = timezone.now()
    written = 0

    for d in ReservedCloudDeployment.objects.select_related(
        "product__gpu", "cloud_provider"
    ).filter(is_active=True):
        breakdown = compute_reserved_cloud_cost(d)
        for tier_suffix, per_gpu in [
            ("reserved", breakdown["per_gpu_hourly_committed"]),
            ("reserved-marginal", breakdown["per_gpu_hourly_reservation_marginal"]),
        ]:
            PricingSnapshot.objects.create(
                provider=d.cloud_provider,          # reuse — not synthetic!
                gpu=d.product.gpu,
                tier=f"{tier_suffix}-{d.slug}",
                region=d.region,
                hourly_usd=per_gpu,
                available=True,
                scraped_at=now,
                raw_payload={
                    "deployment_slug": d.slug,
                    "product_slug": d.product.slug,
                    "breakdown": {k: str(v) for k, v in breakdown.items()},
                },
            )
            written += 1
    return written
```

Celery task + Beat entry + post_save signal mirror M08.T04.

**Tests:**
- `test_generator_reuses_cloud_provider_not_synthetic_one` — Provider.slug stays "lambda", not "lambda-reserved-prod-1yr".
- `test_tier_string_is_composite` — `tier="reserved-prod-1yr"` with composite slug.
- `test_generator_emits_both_committed_and_marginal_snapshots`
- `test_post_save_signal_regenerates`

---

### M09.T05 — YAML schemas + curated seeds

Add `ReservedCapacityProductYAML` and `ReservedCloudDeploymentYAML` to `pricing/services/seed.py`.

Seed at least 12 products across 8 providers, covering all four payment cadences and the Tier 1 / 2 / 3 mix. Examples:

- `seeds/reserved/products/aws-p5-48xl-capacity-block-14d.yaml` — capacity_block cadence.
- `seeds/reserved/products/aws-p4d-3yr-all-upfront.yaml` — all_upfront.
- `seeds/reserved/products/gcp-a3-cud-1yr.yaml` — no_upfront (per-hour discount).
- `seeds/reserved/products/azure-nd-h100-ri-1yr.yaml` — partial_upfront.
- `seeds/reserved/products/lambda-reserved-prod-1yr-h100.yaml` — all_upfront, 70% floor (Tier 2 reference).
- `seeds/reserved/products/coreweave-h100-reserved-1yr.yaml` — Tier 3, curated reference with `listing_observed_at`.
- `seeds/reserved/products/crusoe-mi300x-3yr.yaml` — Tier 3.
- `seeds/reserved/products/oci-bm-gpu-h100-1yr.yaml` — Tier 3.

Plus example deployments under `seeds/reserved/deployments/`:

- `seeds/reserved/deployments/dfa-aws-capacity-block.yaml` — 14-day burst scenario.
- `seeds/reserved/deployments/lambda-prod-1yr-with-actual-deal.yaml` — uses overrides for the negotiated rate.

`seed_reserved` management command. Run order: `seed_catalog` → `seed_providers` → `seed_on_prem` → `seed_reserved`.

---

### M09.T06 — Admin registration

Read-only admin for both models. `ReservedCapacityProduct.list_display` includes `cloud_provider`, `payment_cadence`, `term_months`, `listing_observed_at`. A custom admin filter highlights products with `listing_observed_at > 90 days ago` for staleness review.

---

### M09.T07 — Four-way Appendix A integration test

Validates the cost-cell view shows all four deployment modes side-by-side. PRD §7.6 Appendix A example: Qwen2.5-Coder-32B FP8 at batch=8 ctx=32k.

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.smoke
def test_appendix_a_four_way_comparison():
    """End-to-end: seed catalog, seed scenarios, refresh view, verify all four tiers present."""
    call_command("seed_catalog")
    call_command("seed_providers")
    call_command("seed_on_prem")
    call_command("seed_reserved")

    # Simulate one scrape:
    Provider.objects.update_or_create(slug="runpod", defaults={...})
    PricingSnapshot.objects.create(
        provider=Provider.objects.get(slug="runpod"),
        gpu=GPU.objects.get(slug="nvidia-h100-sxm-80"),
        tier="community", hourly_usd=Decimal("1.99"),
        scraped_at=timezone.now(), raw_payload={},
    )

    from pricing.generators.on_prem import regenerate_on_prem_snapshots
    from pricing.generators.reserved_cloud import regenerate_reserved_cloud_snapshots
    from pricing.services.cost import refresh_cost_cells

    regenerate_on_prem_snapshots()
    regenerate_reserved_cloud_snapshots()
    refresh_cost_cells(concurrently=False)

    with connection.cursor() as c:
        c.execute("""
            SELECT pricing_tier, usd_per_m_output FROM pricing_current_cost_cells
            WHERE model_id IN (SELECT id FROM catalog_model WHERE slug='qwen-2-5-coder-32b')
              AND tp_size = 1 AND batch_size = 8 AND context_length = 32768
            ORDER BY usd_per_m_output
        """)
        rows = c.fetchall()

    tiers = {r[0] for r in rows}
    # Four modes present:
    assert "community" in tiers                              # on-demand cloud
    assert any(t.startswith("reserved-") for t in tiers)     # reserved cloud
    assert "tco" in tiers                                     # on-prem green-field
    assert "marginal" in tiers                                # on-prem with sunk capex

    # Ordering sanity per PRD:
    # marginal (DFA MI300X sunk) < tco (Lambda Echelon) similar to reserved < community
    costs = dict(rows)
    assert costs["marginal"] < costs["tco"]
```

---

## Milestone verification

```bash
python manage.py migrate
python manage.py seed_catalog
python manage.py seed_providers
python manage.py seed_on_prem
python manage.py seed_reserved
python manage.py shell -c "
from pricing.generators.on_prem import regenerate_on_prem_snapshots
from pricing.generators.reserved_cloud import regenerate_reserved_cloud_snapshots
from pricing.services.cost import refresh_cost_cells
print('on-prem snaps:', regenerate_on_prem_snapshots())
print('reserved snaps:', regenerate_reserved_cloud_snapshots())
refresh_cost_cells(concurrently=False)
"

# Verify all four modes appear in cost cells
python manage.py shell -c "
from django.db import connection
with connection.cursor() as c:
    c.execute(\"SELECT DISTINCT split_part(pricing_tier, '-', 1) FROM pricing_current_cost_cells\")
    print(set(r[0] for r in c.fetchall()))
"

pytest -q
ruff check && ruff format --check
mypy catalog pricing
```

Mark M09 done. The dashboard is now functionally complete for Phase 1: four-mode cost comparison live. Stop.

---

## Out of scope

- Optional ComputePrices.com sanity check. M10.
- REST API. Phase 2.
- Angular UI. Phase 2.
