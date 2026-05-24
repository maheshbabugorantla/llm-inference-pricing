# CLAUDE.md

You are working on **llm-inference-pricing** — a Django backend that prices LLM coding-model inference across four deployment modes (cloud on-demand, reserved cloud, on-prem TCO, on-prem marginal) and joins them with vLLM/SGLang throughput benchmarks to produce `$/M input tokens` and `$/M output tokens` cost cells.

## Always read these first

1. **`docs/PRD.md`** — the source of truth for *what* and *why*. Twelve ADRs, twelve entities, ten milestones. If anything in this spec disagrees with the PRD, the PRD wins; flag the disagreement and stop.
2. **`spec/SHARED.md`** — domain language, invariants, code conventions. Read every session. If you find yourself struggling to name something, the term you need is probably already there.
3. **`spec/INDEX.md`** — the milestone map and how to work this spec.
4. **`spec/TESTING.md`** — required testing philosophy. Read before writing any test. Tests must simulate real business scenarios under ideal and non-ideal conditions, not just pass coverage metrics.

## Working rules

- **TDD is non-negotiable.** Every behavior change goes through RED → GREEN → REFACTOR. Write the test, run it, watch it fail with the *right* error, then write the minimal code to pass. No skipping the red step.
- **One milestone at a time, one task at a time.** Milestones depend on each other (see `spec/INDEX.md`). Tasks inside a milestone are ordered; don't skip ahead.
- **Vertical slices.** Each task should ship a thin end-to-end capability, not a horizontal layer. "GPU model + admin + seed + tests for one GPU" is a slice. "All models in models.py" is not.
- **Run the verification block at the end of every task** (each task spec has one). If it doesn't pass, the task isn't done.
- **Commit at task boundaries**, not at milestone boundaries. Commit message format: `M01.T03: <imperative description>`.
- **When uncertain, search the PRD first**, then `SHARED.md`, then ask. Don't guess at domain semantics.
- **If a SKILL.md exists at `/mnt/skills/public/<name>/SKILL.md`** and is relevant to the task, read it before writing code.

## Stack invariants

- Python **3.12+**, Django **5.x**, Postgres **16+** with the **TimescaleDB** extension installed.
- **Django TestCase / `python manage.py test`** for tests. `coverage` for reporting. No pytest.
- **pydantic v2** for YAML schema validation and scraper return types.
- **Celery** + **Celery Beat** for scheduling, with **Redis** as broker.
- **ruff** for lint + format (`ruff check` + `ruff format`). No black, no isort.
- **mypy** strict on `catalog/`, `pricing/`, `seeds/`. Type errors block CI.
- **Decimal** for any money or rate field — never `float`.
- **timezone-aware datetimes** everywhere. `timezone.now()`, never `datetime.utcnow()`.

## Definition of done (per task)

A task is done when **all** of the following are true:

```
python manage.py test <app>.tests.<module> -v 0  # targeted tests, all green
python manage.py test catalog pricing tests -v 0 # full suite still green
ruff check && ruff format --check                # clean
mypy catalog pricing                             # clean
python manage.py makemigrations --check          # no pending migrations
```

Plus the task's specific verification block at the bottom of its spec file.

## Never do these

- Don't introduce a new third-party dependency without an ADR. If it feels needed, write the ADR first under `docs/adr/` and stop for review.
- Don't use `float` for prices, hourly rates, or token counts in cost math. `Decimal` only.
- Don't catch `Exception` broadly. Catch specific exceptions or let them propagate.
- Don't put business logic on Django model classes. Logic lives in `<app>/services/` modules as pure functions taking model instances.
- Don't modify migrations after they're applied to any env. Add new migrations instead.
- Don't write tests that hit the real network. Cloud scrapers use recorded fixtures (see `M04` for the pattern).
- Don't silently drop data on YAML seed errors. Fail loudly with a clear message identifying the bad row.

## When you finish a milestone

1. Run the full verification block at the bottom of the milestone spec.
2. Update `spec/INDEX.md` to mark the milestone done (change `[ ]` → `[x]`).
3. If you learned new domain language or discovered a worth-documenting decision, update `spec/SHARED.md` (for terms) or write a new ADR under `docs/adr/` (for decisions).
4. Stop. Don't auto-advance to the next milestone. The user picks when to start the next one.

## When you hit something the spec doesn't cover

Stop and write a short note in `spec/QUESTIONS.md` (create if it doesn't exist). Describe the gap, the options you considered, and what you'd choose if forced. Then continue with the most defensible interpretation and flag it in your commit message. The user reviews and resolves.
