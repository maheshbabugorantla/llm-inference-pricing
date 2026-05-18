# M04 — RunPod Tier 1 Scraper

**Goal.** Build the RunPod GraphQL scraper that returns on-demand (community + secure) AND four reserved tiers (1mo, 3mo, 6mo, 1yr) in one call. Establish the scraper pattern that M05 / M05.5 follow: pure scraper functions returning pydantic dataclasses, persistence in `scrape_runner`, fixture-based tests (no real network).

**Depends on.** M03.

**Definition of done.** `python manage.py scrape_pricing --provider runpod` against a recorded fixture produces ~30 snapshots across GPU types and tiers; the same against a real network call (manual smoke) produces a similar set; ~20 tests passing.

---

## Tasks

### M04.T01 — `ScrapedPrice` pydantic contract + scraper base

All scrapers return `list[ScrapedPrice]`. This contract is the single API the orchestrator depends on.

**RED.** `pricing/tests/test_scraper_base.py`:

```python
from decimal import Decimal
from pricing.scrapers.base import ScrapedPrice


def test_scraped_price_is_immutable():
    p = ScrapedPrice(
        provider_slug="runpod", gpu_slug_hint="NVIDIA H100 SXM",
        tier="community", hourly_usd=Decimal("1.99"),
        region="", available=True, raw={"x": 1},
    )
    import pytest
    with pytest.raises(Exception):  # pydantic frozen
        p.hourly_usd = Decimal("2.99")


def test_scraped_price_requires_decimal_for_price():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ScrapedPrice(
            provider_slug="x", gpu_slug_hint="x", tier="x",
            hourly_usd="1.99",     # should be Decimal
            region="", available=True, raw={},
        )
```

**GREEN.** `pricing/scrapers/__init__.py` empty; `pricing/scrapers/base.py`:

```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ScrapedPrice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_slug: str         # e.g. "runpod", maps to Provider.slug
    gpu_slug_hint: str         # vendor's name, mapped to our GPU.slug by scraper
    tier: str                  # "community", "secure", "reserved-1yr", etc.
    region: str                # blank if not applicable
    hourly_usd: Decimal
    available: bool
    raw: dict                  # full raw response payload; goes into PricingSnapshot.raw_payload
```

Test → 2 passing.

---

### M04.T02 — GPU mapping table

RunPod uses display names like `"NVIDIA H100 SXM"`; we use slugs like `"nvidia-h100-sxm-80"`. Hard-code the mapping in the scraper module.

**RED.** `pricing/tests/test_runpod_mapping.py`:

```python
import pytest
from pricing.scrapers.runpod import RUNPOD_GPU_MAP, map_runpod_gpu


def test_map_runpod_h100_sxm():
    assert map_runpod_gpu("NVIDIA H100 SXM") == "nvidia-h100-sxm-80"


def test_map_unknown_gpu_returns_none():
    assert map_runpod_gpu("UnobtaniumGPU 9000") is None
```

**GREEN.** `pricing/scrapers/runpod.py`:

```python
RUNPOD_GPU_MAP: dict[str, str] = {
    "NVIDIA H100 SXM": "nvidia-h100-sxm-80",
    "NVIDIA H100 PCIe": "nvidia-h100-pcie-80",
    "NVIDIA H200 SXM": "nvidia-h200",
    "NVIDIA A100 SXM 80GB": "nvidia-a100-sxm-80",
    "NVIDIA A100 SXM 40GB": "nvidia-a100-sxm-40",
    "NVIDIA L40S": "nvidia-l40s",
    "NVIDIA L4": "nvidia-l4",
    "NVIDIA RTX 4090": "nvidia-rtx-4090",
    "NVIDIA RTX 6000 Ada": "nvidia-rtx-6000-ada",
    "NVIDIA B200": "nvidia-b200",
    "AMD Instinct MI300X": "amd-mi300x",
}


def map_runpod_gpu(display_name: str) -> str | None:
    return RUNPOD_GPU_MAP.get(display_name)
```

Test → 2 passing.

---

### M04.T03 — Parser: GraphQL response → list[ScrapedPrice]

Pure function. Network call is in a separate function (T04) that this one composes with. Makes testing trivial.

**RED.** `pricing/tests/test_runpod_parser.py`:

