from __future__ import annotations

import logging
from typing import Any

import httpx
import sentry_sdk
from celery import shared_task

from pricing.scrapers import aws, azure, lambda_labs, nebius, runpod, vast
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


@shared_task(bind=True, max_retries=3, default_retry_delay=600)  # type: ignore[untyped-decorator]
def scrape_aws(self: Any) -> int:
    try:
        return persist_prices(aws.scrape(), gpu_slug_resolver=aws.map_aws_gpu)
    except ParserDriftError as exc:
        sentry_sdk.capture_message(str(exc), level="error")
        raise
    except Exception as exc:
        import botocore.exceptions

        if isinstance(exc, botocore.exceptions.ClientError):
            sentry_sdk.capture_exception(exc)
            logger.exception("aws scrape failed (ClientError)")
            raise self.retry(exc=exc) from exc
        sentry_sdk.capture_exception(exc)
        logger.exception("aws scrape failed")
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=600)  # type: ignore[untyped-decorator]
def scrape_azure(self: Any) -> int:
    try:
        return persist_prices(azure.scrape(), gpu_slug_resolver=azure.map_azure_gpu)
    except ParserDriftError as exc:
        sentry_sdk.capture_message(str(exc), level="error")
        raise
    except httpx.HTTPStatusError as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("azure scrape failed (HTTPStatusError)")
        raise self.retry(exc=exc) from exc
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("azure scrape failed")
        raise self.retry(exc=exc) from exc


@shared_task  # type: ignore[untyped-decorator]
def regenerate_on_prem_snapshots_task() -> int:
    from pricing.generators.on_prem import regenerate_on_prem_snapshots

    return regenerate_on_prem_snapshots()


@shared_task  # type: ignore[untyped-decorator]
def refresh_current_cost_cells() -> None:
    refresh_cost_cells()
