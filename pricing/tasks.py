from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from pricing.scrapers import runpod
from pricing.services.scrape_runner import persist_prices

logger = logging.getLogger("pricing.tasks")


@shared_task(bind=True, max_retries=3, default_retry_delay=300)  # type: ignore[untyped-decorator]
def scrape_runpod(self: Any) -> int:
    try:
        return persist_prices(runpod.scrape(), gpu_slug_resolver=runpod.map_runpod_gpu)
    except Exception as exc:
        logger.exception("runpod scrape failed")
        raise self.retry(exc=exc) from exc
