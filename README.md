# LLM Inference Pricing

A Django backend that tracks GPU cloud pricing across four deployment modes and joins it with vLLM/SGLang throughput benchmarks to produce `$/M input tokens` and `$/M output tokens` cost cells.

## What it does

| Deployment mode | What it prices |
|---|---|
| **Cloud on-demand** | RunPod, Lambda Labs, Vast.ai, Nebius — scraped daily |
| **Reserved cloud** | Lambda Reserved, Nebius, AWS/GCP/Azure RIs — annual commitments |
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
```

Scrapers run on GitHub Actions runners — no rate-limiting, no container egress issues. The JSON artifacts are version-controlled so every scrape is auditable as a plain git diff.

## Tech stack

- **Python 3.12**, **Django 5.x**, **Postgres 16 + TimescaleDB**
- **Celery + Redis** for background scheduling
- **Pydantic v2** for scraper return types and artifact schema validation
- **httpx + tenacity** for HTTP scraping with retries
- **BeautifulSoup4** for HTML parsing
- **pytest + pytest-django** · **ruff** · **mypy strict** · **pre-commit**
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
uv pip install -e ".[dev]"
```

### 3 — Bootstrap the database

```bash
python manage.py migrate
python manage.py seed_catalog      # GPUs, Models, Quantizations, Benchmarks
python manage.py seed_providers    # RunPod, Lambda, Vast, Nebius
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
pytest -q
```

All tests are fixture-driven — no live network calls.

## Management commands

| Command | Description |
|---|---|
| `seed_catalog` | Loads GPUs, Models, Quantizations, BenchmarkPoints from `seeds/` |
| `seed_providers` | Creates Provider rows from `seeds/providers.yaml` |
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

| Provider | Slug | Source | Tiers |
|---|---|---|---|
| RunPod | `runpod` | GraphQL API | `on_demand`, `community`, `secure`, `reserved-1yr`, `reserved-3yr` |
| Lambda Labs | `lambda` | lambda.ai page | `on_demand` |
| Vast.ai | `vast` | REST API | `on_demand` |
| Nebius | `nebius` | nebius.com page | `on_demand`, `preemptible` |

## Project layout

```
.
├── catalog/                      # GPUs, Models, Quantizations, Benchmarks
├── pricing/
│   ├── scrapers/
│   │   ├── __init__.py           # SCRAPERS registry (ScraperEntry NamedTuple)
│   │   ├── base.py               # ScrapedPrice, ParserDriftError
│   │   ├── runpod.py
│   │   ├── lambda_labs.py
│   │   ├── vast.py
│   │   └── nebius.py
│   ├── services/
│   │   ├── scrape_runner.py      # persist_prices() orchestrator
│   │   └── pricing_artifacts.py  # PricingArtifact schema + read/write
│   └── management/commands/
│       ├── dump_pricing.py
│       ├── load_pricing.py
│       └── scrape_pricing.py
├── seeds/                        # YAML seed data (GPUs, providers, etc.)
├── data/pricing/                 # JSON artifacts committed by CI
├── .github/workflows/
│   ├── ci.yml                    # Lint + typecheck + tests on push/PR
│   └── scrape-pricing.yml        # Daily scrape → commit artifacts
├── spec/                         # Per-milestone implementation specs
└── docs/
    ├── PRD.md                    # Full product requirements and ADRs
    └── DOCKER.md                 # Running Claude Code safely in Docker
```

## Development

### Quality gate

```bash
ruff check && ruff format --check   # lint + format
mypy catalog pricing                # type check
pytest -q                           # full test suite
python manage.py makemigrations --check  # no pending migrations
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
  claude -c "cd /workspace && .venv-linux/bin/pytest -q"
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
| M05.5 | Hyperscaler scrapers (AWS, GCP, Azure) | ⏳ deferred |
| M06 | `current_cost_cells` materialized view + cost service | 🔲 |
| M07 | Ops hardening (retention, Sentry, canary CI) | 🔲 |
| M08 | On-prem (`HardwareSKU`, `OnPremDeployment`, TCO generator) | 🔲 |
| M09 | Reserved cloud (`ReservedCapacityProduct`, payment cadences) | 🔲 |
| M10 | ComputePrices.com drift detection *(optional)* | 🔲 |

Full specs in [`spec/INDEX.md`](spec/INDEX.md).

## Documentation

| Document | Description |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | Full product requirements, data model, 12 ADRs |
| [`docs/DOCKER.md`](docs/DOCKER.md) | Docker sandbox setup for Claude Code sessions |
| [`spec/INDEX.md`](spec/INDEX.md) | Milestone map and progress |
| [`spec/SHARED.md`](spec/SHARED.md) | Domain language and code conventions |
| [`CLAUDE.md`](CLAUDE.md) | Instructions for Claude Code — TDD rules, stack invariants |
