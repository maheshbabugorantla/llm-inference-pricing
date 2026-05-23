from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from pricing.scrapers.base import ScrapedPrice

logger = logging.getLogger("pricing.scrapers.runpod")

RUNPOD_GPU_MAP: dict[str, str] = {
    "H100 SXM": "nvidia-h100-sxm-80",
    "H100 PCIe": "nvidia-h100-pcie-80",
    "H200 SXM": "nvidia-h200",
    "A100 SXM": "nvidia-a100-sxm-80",
    "A100 SXM 40GB": "nvidia-a100-sxm-40",
    "L40S": "nvidia-l40s",
    "L4": "nvidia-l4",
    "RTX 4090": "nvidia-rtx-4090",
    "RTX 6000 Ada": "nvidia-rtx-6000-ada",
    "B200": "nvidia-b200",
    "MI300X": "amd-mi300x",
}

TIER_FIELDS: dict[str, str] = {
    "communityPrice": "community",
    "securePrice": "secure",
    "communitySpotPrice": "community-spot",
    "secureSpotPrice": "secure-spot",
    "oneMonthPrice": "reserved-1mo",
    "threeMonthPrice": "reserved-3mo",
    "sixMonthPrice": "reserved-6mo",
    "oneYearPrice": "reserved-1yr",
}


def map_runpod_gpu(display_name: str) -> str | None:
    return RUNPOD_GPU_MAP.get(display_name)


def parse_runpod_response(payload: dict[str, Any]) -> list[ScrapedPrice]:
    out: list[ScrapedPrice] = []
    for gpu in payload.get("data", {}).get("gpuTypes", []):
        display = gpu.get("displayName")
        gpu_slug = map_runpod_gpu(display)
        if gpu_slug is None:
            logger.info("runpod scraper skipping unmapped gpu: %s", display)
            continue
        for field, tier in TIER_FIELDS.items():
            raw_price = gpu.get(field)
            if raw_price is None:
                continue
            out.append(
                ScrapedPrice(
                    provider_slug="runpod",
                    gpu_slug_hint=display,
                    tier=tier,
                    region="",
                    hourly_usd=Decimal(str(raw_price)),
                    available=True,
                    raw=gpu,
                )
            )
    return out


_RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"

_GPU_TYPES_QUERY = """
query GpuTypes {
  gpuTypes {
    id
    displayName
    memoryInGb
    communityPrice
    securePrice
    communitySpotPrice
    secureSpotPrice
    oneMonthPrice
    threeMonthPrice
    sixMonthPrice
    oneYearPrice
  }
}
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_runpod_gputypes() -> dict[str, Any]:
    """Call the RunPod GraphQL API. Public schema, no auth required."""
    response = httpx.post(
        _RUNPOD_GRAPHQL_URL,
        json={"query": _GPU_TYPES_QUERY},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def scrape() -> list[ScrapedPrice]:
    """Top-level entry point. Calls API, parses, returns prices."""
    return parse_runpod_response(fetch_runpod_gputypes())
