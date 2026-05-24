# LLM Inference Pricing

A Django backend that prices LLM coding-model inference across four deployment modes and joins them with vLLM/SGLang throughput benchmarks to produce `$/M input tokens` and `$/M output tokens` cost cells.

## What it does

| Deployment mode | What it prices |
|---|---|
| **Cloud on-demand** | RunPod, Lambda Labs, Vast.ai, Nebius, AWS, GCP, Azure — scraped daily |
| **Reserved cloud** | Lambda Reserved, AWS RIs/Capacity Blocks, GCP CUDs, Azure RIs, CoreWeave, Crusoe, Nebius, OCI, RunPod — committed terms (6-month to 3-year, plus short-duration capacity blocks) |
| **On-prem TCO** | Capex amortized + power × PUE + colo + ops |
| **On-prem marginal** | Opex only, capex sunk |

All four modes write to a single `PricingSnapshot` hypertable (TimescaleDB). A `current_cost_cells` materialized view joins the latest snapshot for each provider/GPU/tier combination with benchmark throughput data to produce uniform cost cells across the full operating-point grid (`{batch=1,8,32,64} × {ctx=4k,32k,128k}`).

## Architecture

```
GitHub Actions (daily)
  └─ dump_pricing --provider all
       └─ data/pricing/<provider>.json   ← committed back to main

Developer / CI
  └─ load_pricing --provider all
       └─ PricingSnapshot (TimescaleDB)
            └─ current_cost_cells (materialized view)

On-prem & reserved-cloud generators (run on seed / deployment save)
  └─ seed_on_prem / seed_reserved
       └─ OnPremDeployment / ReservedCloudDeployment
            └─ PricingSnapshot rows (synthetic, tier = "tco"/"marginal" or "reserved-<slug>"/"reserved-marginal-<slug>")
                 └─ current_cost_cells (same view, same schema)
```

Scrapers run on GitHub Actions runners — no rate-limiting, no container egress issues. The JSON artifacts are version-controlled so every scrape is auditable as a plain git diff.

On-prem and reserved-cloud pricing is computed from YAML-curated deployment configs rather than scraped, and flows into the same `PricingSnapshot` table so cost comparison is uniform across all modes.

## Tech stack

- **Python 3.12**, **Django 5.x**, **Postgres 16 + TimescaleDB**
- **Celery + Redis** for background scheduling
- **Pydantic v2** for scraper return types, artifact schema validation, and YAML seed validation
- **httpx + tenacity** for HTTP scraping with retries
- **BeautifulSoup4** for HTML parsing
- **Django TestCase / coverage** · **ruff** · **mypy strict** · **pre-commit**
- **uv** for dependency management

## Quickstart

### Prerequisites

