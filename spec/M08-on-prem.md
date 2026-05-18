# M08 — On-Prem (HardwareSKU + OnPremDeployment + Generator)

**Goal.** Model on-prem hardware as `HardwareSKU` (the buyable thing) and `OnPremDeployment` (a scenario instance with utilization, depreciation, opex inputs). Generate two synthetic `PricingSnapshot` rows per deployment: `tier="tco"` (green-field amortized) and `tier="marginal"` (capex sunk).

**Depends on.** M03 (PricingSnapshot), M06 (cost-cell view picks up these synthetic snapshots automatically).

**Definition of done.** `HardwareSKU` and `OnPremDeployment` models live, with YAML seeders. A `generate_on_prem_snapshots` service produces correct TCO and marginal snapshots that match worked PRD examples. ~25 tests passing including hand-verified math.

---

## Background math (PRD §7.3)

**Capex amortization** (annualized, then hourly):

```
salvage_value     = capex * salvage_pct
depreciable       = capex - salvage_value
active_hours_year = 8760 * utilization_pct
yearly_capex      = depreciable / depreciation_years
hourly_capex      = yearly_capex / active_hours_year
```

**Opex per active hour:**

```
power_hourly      = (gpu_tdp_watts * num_gpus + host_overhead_watts) * pue / 1000 * power_usd_per_kwh
colo_hourly       = monthly_colo_usd / (730 * utilization_pct)
bandwidth_hourly  = monthly_bandwidth_usd / (730 * utilization_pct)
ops_hourly        = sysadmin_annual_burdened_usd / (2080 * gpu_count_per_admin) / utilization_pct
                    # the / utilization_pct adjusts to per-active-hour
```

**Node hourly TCO** = `hourly_capex + power_hourly + colo_hourly + bandwidth_hourly + ops_hourly`
**Node hourly marginal** = `power_hourly + colo_hourly + bandwidth_hourly + ops_hourly` (no capex)
**Per-GPU hourly** = `node_hourly / num_gpus`

---

## Tasks

### M08.T01 — `HardwareSKU` model

Fields per PRD §6.9:

```python
class HardwareSKU(models.Model):
    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    vendor = models.CharField(max_length=64)              # e.g. "supermicro", "dell", "lambda-echelon"
    num_gpus = models.PositiveSmallIntegerField()
    gpu = models.ForeignKey("catalog.GPU", on_delete=models.PROTECT)
    cpu_model = models.CharField(max_length=128)
    cpu_sockets = models.PositiveSmallIntegerField()
    ram_gb = models.PositiveIntegerField()
    nvme_tb = models.PositiveIntegerField()
    network_gbps = models.PositiveIntegerField()
    host_tdp_watts = models.PositiveIntegerField(help_text="non-GPU power draw at peak")
    reference_msrp_usd = models.DecimalField(max_digits=10, decimal_places=2,
                                              null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("vendor", "slug")
```

Tests: factory, slug-unique, FK to GPU works.

---

### M08.T02 — `OnPremDeployment` model

Per PRD §6.10:

```python
class OnPremDeployment(models.Model):
    slug = models.SlugField(unique=True, max_length=128)
    display_name = models.CharField(max_length=128)
    hardware_sku = models.ForeignKey(HardwareSKU, on_delete=models.PROTECT)
    num_nodes = models.PositiveIntegerField(default=1)

    # Procurement
    capex_per_node_usd = models.DecimalField(max_digits=10, decimal_places=2)
    salvage_pct = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0.100"))
    depreciation_years = models.PositiveSmallIntegerField(default=4)

    # Utilization
    expected_utilization_pct = models.DecimalField(max_digits=4, decimal_places=3,
                                                    default=Decimal("0.700"))

    # Power / facility
    power_usd_per_kwh = models.DecimalField(max_digits=6, decimal_places=4)
    pue = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("1.400"))
    monthly_colo_usd = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    monthly_bandwidth_usd = models.DecimalField(max_digits=10, decimal_places=2,
                                                default=Decimal("0"))

    # Ops
    sysadmin_annual_burdened_usd = models.DecimalField(max_digits=10, decimal_places=2)
    gpu_count_per_admin = models.PositiveIntegerField(default=128)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("slug",)
```

Tests: factory, fields default sensibly, FKs work, slug unique.

---

### M08.T03 — Cost math service with hand-verified cases

`pricing/services/on_prem_cost.py`:

