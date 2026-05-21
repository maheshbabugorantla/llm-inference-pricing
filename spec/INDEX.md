# spec/INDEX.md — Milestone map

## How to work this spec

1. Read `CLAUDE.md` at the project root every session.
2. Read `spec/SHARED.md` every session.
3. Read **the spec for exactly one milestone** before starting it. Read it fully before writing any code.
4. Inside a milestone, work tasks in order (`M0X.T01` → `M0X.T02` → …). Each task has a test plan, implementation plan, and verification block.
5. Use the TDD loop on every task: write the failing test, run it, watch it fail with the right error, write the minimum code to pass, refactor.
6. Commit at task boundaries. Format: `M01.T03: <imperative>`.
7. At milestone end, run the milestone's full verification block and update the checkbox below.

## Milestone progress

- [x] **M00** — Bootstrap (project init, tooling, Docker compose, CI) → `BOOTSTRAP.md`
- [x] **M01** — Catalog foundations (GPU, Model, Quantization, `seed_catalog`) → `M01-catalog-foundations.md`
- [x] **M02** — Benchmarks + fit calculation → `M02-benchmarks-and-fit.md`
- [x] **M03** — `pricing` app + Provider + PricingSnapshot + TimescaleDB → `M03-pricing-app-schema.md`
- [x] **M04** — RunPod Tier 1 scraper (on-demand + 4 reserved tiers in one call) → `M04-runpod-scraper.md`
- [x] **M05** — Tier 2 page scrapers (Lambda, Vast, Nebius) → `M05-tier2-page-scrapers.md`
- [x] **M05.6** — Pricing Data Pipeline (GH Actions + JSON artifacts) → `M05.6-pricing-data-pipeline.md`
- [x] **M05.5a** — GCP scraper (carved from M05.5; AWS+Azure deferred) → `M05.5a-gcp-scraper.md`
- [ ] **M05.5b** — Tier 1 hyperscaler scrapers (AWS, Azure) → `M05.5-hyperscaler-scrapers.md` *(deferred until after M06)*
- [x] **M06** — `current_cost_cells` materialized view + cost service → `M06-current-cost-cells.md`
- [ ] **M07** — Ops hardening (continuous aggregate, retention, Sentry, canary CI) → `M07-ops-hardening.md`
- [ ] **M08** — On-prem (`HardwareSKU`, `OnPremDeployment`, generator) → `M08-on-prem.md`
- [ ] **M09** — Reserved cloud (`ReservedCapacityProduct`, `ReservedCloudDeployment`, generator) → `M09-reserved-cloud.md`
- [ ] **M10** — *(Optional)* ComputePrices.com drift detection → `M10-drift-detection.md`

## Dependency graph

```
M00 (bootstrap)
 └─ M01 (catalog)
     └─ M02 (benchmarks + fit)
         └─ M03 (pricing schema)
             ├─ M04 (RunPod) ─┐
             ├─ M05 (Tier 2) ─┤
             │   └─ M05.6 (pipeline) ─┤
             └─ M05.5 (hyper) ─────────┤
                                       └─ M06 (cost cells)
                                   └─ M07 (ops)
                                       ├─ M08 (on-prem)
                                       │   └─ (back into M06 refresh)
                                       └─ M09 (reserved cloud)
                                           └─ M10 (drift detection, optional)
```

M04, M05, and M05.5 are parallelizable in theory but should be sequenced because M04 establishes the scraper pattern. Do M04 first, then M05 and M05.5 in either order.

## Estimated work

Rough estimates assuming a single Claude Code session per milestone, with the user reviewing/merging between:

| Milestone | Code lines | Tests | Real time |
|---|---|---|---|
| M00 | ~200 (config) | n/a | 1 session |
| M01 | ~400 | ~20 | 1–2 sessions |
| M02 | ~500 | ~30 | 2 sessions |
| M03 | ~300 | ~15 | 1 session |
| M04 | ~400 | ~20 | 1–2 sessions |
| M05 | ~600 (3 scrapers) | ~30 | 2 sessions |
| M05.5 | ~700 (3 hyperscalers) | ~30 | 2–3 sessions |
| M06 | ~300 + SQL | ~15 | 1 session |
| M07 | ~200 | ~10 | 1 session |
| M08 | ~500 | ~25 | 2 sessions |
| M09 | ~600 | ~30 | 2 sessions |
| M10 | ~200 | ~10 | 1 session |

Total: ~5,000 LOC, ~235 tests. M01–M03 are foundational; M04 onward should be faster once patterns are set.

## When a milestone is "done"

Each milestone file has a **Verification block** at the end. Run it. Update this file's checkbox only after the block passes cleanly. Then stop and wait for the user to start the next milestone — don't auto-advance.
