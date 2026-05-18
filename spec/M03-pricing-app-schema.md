# M03 — Pricing App Schema (Provider, PricingSnapshot, TimescaleDB)

**Goal.** Land the `pricing` Django app, the generalized `Provider` model with `data_source_tier`, the `PricingSnapshot` hypertable, and the TimescaleDB extension migration. No scrapers yet — just the foundation snapshots will write into.

**Depends on.** M02.

**Definition of done.** TimescaleDB extension is installed; `PricingSnapshot` is a hypertable; manual `INSERT` of a snapshot works; ~15 tests passing.

---

## Tasks

### M03.T01 — Bootstrap `pricing` app

Add `pricing` to `INSTALLED_APPS` (already done in M00). Create `pricing/apps.py`, `pricing/models.py` (empty), `pricing/tests/__init__.py`, `pricing/tests/conftest.py`, `pricing/tests/factories.py`.

`pricing/tests/test_canary.py` confirms imports:

```python
def test_pricing_app_imports():
    from pricing.apps import PricingConfig    # noqa
```

Run `pytest pricing/tests/ -q` → 1 passing.

---

### M03.T02 — `Provider` model

**RED.** `pricing/tests/test_provider.py`:

```python
import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from pricing.tests.factories import ProviderFactory


@pytest.mark.django_db
def test_provider_slug_unique():
    ProviderFactory(slug="runpod")
    with pytest.raises(IntegrityError):
        ProviderFactory(slug="runpod")


@pytest.mark.django_db
@pytest.mark.parametrize("tier", ["realtime_api", "scraped_page", "manual_curation", "synthetic"])
def test_provider_data_source_tier_accepts_valid_values(tier):
    p = ProviderFactory(data_source_tier=tier)
    p.full_clean()


@pytest.mark.django_db
def test_provider_data_source_tier_rejects_invalid(setting_invalid=True):
    p = ProviderFactory(data_source_tier="something-else")
    with pytest.raises(ValidationError):
        p.full_clean()


@pytest.mark.django_db
@pytest.mark.parametrize("ptype", ["cloud", "on_prem"])
def test_provider_type_choices(ptype):
    p = ProviderFactory(provider_type=ptype)
    p.full_clean()
```

**GREEN.** `pricing/models.py`:

```python
from __future__ import annotations

from decimal import Decimal

from django.db import models


class Provider(models.Model):
    TYPE_CHOICES = [("cloud", "Cloud"), ("on_prem", "On-premises")]
    DATA_SOURCE_TIERS = [
        ("realtime_api", "Tier 1 — real-time machine-readable API"),
        ("scraped_page", "Tier 2 — HTML/page scraping"),
        ("manual_curation", "Tier 3 — gated; YAML curation + override"),
        ("synthetic", "On-prem / reserved-cloud generator output"),
    ]

    slug = models.SlugField(unique=True, max_length=64)
    display_name = models.CharField(max_length=64)
    provider_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    data_source_tier = models.CharField(max_length=24, choices=DATA_SOURCE_TIERS)
    pricing_url = models.URLField(blank=True)
    has_api = models.BooleanField(default=False)
    api_endpoint = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("provider_type", "slug")

    def __str__(self) -> str:
        return self.display_name
```

Migrate. Run tests → 4 passing.

Add `ProviderFactory` to `pricing/tests/factories.py`:

```python
import factory
from factory.django import DjangoModelFactory

from pricing.models import Provider


class ProviderFactory(DjangoModelFactory):
    class Meta:
        model = Provider

    slug = factory.Sequence(lambda n: f"provider-{n}")
    display_name = "Test Provider"
    provider_type = "cloud"
    data_source_tier = "realtime_api"
```

---

### M03.T03 — `PricingSnapshot` model (regular table for now)

**RED.** `pricing/tests/test_pricing_snapshot.py`:

```python
from decimal import Decimal

import pytest
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.tests.factories import ProviderFactory, PricingSnapshotFactory


@pytest.mark.django_db
def test_pricing_snapshot_stores_decimal_hourly_usd():
    snap = PricingSnapshotFactory(hourly_usd=Decimal("2.49"))
    snap.refresh_from_db()
    assert snap.hourly_usd == Decimal("2.49")
    assert isinstance(snap.hourly_usd, Decimal)


@pytest.mark.django_db
def test_pricing_snapshot_raw_payload_is_jsonfield():
    snap = PricingSnapshotFactory(raw_payload={"price": "2.49", "raw_origin": "test"})
    snap.refresh_from_db()
    assert snap.raw_payload["price"] == "2.49"


@pytest.mark.django_db
def test_pricing_snapshot_scraped_at_required():
    """scraped_at is mandatory and timezone-aware."""
    snap = PricingSnapshotFactory(scraped_at=timezone.now())
    assert snap.scraped_at.tzinfo is not None
```

Add `PricingSnapshotFactory`:

```python
from pricing.models import PricingSnapshot
from catalog.tests.factories import GPUFactory

class PricingSnapshotFactory(DjangoModelFactory):
    class Meta:
        model = PricingSnapshot

    provider = factory.SubFactory(ProviderFactory)
    gpu = factory.SubFactory(GPUFactory)
    tier = "on_demand"
    region = ""
    hourly_usd = factory.Faker("pydecimal", left_digits=2, right_digits=4, positive=True)
    available = True
    scraped_at = factory.LazyFunction(lambda: __import__("django.utils.timezone", fromlist=["now"]).now())
    raw_payload = factory.LazyFunction(dict)
```