```python
from decimal import Decimal
from typing import TypedDict


class CostBreakdown(TypedDict):
    hourly_capex_per_node: Decimal
    hourly_power_per_node: Decimal
    hourly_colo_per_node: Decimal
    hourly_bandwidth_per_node: Decimal
    hourly_ops_per_node: Decimal
    node_hourly_tco: Decimal
    node_hourly_marginal: Decimal
    per_gpu_hourly_tco: Decimal
    per_gpu_hourly_marginal: Decimal


def compute_on_prem_cost(deployment) -> CostBreakdown:
    """Pure function. Takes an OnPremDeployment instance, returns the breakdown.
    Assumes deployment.hardware_sku is prefetched."""
    sku = deployment.hardware_sku
    gpu = sku.gpu

    # Capex
    depreciable = deployment.capex_per_node_usd * (Decimal(1) - deployment.salvage_pct)
    active_hours_per_year = Decimal(8760) * deployment.expected_utilization_pct
    hourly_capex = depreciable / deployment.depreciation_years / active_hours_per_year

    # Power
    node_watts = Decimal(gpu.tdp_watts) * sku.num_gpus + Decimal(sku.host_tdp_watts)
    power_kw_with_pue = node_watts * deployment.pue / Decimal(1000)
    hourly_power = power_kw_with_pue * deployment.power_usd_per_kwh

    # Facility (per active hour)
    hourly_colo = deployment.monthly_colo_usd / (Decimal(730) * deployment.expected_utilization_pct)
    hourly_bw = deployment.monthly_bandwidth_usd / (Decimal(730) * deployment.expected_utilization_pct)

    # Ops
    ops_per_admin_per_active_hour = (
        deployment.sysadmin_annual_burdened_usd
        / Decimal(2080)
        / Decimal(deployment.gpu_count_per_admin)
        / deployment.expected_utilization_pct
    )
    hourly_ops = ops_per_admin_per_active_hour * Decimal(sku.num_gpus)

    node_tco = hourly_capex + hourly_power + hourly_colo + hourly_bw + hourly_ops
    node_marginal = hourly_power + hourly_colo + hourly_bw + hourly_ops

    return {
        "hourly_capex_per_node": hourly_capex.quantize(Decimal("0.01")),
        "hourly_power_per_node": hourly_power.quantize(Decimal("0.01")),
        "hourly_colo_per_node": hourly_colo.quantize(Decimal("0.01")),
        "hourly_bandwidth_per_node": hourly_bw.quantize(Decimal("0.01")),
        "hourly_ops_per_node": hourly_ops.quantize(Decimal("0.01")),
        "node_hourly_tco": node_tco.quantize(Decimal("0.01")),
        "node_hourly_marginal": node_marginal.quantize(Decimal("0.01")),
        "per_gpu_hourly_tco": (node_tco / sku.num_gpus).quantize(Decimal("0.0001")),
        "per_gpu_hourly_marginal": (node_marginal / sku.num_gpus).quantize(Decimal("0.0001")),
    }
```

**Tests** with hand-verified examples (PRD Appendix A reference cases):

- `test_lambda_echelon_4xh100_green_field_per_gpu_hourly_tco_around_3_89` — Lambda Echelon-style box, 4× H100, capex $180k, 4-yr depreciation, 70% util, $0.10/kWh, PUE 1.4, $1000/mo colo, $200/mo bw, sysadmin $200k/yr, 128 GPUs/admin → per-GPU TCO around $3.89/hr (PRD §7.6).
- `test_marginal_excludes_capex` — node_marginal == node_tco - hourly_capex_per_node.
- `test_mi300x_marginal_around_0_36_per_gpu_at_dfa` — 8× MI300X DFA scenario, capex sunk, $0.06/kWh, PUE 1.6, $2000/mo colo+bw, sysadmin $250k → per-GPU marginal ~$0.36/hr (PRD §7.6).
- `test_higher_utilization_reduces_per_active_hour_costs` — same deployment with util 0.5 vs 0.9, latter is cheaper per hour.
- `test_decimal_arithmetic_no_float_contamination` — pass `float` for one input, expect `TypeError` or precision loss flag.

---

### M08.T04 — Synthetic snapshot generator

`pricing/generators/__init__.py` empty; `pricing/generators/on_prem.py`:

```python
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from pricing.models import OnPremDeployment, PricingSnapshot, Provider
from pricing.services.on_prem_cost import compute_on_prem_cost


@transaction.atomic
def regenerate_on_prem_snapshots() -> int:
    """For each active OnPremDeployment, emit two snapshots (tco, marginal)
    under a synthetic Provider named 'on-prem-{deployment.slug}'."""
    now = timezone.now()
    written = 0

    for d in OnPremDeployment.objects.select_related(
        "hardware_sku__gpu"
    ).filter(is_active=True):
        provider, _ = Provider.objects.update_or_create(
            slug=f"on-prem-{d.slug}",
            defaults={
                "display_name": d.display_name,
                "provider_type": "on_prem",
                "data_source_tier": "synthetic",
            },
        )
        breakdown = compute_on_prem_cost(d)
        for tier_name, per_gpu in [
            ("tco", breakdown["per_gpu_hourly_tco"]),
            ("marginal", breakdown["per_gpu_hourly_marginal"]),
        ]:
            PricingSnapshot.objects.create(
                provider=provider,
                gpu=d.hardware_sku.gpu,
                tier=tier_name,
                region="",
                hourly_usd=per_gpu,
                available=True,
                scraped_at=now,
                raw_payload={
                    "deployment_slug": d.slug,
                    "breakdown": {k: str(v) for k, v in breakdown.items()},
                },
            )
            written += 1
    return written
```

Celery task in `pricing/tasks.py`:

```python
@shared_task
def regenerate_on_prem_snapshots_task() -> int:
    from pricing.generators.on_prem import regenerate_on_prem_snapshots
    return regenerate_on_prem_snapshots()
```

`CELERY_BEAT_SCHEDULE` gains `"regenerate-on-prem": {"task": ..., "schedule": crontab(minute=5)}`.

**On-save signal** in `pricing/models.py`:

```python
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=OnPremDeployment)
def _regenerate_on_save(sender, instance, **kwargs):
    """Regenerate snapshots immediately when a deployment changes."""
    if not kwargs.get("created", False) and not instance.is_active:
        return
    from pricing.generators.on_prem import regenerate_on_prem_snapshots
    regenerate_on_prem_snapshots()
```

**Tests:**
- `test_generator_emits_two_snapshots_per_deployment` — tco and marginal.
- `test_generator_creates_synthetic_provider`
- `test_post_save_signal_triggers_regeneration`
- `test_inactive_deployments_excluded`

---

### M08.T05 — YAML schemas + seeders

- `seeds/hardware/` with one YAML per SKU. Example entries:
  - `lambda-echelon-4xh100.yaml`
  - `supermicro-8xmi300x.yaml`
  - `dell-r760xa-8xh100.yaml`
- `seeds/deployments/` with named scenarios:
  - `dfa-mi300x-marginal.yaml` (capex sunk, low power cost)
  - `lambda-echelon-green-field.yaml` (full TCO)
  - `pinnacle-3xh100-startup.yaml` (small lab)
- Add `HardwareSKUYAML` and `OnPremDeploymentYAML` pydantic schemas to `pricing/services/seed.py` (parallel to provider schema).
- `seed_on_prem` management command loads both. Run after `seed_catalog` (needs GPU FKs).

**Tests:** YAML schema validation, command idempotency, real seeds load to expected counts.

---

### M08.T06 — Admin registration

`HardwareSKU` and `OnPremDeployment` registered read-only. List displays show key cost-driving fields.

---

## Milestone verification

```bash
python manage.py migrate
python manage.py seed_catalog
python manage.py seed_providers
python manage.py seed_on_prem
python manage.py shell -c "
from pricing.generators.on_prem import regenerate_on_prem_snapshots
print('snapshots written:', regenerate_on_prem_snapshots())
"
python manage.py shell -c "
from pricing.services.cost import refresh_cost_cells
refresh_cost_cells(concurrently=False)
from django.db import connection
with connection.cursor() as c:
    c.execute(\"\"\"
        SELECT provider_id, pricing_tier, COUNT(*)
        FROM pricing_current_cost_cells
        WHERE pricing_tier IN ('tco', 'marginal')
        GROUP BY provider_id, pricing_tier
    \"\"\")
    for row in c.fetchall(): print(row)
"
# expect rows for each on-prem deployment with both tco and marginal tiers

pytest -q
ruff check && ruff format --check
mypy catalog pricing
```

Mark M08 done. Stop.

---

## Out of scope

- Reserved cloud. M09.
- Drift detection. M10.
- Multi-region cost modeling. Phase 2.
- Spot/preemptible on-prem pricing. Doesn't apply.