```python
import json
from decimal import Decimal
from pathlib import Path

import pytest

from pricing.scrapers.runpod import parse_runpod_response

FIXTURE = Path(__file__).parent / "fixtures" / "runpod_gputypes_response.json"


def test_parse_yields_community_and_secure_tiers():
    payload = json.loads(FIXTURE.read_text())
    prices = parse_runpod_response(payload)
    tiers = {p.tier for p in prices if p.gpu_slug_hint == "NVIDIA H100 SXM"}
    assert "community" in tiers
    assert "secure" in tiers


def test_parse_yields_reserved_tiers_when_present():
    payload = json.loads(FIXTURE.read_text())
    prices = parse_runpod_response(payload)
    tiers = {p.tier for p in prices if p.gpu_slug_hint == "NVIDIA H100 SXM"}
    assert "reserved-1mo" in tiers
    assert "reserved-1yr" in tiers


def test_parse_drops_unmapped_gpus():
    """If RunPod adds a new GPU we don't have in RUNPOD_GPU_MAP, skip it
    (logged at info level — not a failure)."""
    payload = {
        "data": {
            "gpuTypes": [
                {"displayName": "Unknown GPU", "communityPrice": 1.0, "securePrice": 2.0,
                 "memoryInGb": 80}
            ]
        }
    }
    prices = parse_runpod_response(payload)
    # The "Unknown GPU" entry should produce no prices since we can't map it.
    assert len(prices) == 0


def test_parse_returns_decimals_not_floats():
    payload = json.loads(FIXTURE.read_text())
    prices = parse_runpod_response(payload)
    assert all(isinstance(p.hourly_usd, Decimal) for p in prices)
```

Create the fixture: `pricing/tests/fixtures/runpod_gputypes_response.json`. Use this as a minimal stub (real fixture should have richer data):

```json
{
  "data": {
    "gpuTypes": [
      {
        "displayName": "NVIDIA H100 SXM",
        "memoryInGb": 80,
        "communityPrice": 1.99,
        "securePrice": 2.69,
        "communitySpotPrice": 0.99,
        "secureSpotPrice": 1.49,
        "oneMonthPrice": 1.79,
        "threeMonthPrice": 1.69,
        "sixMonthPrice": 1.59,
        "oneYearPrice": 1.49
      },
      {
        "displayName": "NVIDIA RTX 4090",
        "memoryInGb": 24,
        "communityPrice": 0.39,
        "securePrice": 0.69,
        "communitySpotPrice": 0.19,
        "secureSpotPrice": 0.39,
        "oneMonthPrice": null,
        "threeMonthPrice": null,
        "sixMonthPrice": null,
        "oneYearPrice": null
      }
    ]
  }
}
```

**GREEN.** Append to `pricing/scrapers/runpod.py`:

```python
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from pricing.scrapers.base import ScrapedPrice

logger = logging.getLogger("pricing.scrapers.runpod")


# Map RunPod field name → our tier slug
_TIER_FIELDS: dict[str, str] = {
    "communityPrice":      "community",
    "securePrice":         "secure",
    "communitySpotPrice":  "community-spot",
    "secureSpotPrice":     "secure-spot",
    "oneMonthPrice":       "reserved-1mo",
    "threeMonthPrice":     "reserved-3mo",
    "sixMonthPrice":       "reserved-6mo",
    "oneYearPrice":        "reserved-1yr",
}


def parse_runpod_response(payload: dict[str, Any]) -> list[ScrapedPrice]:
    out: list[ScrapedPrice] = []
    for gpu in payload.get("data", {}).get("gpuTypes", []):
        display = gpu.get("displayName")
        gpu_slug = map_runpod_gpu(display)
        if gpu_slug is None:
            logger.info("runpod scraper skipping unmapped gpu: %s", display)
            continue
        for field, tier in _TIER_FIELDS.items():
            raw_price = gpu.get(field)
            if raw_price is None:
                continue
            out.append(ScrapedPrice(
                provider_slug="runpod",
                gpu_slug_hint=display,
                tier=tier,
                region="",
                hourly_usd=Decimal(str(raw_price)),
                available=True,
                raw=gpu,
            ))
    return out
```

Test → 4 passing.

---

### M04.T04 — Network call wrapper

Single function that issues the GraphQL POST and returns the parsed JSON. Wrapped in `tenacity` retry. **Doesn't get tested with real network in CI** — only the parser is unit-tested. Manual smoke verification by running the management command in T07.

**Implementation** in `pricing/scrapers/runpod.py`:

```python
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

_RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"

_GPU_TYPES_QUERY = """
query GpuTypes {
  gpuTypes {
    id
    displayName
    memoryInGb
    communityPrice
    securePrice
    communitySpotPrice
    secureSpotPrice
    oneMonthPrice
    threeMonthPrice
    sixMonthPrice
    oneYearPrice
  }
}
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_runpod_gputypes() -> dict[str, Any]:
    """Call the RunPod GraphQL API. Public schema, no auth required."""
    response = httpx.post(
        _RUNPOD_GRAPHQL_URL,
        json={"query": _GPU_TYPES_QUERY},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def scrape() -> list[ScrapedPrice]:
    """Top-level entry point. Calls API, parses, returns prices."""
    return parse_runpod_response(fetch_runpod_gputypes())
```

Add `httpx` to `pyproject.toml` dependencies.

