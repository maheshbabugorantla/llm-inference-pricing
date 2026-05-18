"""Vast.ai GPU marketplace scraper.

Endpoint: https://console.vast.ai/api/v0/bundles/
Approach: GET JSON → aggregate hundreds of host bundles per GPU name →
emit one ScrapedPrice per (gpu, tier) with p50 dph_total as hourly_usd.
Tiers: on_demand (is_bid_only=False), interruptible (is_bid_only=True).
"""

from __future__ import annotations

import logging
import statistics
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from pricing.scrapers.base import ScrapedPrice

logger = logging.getLogger("pricing.scrapers.vast")

VAST_GPU_MAP: dict[str, str] = {
    "RTX 4090": "nvidia-rtx-4090",
    "H100 SXM5": "nvidia-h100-sxm-80",
    "H100 PCIe": "nvidia-h100-pcie-80",
    "H200 SXM5": "nvidia-h200",
    "A100 SXM4": "nvidia-a100-sxm-80",
    "A100 PCIe": "nvidia-a100-sxm-40",
    "L40S": "nvidia-l40s",
    "RTX 6000 Ada": "nvidia-rtx-6000-ada",
}

_VAST_URL = "https://console.vast.ai/api/v0/bundles/"


def map_vast_gpu(display_name: str) -> str | None:
    return VAST_GPU_MAP.get(display_name)


def _percentile(values: list[float], pct: int) -> float:
    """Return the pct-th percentile (0-100) of a sorted list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (pct / 100) * (len(sorted_vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def parse_vast_response(payload: dict[str, Any]) -> list[ScrapedPrice]:
    """Aggregate Vast.ai bundle marketplace response into per-GPU ScrapedPrices."""
    # Group dph_total values by (gpu_name, tier)
    groups: dict[tuple[str, str], list[float]] = {}
    for bundle in payload.get("offers", []):
        gpu_name = bundle.get("gpu_name", "")
        if map_vast_gpu(gpu_name) is None:
            if gpu_name:
                logger.info("vast skipping unmapped gpu: %s", gpu_name)
            continue
        tier = "interruptible" if bundle.get("is_bid_only") else "on_demand"
        key = (gpu_name, tier)
        groups.setdefault(key, []).append(float(bundle.get("dph_total", 0)))

    out: list[ScrapedPrice] = []
    for (gpu_name, tier), prices in groups.items():
        if not prices:
            continue
        p50 = statistics.median(prices)
        p90 = _percentile(prices, 90)
        distribution = {
            "min": min(prices),
            "p50": p50,
            "p90": p90,
            "max": max(prices),
            "count": len(prices),
        }
        out.append(
            ScrapedPrice(
                provider_slug="vast",
                gpu_slug_hint=gpu_name,
                tier=tier,
                region="",
                hourly_usd=Decimal(str(round(p50, 6))),
                available=True,
                raw=distribution,
            )
        )

    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_vast_bundles() -> dict[str, Any]:
    response = httpx.get(_VAST_URL, timeout=30.0)
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def scrape() -> list[ScrapedPrice]:
    return parse_vast_response(fetch_vast_bundles())
