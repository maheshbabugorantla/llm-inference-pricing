from __future__ import annotations

import logging
from typing import Any

import sentry_sdk
from celery import shared_task

from pricing.scrapers import lambda_labs, nebius, runpod, vast
from pricing.scrapers.base import ParserDriftError
from pricing.services.cost import refresh_cost_cells
from pricing.services.scrape_runner import persist_prices

logger = logging.getLogger("pricing.tasks")


@shared_task(bind=True, max_retries=3, default_retry_delay=300)  # type: ignore[untyped-decorator]
def scrape_runpod(self: Any) -> int:
    try:
        return persist_prices(runpod.scrape(), gpu_slug_resolver=runpod.map_runpod_gpu)
    except ParserDriftError as exc:
        sentry_sdk.capture_message(str(exc), level="error")
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("runpod scrape failed")
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=600)  # type: ignore[untyped-decorator]
def scrape_lambda(self: Any) -> int:
    try:
        return persist_prices(lambda_labs.scrape(), gpu_slug_resolver=lambda_labs.map_lambda_gpu)
    except ParserDriftError as exc:
        sentry_sdk.capture_message(str(exc), level="error")
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("lambda scrape failed")
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=600)  # type: ignore[untyped-decorator]
def scrape_vast(self: Any) -> int:
    try:
        return persist_prices(vast.scrape(), gpu_slug_resolver=vast.map_vast_gpu)
    except ParserDriftError as exc:
        sentry_sdk.capture_message(str(exc), level="error")
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("vast scrape failed")
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=600)  # type: ignore[untyped-decorator]
def scrape_nebius(self: Any) -> int:
    try:
        return persist_prices(nebius.scrape(), gpu_slug_resolver=nebius.map_nebius_gpu)
    except ParserDriftError as exc:
        sentry_sdk.capture_message(str(exc), level="error")
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("nebius scrape failed")
        raise self.retry(exc=exc) from exc


@shared_task  # type: ignore[untyped-decorator]
def refresh_current_cost_cells() -> None:
    refresh_cost_cells()