No new tests; manual smoke in T07.

---

### M04.T05 — `scrape_runner` orchestrator

The orchestrator takes a list of `ScrapedPrice` and persists them as `PricingSnapshot` rows in one transaction. Single source of DB-touching logic; all scrapers go through it.

**RED.** `pricing/tests/test_scrape_runner.py`:

```python
from decimal import Decimal

import pytest
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider
from pricing.scrapers.base import ScrapedPrice
from pricing.services.scrape_runner import persist_prices


@pytest.mark.django_db
def test_persist_prices_creates_snapshots():
    provider = Provider.objects.create(
        slug="runpod", display_name="RunPod",
        provider_type="cloud", data_source_tier="realtime_api",
    )
    h100 = GPUFactory(slug="nvidia-h100-sxm-80")

    prices = [ScrapedPrice(
        provider_slug="runpod", gpu_slug_hint="NVIDIA H100 SXM",
        tier="community", hourly_usd=Decimal("1.99"),
        region="", available=True, raw={"displayName": "NVIDIA H100 SXM"},
    )]
    n = persist_prices(prices, gpu_slug_resolver=lambda hint: "nvidia-h100-sxm-80")
    assert n == 1
    assert PricingSnapshot.objects.count() == 1
    snap = PricingSnapshot.objects.get()
    assert snap.hourly_usd == Decimal("1.99")
    assert snap.tier == "community"
    assert snap.scraped_at.tzinfo is not None


@pytest.mark.django_db
def test_persist_prices_skips_unmapped_gpu():
    Provider.objects.create(
        slug="runpod", display_name="RunPod",
        provider_type="cloud", data_source_tier="realtime_api",
    )
    prices = [ScrapedPrice(
        provider_slug="runpod", gpu_slug_hint="UnknownGPU",
        tier="community", hourly_usd=Decimal("1.99"),
        region="", available=True, raw={},
    )]
    n = persist_prices(prices, gpu_slug_resolver=lambda hint: None)
    assert n == 0


@pytest.mark.django_db
def test_persist_prices_rejects_missing_provider():
    """If the Provider doesn't exist, raise loudly — don't silently drop."""
    prices = [ScrapedPrice(
        provider_slug="never-seen", gpu_slug_hint="x", tier="x",
        hourly_usd=Decimal("1"), region="", available=True, raw={},
    )]
    with pytest.raises(Provider.DoesNotExist):
        persist_prices(prices, gpu_slug_resolver=lambda hint: None)
```

**GREEN.** `pricing/services/__init__.py` empty; `pricing/services/scrape_runner.py`:

```python
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from catalog.models import GPU
from pricing.models import PricingSnapshot, Provider
from pricing.scrapers.base import ScrapedPrice

logger = logging.getLogger("pricing.scrape_runner")


@transaction.atomic
def persist_prices(
    prices: Iterable[ScrapedPrice],
    *,
    gpu_slug_resolver: Callable[[str], str | None],
) -> int:
    """Persist a batch of scraped prices as PricingSnapshot rows.

    Returns the number of rows actually written. Unmapped GPUs are
    skipped (with info log). Missing Provider raises (configuration error).
    """
    now = timezone.now()
    providers_cache: dict[str, Provider] = {}
    gpus_cache: dict[str, GPU] = {}
    written = 0

    for price in prices:
        if price.provider_slug not in providers_cache:
            providers_cache[price.provider_slug] = Provider.objects.get(slug=price.provider_slug)
        provider = providers_cache[price.provider_slug]

        gpu_slug = gpu_slug_resolver(price.gpu_slug_hint)
        if gpu_slug is None:
            logger.info("dropping price for unmapped gpu hint: %s", price.gpu_slug_hint)
            continue

        if gpu_slug not in gpus_cache:
            try:
                gpus_cache[gpu_slug] = GPU.objects.get(slug=gpu_slug)
            except GPU.DoesNotExist:
                logger.error("gpu mapping returned unknown slug: %s", gpu_slug)
                continue
        gpu = gpus_cache[gpu_slug]

        PricingSnapshot.objects.create(
            provider=provider,
            gpu=gpu,
            tier=price.tier,
            region=price.region,
            hourly_usd=price.hourly_usd,
            available=price.available,
            scraped_at=now,
            raw_payload=price.raw,
        )
        written += 1

    logger.info("persisted %d snapshots for providers=%s",
                written, sorted(providers_cache.keys()))
    return written
```

Test → 3 passing.

---

### M04.T06 — `scrape_pricing` management command

