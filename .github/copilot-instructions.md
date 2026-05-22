# Copilot Instructions — llm-inference-pricing

## Code review format

Every inline review comment **must** start with a severity label on its own line:

```
[SEVERITY: HIGH]
```
```
[SEVERITY: MEDIUM]
```
```
[SEVERITY: LOW]
```

### Severity definitions

| Level | Use for |
|-------|---------|
| **HIGH** | Bugs, security vulnerabilities, data loss risks, `ZeroDivisionError`/unguarded division, incorrect business logic that produces wrong cost output, non-atomic writes that can leave DB in inconsistent state |
| **MEDIUM** | Logic errors that don't crash but yield wrong results, missing validation at system boundaries (user input / external API), O(N²) complexity that degrades in production, unhandled exceptions that silently drop data |
| **LOW** | Style, naming, documentation, redundant code, minor improvements, nits |

Raise HIGH or MEDIUM **only** when the issue clearly violates correctness, security, or a stated invariant. Do not use HIGH/MEDIUM for subjective preferences or speculative future problems.

## Project-specific invariants (always flag HIGH if violated)

- `float` used for any price, rate, or token count — must be `Decimal`
- `datetime.utcnow()` — must be `timezone.now()`
- Business logic on a Django model class — must live in `<app>/services/`
- `Exception` caught broadly — catch specific exceptions or let propagate
- Pending migration produced by a model change — migrations must be committed
- Real network calls in tests — scrapers use recorded fixtures

## Stack context

Python 3.12 · Django 5.x · Postgres 16 + TimescaleDB · Celery + Redis  
pytest-django · pydantic v2 · ruff · mypy strict · Decimal for all money
