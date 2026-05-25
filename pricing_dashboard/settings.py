from __future__ import annotations

import os
from pathlib import Path

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-insecure-key-do-not-use-in-production-override-via-env",
)

DEBUG = os.environ.get("DEBUG", "False") == "True"

ALLOWED_HOSTS: list[str] = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "django_filters",
    "catalog",
    "pricing",
    "api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pricing_dashboard.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "pricing_dashboard.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "pricing"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "db"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": [
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": "1000/hour"},
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "LLM Inference Pricing API",
    "DESCRIPTION": "Read-only API for LLM inference cost cells.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
    ],
}

_frontend_origin = os.environ.get("FRONTEND_ORIGIN")
CORS_ALLOWED_ORIGINS = ["http://localhost:4200"] + ([_frontend_origin] if _frontend_origin else [])
CORS_ALLOW_CREDENTIALS = False

CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = False

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=0.0,
        send_default_pii=False,
    )

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "pricing": {"handlers": ["console"], "level": "INFO", "propagate": True},
        "catalog": {"handlers": ["console"], "level": "INFO", "propagate": True},
    },
}

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE: dict[str, object] = {
    "scrape-runpod-hourly": {
        "task": "pricing.tasks.scrape_runpod",
        "schedule": crontab(minute=3),
    },
    "scrape-lambda-daily": {
        "task": "pricing.tasks.scrape_lambda",
        "schedule": crontab(minute=15, hour=6),
    },
    "scrape-vast-daily": {
        "task": "pricing.tasks.scrape_vast",
        "schedule": crontab(minute=15, hour=6),
    },
    "scrape-nebius-daily": {
        "task": "pricing.tasks.scrape_nebius",
        "schedule": crontab(minute=15, hour=6),
    },
    "scrape-aws-hourly": {
        "task": "pricing.tasks.scrape_aws",
        "schedule": crontab(minute=4),
    },
    "scrape-azure-hourly": {
        "task": "pricing.tasks.scrape_azure",
        "schedule": crontab(minute=4),
    },
    "regenerate-on-prem": {
        "task": "pricing.tasks.regenerate_on_prem_snapshots_task",
        "schedule": crontab(minute=5),
    },
    "regenerate-reserved-cloud": {
        "task": "pricing.tasks.regenerate_reserved_cloud_snapshots_task",
        "schedule": crontab(minute=6),
    },
    "refresh-cost-cells": {
        "task": "pricing.tasks.refresh_current_cost_cells",
        "schedule": crontab(minute=10),
    },
    "computeprices-sanity-check": {
        "task": "pricing.tasks.computeprices_sanity_check",
        "schedule": crontab(minute=0, hour=8, day_of_week=1),  # Mondays 08:00
    },
}