**RED.** `pricing/tests/test_scrape_pricing_command.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command

from catalog.tests.factories import GPUFactory
from pricing.models import PricingSnapshot, Provider


@pytest.mark.django_db
def test_scrape_pricing_runpod_uses_fixture(monkeypatch):
    """Patch the network call to return a fixture instead of hitting real API."""
    Provider.objects.create(
        slug="runpod", display_name="RunPod",
        provider_type="cloud", data_source_tier="realtime_api",
    )
    GPUFactory(slug="nvidia-h100-sxm-80")
    GPUFactory(slug="nvidia-rtx-4090")    # mapped from RUNPOD_GPU_MAP

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "runpod_gputypes_response.json").read_text()
    )
    with patch("pricing.scrapers.runpod.fetch_runpod_gputypes", return_value=fixture):
        call_command("scrape_pricing", "--provider", "runpod")

    # Should have written snapshots for both GPUs across all tiers with non-null prices
    h100_snaps = PricingSnapshot.objects.filter(gpu__slug="nvidia-h100-sxm-80")
    assert h100_snaps.count() == 8     # 4 on-demand/spot + 4 reserved tiers
    tiers = set(h100_snaps.values_list("tier", flat=True))
    assert "reserved-1yr" in tiers
    assert "community" in tiers
```

**GREEN.** `pricing/management/commands/__init__.py` empty; `pricing/management/commands/scrape_pricing.py`:

```python
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from pricing.scrapers import runpod
from pricing.services.scrape_runner import persist_prices


_SCRAPERS = {
    "runpod": (runpod.scrape, runpod.map_runpod_gpu),
    # M05/M05.5 will add more here
}


class Command(BaseCommand):
    help = "Run a provider scraper and persist snapshots."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--provider", required=True, choices=sorted(_SCRAPERS))

    def handle(self, *args, **options) -> None:
        scrape_fn, resolver = _SCRAPERS[options["provider"]]
        prices = scrape_fn()
        n = persist_prices(prices, gpu_slug_resolver=resolver)
        self.stdout.write(self.style.SUCCESS(f"persisted {n} snapshots"))
```

Test → 1 passing.

---

### M04.T07 — Celery task

`pricing/tasks.py`:

```python
from __future__ import annotations

import logging

from celery import shared_task

from pricing.scrapers import runpod
from pricing.services.scrape_runner import persist_prices

logger = logging.getLogger("pricing.tasks")


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def scrape_runpod(self) -> int:
    try:
        return persist_prices(runpod.scrape(), gpu_slug_resolver=runpod.map_runpod_gpu)
    except Exception as exc:
        logger.exception("runpod scrape failed")
        raise self.retry(exc=exc)
```

Test:

```python
# pricing/tests/test_tasks.py
from unittest.mock import patch
import pytest

from pricing.tasks import scrape_runpod


@pytest.mark.django_db
def test_scrape_runpod_task_calls_scrape_runner():
    with patch("pricing.tasks.persist_prices", return_value=5) as m:
        with patch("pricing.tasks.runpod.scrape", return_value=[]):
            result = scrape_runpod.apply().get()
            assert result == 5
            m.assert_called_once()
```

Wire into `CELERY_BEAT_SCHEDULE` in `settings.py`:

```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "scrape-runpod-hourly": {
        "task": "pricing.tasks.scrape_runpod",
        "schedule": crontab(minute=3),
    },
}
```

---

### M04.T08 — Manual smoke verification

Run against the real RunPod API once, manually:

```bash
python manage.py shell -c "
from pricing.scrapers.runpod import scrape
prices = scrape()
print(f'got {len(prices)} prices')
print(prices[:3])
"
```

Expected: at least 8 prices for nvidia-h100-sxm-80 (4 on-demand + 4 reserved tiers). Document any new GPU display names that show up in RunPod's response and aren't in `RUNPOD_GPU_MAP` — extend the map and commit.

---

## Milestone verification

```bash
# Seed catalog first
python manage.py seed_catalog

# Ensure Provider row exists
python manage.py shell -c "
from pricing.models import Provider
Provider.objects.update_or_create(
    slug='runpod', defaults={
        'display_name': 'RunPod', 'provider_type': 'cloud',
        'data_source_tier': 'realtime_api',
        'pricing_url': 'https://www.runpod.io/pricing',
        'has_api': True,
        'api_endpoint': 'https://api.runpod.io/graphql',
    }
)
"

# Run the scraper (real network)
python manage.py scrape_pricing --provider runpod

# Confirm snapshots landed
python manage.py shell -c "
from pricing.models import PricingSnapshot
print(PricingSnapshot.objects.count(), 'snapshots')
print(set(PricingSnapshot.objects.values_list('tier', flat=True)))
"

pytest pricing/ tests/ -q
ruff check && ruff format --check
mypy catalog pricing
```

Mark M04 done. Stop.

---

## Out of scope for M04

- Other providers' scrapers. M05 / M05.5.
- Cost-cell view. M06.
- Continuous aggregates. M07.
- On-prem or reserved-cloud snapshots. M08 / M09.
