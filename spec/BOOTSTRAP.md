# M00 — Bootstrap

**Goal.** Stand up the Django project, Postgres+TimescaleDB+Redis via Docker Compose, pytest+pytest-django+ruff+mypy tooling, pre-commit hooks, and a minimal CI workflow. Nothing functional yet — just a green skeleton that downstream milestones build on.

**Depends on.** Nothing.

**Definition of done.** `pytest -q` runs, finds zero tests, and exits 0. `ruff check`, `ruff format --check`, and `mypy .` all pass on a project containing one trivial test file.

---

## Tasks

### M00.T01 — Initialize repo layout

Create the directory tree from `spec/SHARED.md` "Code conventions → Layout":

```
project_root/
├── manage.py
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── .gitignore
├── conftest.py
├── pricing_dashboard/
├── catalog/
├── pricing/
├── seeds/
├── docs/PRD.md (already there)
└── spec/ (already there)
```

`catalog/` and `pricing/` are empty for now (just `__init__.py` + `apps.py` + empty `migrations/__init__.py`). `seeds/` is empty.

**Verification.** `find . -type d | sort` matches the tree.

---

### M00.T02 — `pyproject.toml`

Single source of truth for dependencies, ruff, mypy, pytest config.

```toml
[project]
name = "llm-inference-pricing"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "django>=5.0,<6.0",
    "psycopg[binary]>=3.2",
    "celery[redis]>=5.4",
    "redis>=5.0",
    "pydantic>=2.5",
    "pyyaml>=6.0",
    "tenacity>=8.2",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-django>=4.8",
    "pytest-cov>=5.0",
    "factory-boy>=3.3",
    "ruff>=0.5",
    "mypy>=1.10",
    "django-stubs[compatible-mypy]>=5.0",
    "types-PyYAML",
    "pre-commit>=3.7",
]

[tool.ruff]
target-version = "py312"
line-length = 110

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "DJ", "TCH", "RUF"]
ignore = ["E501"]    # line length handled by formatter

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["mypy_django_plugin.main"]
exclude = ["migrations/"]

[tool.django-stubs]
django_settings_module = "pricing_dashboard.settings"

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "pricing_dashboard.settings"
python_files = ["test_*.py"]
addopts = "-ra --strict-markers"
```

**Verification.** `pip install -e ".[dev]"` succeeds.

---

### M00.T03 — Django project + settings

`pricing_dashboard/settings.py` configured for dev with environment-variable-driven Postgres connection:

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "catalog",
    "pricing",
]
USE_TZ = True
TIME_ZONE = "UTC"
```

Plus minimal `urls.py` and `wsgi.py`. No views yet.

**Verification.** `python manage.py check` exits 0.

---

### M00.T04 — Docker Compose

`docker-compose.yml` brings up:

- `db`: `timescale/timescaledb:latest-pg16` image, port 5432, healthcheck on `pg_isready`, volume for persistence.
- `redis`: `redis:7-alpine`, port 6379, healthcheck on `redis-cli ping`.

`.env.example` has `DB_*` and `REDIS_URL` placeholders. `.gitignore` excludes `.env`.

**Verification.**
```bash
docker compose up -d
docker compose exec db psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
docker compose exec db psql -U postgres -c "SELECT extversion FROM pg_extension WHERE extname='timescaledb';"
# expect a version string, not empty
```

---

### M00.T05 — pytest scaffold + the canary test

`conftest.py` at repo root configures pytest-django:

```python
import pytest

@pytest.fixture(autouse=True)
def _enable_db_access_for_all_tests(db):
    """Every test has DB access by default. Mark explicitly if not needed."""
    pass
```

Wait — that fixture forces DB use. Drop it; instead require explicit `@pytest.mark.django_db` on DB-touching tests (matches `SHARED.md` convention).

Replace with:

```python
# conftest.py
import django
from django.conf import settings


def pytest_configure(config):
    if not settings.configured:
        django.setup()
```

Then create one canary test in `catalog/tests/test_canary.py`:

```python
def test_canary():
    assert 1 + 1 == 2
```

**Verification.** `pytest -q` shows `1 passed`.

---

### M00.T06 — Celery scaffold (no tasks yet)

`pricing_dashboard/celery.py`:

```python
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pricing_dashboard.settings")
app = Celery("pricing_dashboard")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

Add to `settings.py`:

```python
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BEAT_SCHEDULE = {}    # populated in later milestones
```

`pricing_dashboard/__init__.py`:

```python
from .celery import app as celery_app
__all__ = ("celery_app",)
```

**Verification.** `celery -A pricing_dashboard inspect ping` returns at least one worker if a worker is running (skip this check if Celery worker isn't started locally; just verify import works: `python -c "from pricing_dashboard.celery import app; print(app)"`).

---

### M00.T07 — Pre-commit hooks

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        additional_dependencies: [django-stubs, types-PyYAML]
        args: [catalog, pricing]
```

**Verification.** `pre-commit install && pre-commit run --all-files` passes.

---

### M00.T08 — Minimal CI workflow

`.github/workflows/ci.yml` runs on push and PR:

- Services: `timescale/timescaledb:latest-pg16` + `redis:7-alpine` exposed to runner.
- Steps: install Python 3.12, install `.[dev]`, run `ruff check`, `ruff format --check`, `mypy catalog pricing`, `pytest -q --cov=catalog --cov=pricing --cov-report=term-missing`.

Single workflow file; can be expanded later.

**Verification.** Push to a feature branch and confirm CI runs to green on the canary test.

---

## Milestone verification

Run after all tasks complete:

```bash
docker compose up -d
docker compose exec db psql -U postgres -c "SELECT extversion FROM pg_extension WHERE extname='timescaledb';"
python manage.py check
pytest -q
ruff check
ruff format --check
mypy catalog pricing
pre-commit run --all-files
```

All commands exit 0. CI on the canary test branch is green. `spec/INDEX.md` updated to mark M00 done.

---

## Out of scope for M00

- Any domain models. Save for M01.
- Any management commands. Save for M01.
- Logging / Sentry / observability. Save for M07.
- Auth / users. Phase 2 if ever.