**GREEN.** Append to `pricing/models.py`:

```python
class PricingSnapshot(models.Model):
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT)
    gpu = models.ForeignKey("catalog.GPU", on_delete=models.PROTECT)
    tier = models.CharField(max_length=64)
    region = models.CharField(max_length=64, blank=True)
    hourly_usd = models.DecimalField(max_digits=8, decimal_places=4)
    available = models.BooleanField(default=True)
    scraped_at = models.DateTimeField(db_index=True)
    raw_payload = models.JSONField()

    class Meta:
        ordering = ("-scraped_at",)
        indexes = [
            models.Index(fields=["provider", "gpu", "tier", "-scraped_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider.slug}/{self.gpu.slug}/{self.tier} @ {self.scraped_at:%Y-%m-%d %H:%M}"
```

Migrate. Run tests → 3 passing.

---

### M03.T04 — TimescaleDB extension + hypertable migration

Two-step custom migration: enable extension, then convert table to hypertable.

**RED.** `pricing/tests/test_timescale_setup.py`:

```python
import pytest
from django.db import connection


@pytest.mark.django_db
def test_timescaledb_extension_is_installed():
    with connection.cursor() as c:
        c.execute("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")
        row = c.fetchone()
        assert row is not None, "timescaledb extension not installed"


@pytest.mark.django_db
def test_pricing_snapshot_is_a_hypertable():
    with connection.cursor() as c:
        c.execute(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'pricing_pricingsnapshot'"
        )
        assert c.fetchone() is not None, "pricing_pricingsnapshot is not a hypertable"
```

Note: these tests assume the test DB has TimescaleDB. Ensure CI Postgres image is `timescale/timescaledb`.

**GREEN.** `pricing/migrations/0002_timescaledb.py`:

```python
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("pricing", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS timescaledb;",
            reverse_sql="-- preserved",
        ),
        migrations.RunSQL(
            sql=(
                "SELECT create_hypertable("
                "  'pricing_pricingsnapshot', 'scraped_at', "
                "  chunk_time_interval => INTERVAL '7 days', "
                "  if_not_exists => TRUE, migrate_data => TRUE"
                ");"
            ),
            reverse_sql="-- irreversible",
        ),
    ]
```

Run `python manage.py migrate` against the dev DB, then run tests → 2 passing.

**Watch out:** if the test DB is dropped and recreated by pytest between runs (depends on `--reuse-db`), the extension must be re-created each time. Default pytest-django config recreates the DB; the migration covers this.

---

### M03.T05 — Admin registration

Mirror previous milestones: read-only admin for `Provider` and `PricingSnapshot` with useful `list_display`, `search_fields`, `list_filter`.

For `PricingSnapshot`, `list_filter = ("provider", "gpu", "tier", "available")` and `list_display = ("scraped_at", "provider", "gpu", "tier", "hourly_usd")`. Default ordering by `-scraped_at`.

Test confirms registration. 1 passing.

---

### M03.T06 — Invariant I1 enforcement

```python
# tests/test_invariants.py — append

@pytest.mark.django_db
def test_invariant_i1_hourly_usd_non_negative():
    from pricing.tests.factories import PricingSnapshotFactory
    from decimal import Decimal

    # Positive value works
    PricingSnapshotFactory(hourly_usd=Decimal("0.01"))

    # Negative value should ideally be rejected by a check constraint,
    # but Django Decimal doesn't enforce that by default. Add a model-level
    # constraint:
```

Add a `CheckConstraint` to `PricingSnapshot.Meta`:

```python
class Meta:
    ordering = ("-scraped_at",)
    constraints = [
        models.CheckConstraint(
            check=models.Q(hourly_usd__gte=0),
            name="pricingsnapshot_hourly_usd_nonneg",
        ),
    ]
    indexes = [...]
```

Migrate. Update invariant test to confirm DB rejects negative.

---

## Milestone verification

```bash
python manage.py migrate
python manage.py shell -c "
from pricing.models import Provider, PricingSnapshot
from catalog.models import GPU
from decimal import Decimal
from django.utils import timezone

p = Provider.objects.create(
    slug='manual-test', display_name='Manual Test',
    provider_type='cloud', data_source_tier='realtime_api',
)
gpu = GPU.objects.first()
PricingSnapshot.objects.create(
    provider=p, gpu=gpu, tier='on_demand', hourly_usd=Decimal('2.49'),
    scraped_at=timezone.now(), raw_payload={'test': True},
)
print('inserted:', PricingSnapshot.objects.count())
"

pytest pricing/ tests/ -q
ruff check && ruff format --check
mypy catalog pricing
python manage.py makemigrations --check
```

Update `spec/INDEX.md`. Stop.

---

## Out of scope for M03

- Scrapers. M04+.
- Cost-cell materialized view. M06.
- Continuous aggregates / retention. M07.
- On-prem / reserved-cloud deployment models. M08 / M09.
