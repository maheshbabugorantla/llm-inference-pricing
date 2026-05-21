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
    from pricing.scrapers import aws, azure, gcp, lambda_labs, nebius, runpod, vast

    return {
        "runpod": ScraperEntry(runpod.scrape, runpod.map_runpod_gpu, "https://api.runpod.io/graphql"),
        "lambda": ScraperEntry(lambda_labs.scrape, lambda_labs.map_lambda_gpu, "https://lambda.ai/instances"),
        "vast": ScraperEntry(vast.scrape, vast.map_vast_gpu, "https://console.vast.ai/api/v0/bundles/"),
        "nebius": ScraperEntry(nebius.scrape, nebius.map_nebius_gpu, "https://nebius.com/prices"),
        "gcp": ScraperEntry(
            gcp.scrape,
            gcp.map_gcp_gpu,
            "https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus",
        ),
        "aws": ScraperEntry(
            aws.scrape,
            aws.map_aws_gpu,
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/index.json",
        ),
        "azure": ScraperEntry(
            azure.scrape,
            azure.map_azure_gpu,
            "https://prices.azure.com/api/retail/prices",
        ),
    }


SCRAPERS: dict[str, ScraperEntry] = _build_scrapers()
