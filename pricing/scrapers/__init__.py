from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable

    from pricing.scrapers.base import ScrapedPrice


class ScraperEntry(NamedTuple):
    scrape_fn: Callable[[], list[ScrapedPrice]]
    gpu_slug_resolver: Callable[[str], str | None]
    source_url: str


def _build_scrapers() -> dict[str, ScraperEntry]:
    from pricing.scrapers import lambda_labs, nebius, runpod, vast

    return {
        "runpod": ScraperEntry(runpod.scrape, runpod.map_runpod_gpu, "https://api.runpod.io/graphql"),
        "lambda": ScraperEntry(lambda_labs.scrape, lambda_labs.map_lambda_gpu, "https://lambda.ai/instances"),
        "vast": ScraperEntry(vast.scrape, vast.map_vast_gpu, "https://console.vast.ai/api/v0/bundles/"),
        "nebius": ScraperEntry(nebius.scrape, nebius.map_nebius_gpu, "https://nebius.com/prices"),
    }


SCRAPERS: dict[str, ScraperEntry] = _build_scrapers()