- Docker Desktop
- Python 3.12 (`pyenv` or `uv` recommended)
- `uv` — `pip install uv` or see [docs.astral.sh/uv](https://docs.astral.sh/uv/)

### 1 — Start the local services

```bash
docker compose up -d db redis
```

Starts TimescaleDB on port **5434** (mapped to avoid conflicts with any existing Postgres on 5432) and Redis on 6379.

### 2 — Install dependencies

```bash
uv sync --group dev
source .venv/bin/activate
```

### 3 — Bootstrap the database

```bash
python manage.py migrate
python manage.py seed_catalog      # GPUs, Models, Quantizations, Benchmarks
python manage.py seed_providers    # Provider rows (cloud providers)
python manage.py seed_on_prem      # HardwareSKUs + OnPremDeployments → snapshots
python manage.py seed_reserved     # ReservedCapacityProducts + ReservedCloudDeployments → snapshots
```

### 4 — Load pricing data

The daily GitHub Actions workflow keeps `data/pricing/*.json` up to date. Load the committed artifacts into your local DB:

```bash
python manage.py load_pricing --provider all
```

Or scrape live (requires network access to provider sites):

```bash
python manage.py dump_pricing --provider all     # → data/pricing/*.json
python manage.py load_pricing --provider all     # → PricingSnapshot rows
```

### 5 — Run tests

```bash
python manage.py test catalog pricing --noinput -v 0
```

Or with coverage:

```bash
coverage run manage.py test catalog pricing --noinput -v 0 && coverage report
```

All tests are fixture-driven — no live network calls.

## Management commands

| Command | Description |
|---|---|
| `seed_catalog` | Loads GPUs, Models, Quantizations, BenchmarkPoints from `seeds/` |
| `seed_providers` | Creates Provider rows from `seeds/providers.yaml` |
| `seed_on_prem` | Loads HardwareSKUs and OnPremDeployments; regenerates on-prem snapshots |
| `seed_reserved` | Loads ReservedCapacityProducts and ReservedCloudDeployments; regenerates reserved-cloud snapshots |
| `scrape_pricing --provider <slug>` | Live-scrapes one provider and persists to DB immediately |
| `dump_pricing --provider <slug\|all>` | Fetches live prices and writes `data/pricing/<slug>.json` (no DB) |
| `load_pricing --provider <slug\|all>` | Reads `data/pricing/<slug>.json` and persists to DB |

`dump_pricing` and `load_pricing` are deliberately separate: the GitHub Actions runner does the network-heavy scraping; a local developer just loads the committed artifacts without touching any provider site.

## Pricing data pipeline

```
.github/workflows/scrape-pricing.yml
  ├─ triggers: daily 06:17 UTC + workflow_dispatch
  ├─ python manage.py dump_pricing --provider all
  ├─ if data/pricing/ changed → git commit + push to main
  └─ exits non-zero on partial failure (failed provider's JSON is preserved)

.github/workflows/canary.yml
  ├─ triggers: weekly
  └─ runs scrapers against live sites to detect parser drift (ParserDriftError)
```

Each `data/pricing/<slug>.json` is a Pydantic-validated `PricingArtifact`:

```jsonc
{
  "schema_version": 1,
  "provider_slug": "runpod",
  "scraped_at": "2026-05-18T06:17:42+00:00",
  "source_url": "https://api.runpod.io/graphql",
  "scraper_version": "abc1234",
  "prices": [
    { "gpu_slug_hint": "H100 SXM", "tier": "on_demand", "hourly_usd": "2.79", ... }
  ]
}
```

## Supported providers

### Cloud on-demand (scraped)

| Provider | Slug | Source | Tiers |
|---|---|---|---|
| RunPod | `runpod` | GraphQL API | `community`, `secure`, `community-spot`, `secure-spot`, `reserved-1mo`, `reserved-3mo`, `reserved-6mo`, `reserved-1yr` |
| Lambda Labs | `lambda` | lambda.ai page | `on_demand` |
| Vast.ai | `vast` | REST API | `on_demand` |
| Nebius | `nebius` | nebius.com page | `on_demand`, `preemptible` |
| AWS | `aws` | pricing JSON API | `on_demand` |
| GCP | `gcp` | cloud billing API | `on_demand` |
| Azure | `azure` | retail prices API | `on_demand` |

### Reserved cloud (YAML-curated)

Committed pricing in `seeds/reserved/products/`. Each product maps to a `ReservedCapacityProduct` with cadence (`all_upfront`, `partial_upfront`, `no_upfront`, `capacity_block`) and term. A `ReservedCloudDeployment` pairs a product with expected utilization and optional negotiated overrides.

| Provider | Products seeded |
|---|---|
| Lambda Labs | H100 1-yr all-upfront |
| AWS | p4d 3-yr all-upfront; p5 14-day capacity block; p5e 7-day capacity block |
| GCP | A3 CUD 1-yr and 3-yr no-upfront |
| Azure | ND H100 RI 1-yr and 3-yr partial-upfront |
| CoreWeave | H100 reserved 1-yr |
| Crusoe | MI300x reserved 3-yr |
| Nebius | H100 reserved 6-month |
| OCI | BM.GPU.H100 1-yr |
| RunPod | H100 reserved 1-yr |

### On-prem (YAML-curated)

Hardware configs in `seeds/hardware/`; deployment configs in `seeds/deployments/`. The TCO generator computes amortized capex, power, colo, and ops costs; the marginal generator omits capex.

| SKU | Description |
|---|---|
| `dell-r760xa-8xh100` | Dell R760xa with 8× H100 SXM |
| `lambda-echelon-4xh100` | Lambda Echelon with 4× H100 |
| `supermicro-8xmi300x` | Supermicro with 8× MI300X |

## Project layout

```
.
├── catalog/                        # GPUs, Models, Quantizations, Benchmarks
├── pricing/
│   ├── scrapers/
│   │   ├── __init__.py             # SCRAPERS registry (ScraperEntry NamedTuple)
│   │   ├── base.py                 # ScrapedPrice, ParserDriftError
│   │   ├── runpod.py
│   │   ├── lambda_labs.py
│   │   ├── vast.py
│   │   ├── nebius.py
│   │   ├── aws.py
│   │   ├── gcp.py
│   │   └── azure.py
│   ├── generators/
│   │   ├── on_prem.py              # TCO + marginal snapshot generator
│   │   └── reserved_cloud.py      # committed + marginal snapshot generator
│   ├── services/
│   │   ├── scrape_runner.py        # persist_prices() orchestrator
│   │   ├── pricing_artifacts.py    # PricingArtifact schema + read/write
│   │   ├── cost.py                 # current_cost_cells query helpers
│   │   ├── on_prem_cost.py         # PRD §7.3 on-prem TCO formula
│   │   ├── reserved_cloud_cost.py  # PRD §7.4 reserved-cloud cost formula
│   │   ├── reserved_cloud_validate.py  # payment-cadence model validation
│   │   └── seed.py                 # Pydantic YAML schemas for all seed types
│   └── management/commands/
│       ├── dump_pricing.py
│       ├── load_pricing.py
│       ├── scrape_pricing.py
│       ├── seed_providers.py
│       ├── seed_on_prem.py
│       └── seed_reserved.py
├── seeds/
│   ├── gpus.yaml
│   ├── models/
│   ├── quantizations.yaml
│   ├── benchmarks/
│   ├── providers.yaml
│   ├── hardware/                   # HardwareSKU YAML files
│   ├── deployments/                # OnPremDeployment YAML files
│   └── reserved/
│       ├── products/               # ReservedCapacityProduct YAML files
│       └── deployments/            # ReservedCloudDeployment YAML files
├── data/pricing/                   # JSON artifacts committed by CI
├── .github/workflows/
│   ├── ci.yml                      # Lint + typecheck + tests on push/PR
│   ├── scrape-pricing.yml          # Daily scrape → commit artifacts
│   └── canary.yml                  # Weekly scraper drift detection
├── spec/                           # Per-milestone implementation specs
└── docs/
    ├── PRD.md                      # Full product requirements and ADRs
    └── DOCKER.md                   # Running Claude Code safely in Docker
```

## Development

### Quality gate

```bash
ruff check && ruff format --check              # lint + format
mypy catalog pricing                           # type check
python manage.py test catalog pricing -v 0    # full test suite
python manage.py makemigrations --check        # no pending migrations
```

### Pre-commit hooks

```bash
pre-commit install          # one-time setup
pre-commit run --all-files  # run manually
```

Hooks run `ruff check`, `ruff format`, and `mypy` on every commit.

### Running tests in Docker

```bash
docker compose -f docker-compose.yml -f compose.claude.yml run --rm \
  --entrypoint /bin/bash \
  -e DB_HOST=db -e DB_PORT=5432 -e DB_NAME=pricing \
  -e DB_USER=postgres -e DB_PASSWORD=postgres \
  claude -c "cd /workspace && .venv-linux/bin/python manage.py test catalog pricing -v 0"
```

See [`docs/DOCKER.md`](docs/DOCKER.md) for how to run Claude Code with `--dangerously-skip-permissions` safely inside a container with an egress firewall.

## Milestone progress

| Milestone | Description | Status |
|---|---|---|
| M00 | Bootstrap — project init, tooling, Docker, CI | ✅ |
| M01 | Catalog foundations — GPU, Model, Quantization, `seed_catalog` | ✅ |
| M02 | Benchmarks + fit calculation | ✅ |
| M03 | `pricing` app + Provider + PricingSnapshot + TimescaleDB | ✅ |
| M04 | RunPod Tier 1 scraper (on-demand + reserved tiers) | ✅ |
| M05 | Tier 2 page scrapers (Lambda, Vast, Nebius) | ✅ |
| M05.6 | Pricing data pipeline (GH Actions + JSON artifacts) | ✅ |
| M05.5 | Hyperscaler scrapers (AWS, GCP, Azure) | ✅ |
| M06 | `current_cost_cells` materialized view + cost service | ✅ |
| M07 | Ops hardening (TimescaleDB retention, Sentry, canary CI) | ✅ |
| M08 | On-prem (`HardwareSKU`, `OnPremDeployment`, TCO generator) | ✅ |
| M09 | Reserved cloud (`ReservedCapacityProduct`, payment cadences) | ✅ |
| M10 | ComputePrices.com drift detection *(optional)* | 🔲 |
| M11 | Test quality uplift — pytest → Django TestCase, coverage CI | ✅ |

Full specs in [`spec/INDEX.md`](spec/INDEX.md).

## Documentation

| Document | Description |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | Full product requirements, data model, 12 ADRs |
| [`docs/DOCKER.md`](docs/DOCKER.md) | Docker sandbox setup for Claude Code sessions |
| [`spec/INDEX.md`](spec/INDEX.md) | Milestone map and progress |
| [`spec/SHARED.md`](spec/SHARED.md) | Domain language and code conventions |
| [`CLAUDE.md`](CLAUDE.md) | Instructions for Claude Code — TDD rules, stack invariants |
