"""ComputePrices.com GPU pricing API client.

Uses the public REST API at https://computeprices.com/api/v1/.
Public tier: 60 req/hr per IP (no auth required).
Free tier: 5,000 req/hr — set COMPUTEPRICES_API_KEY env var with a key
obtained by emailing api@computeprices.com with your use-case.

API reference: https://computeprices.com/docs/api
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from pricing.scrapers.base import ParserDriftError

logger = logging.getLogger("pricing.scrapers.computeprices")

_BASE_URL = "https://computeprices.com/api/v1"

# Maps our internal GPU slugs → ComputePrices API gpu slugs.
# Only GPUs that appear in ComputePrices listings are included.
GPU_SLUG_MAP: dict[str, str] = {
    "nvidia-h100-sxm-80": "h100",
    "nvidia-h100-pcie-80": "h100pcie",
    "nvidia-h200": "h200",
    "nvidia-a100-sxm-80": "a100sxm",
    "nvidia-a100-sxm-40": "a100sxm",
    "nvidia-l40s": "l40s",
    "nvidia-l4": "l4",
    "nvidia-rtx-4090": "rtx4090",
    "nvidia-rtx-6000-ada": "rtx6000ada",
    "nvidia-b200": "b200",
    "nvidia-t4": "t4",
    "nvidia-v100": "v100",
    "amd-mi300x": "mi300x",
    "amd-mi250x": "mi250x",
}

# ComputePrices provider_slug → our slug.
# Only entries where they differ are listed; all others pass through unchanged.
PROVIDER_SLUG_MAP: dict[str, str] = {
    "oracle": "oci",
    "google": "gcp",
}

_REQUIRED_ITEM_KEYS: frozenset[str] = frozenset({"provider_slug", "price_per_hour_usd"})


def map_provider_slug(their_slug: str) -> str:
    """Map a ComputePrices provider_slug to our internal provider slug."""
    return PROVIDER_SLUG_MAP.get(their_slug, their_slug)


def parse_computeprices_response(data: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalise ComputePrices API response items to {provider, hourly_usd, gpu?} format.

    hourly_usd is price_per_hour_usd (per-GPU) to align with per_active_hour_usd semantics.
    Items with null price_per_hour_usd are skipped (no price to compare).
    Raises ParserDriftError if data is empty or any item is missing required fields.
    """
    if not data:
        raise ParserDriftError(
            "computeprices API returned empty data — GPU may not be tracked or has no listings"
        )

    out: list[dict[str, str]] = []
    for i, item in enumerate(data):
        missing = _REQUIRED_ITEM_KEYS - item.keys()
        if missing:
            raise ParserDriftError(
                f"computeprices API item {i} missing required fields {missing!r}"
                " — API schema may have changed"
            )

        price = item["price_per_hour_usd"]
        if price is None:
            logger.debug("computeprices item %d has null price_per_hour_usd — skipping", i)
            continue

        price_str = str(price)
        try:
            Decimal(price_str)
        except InvalidOperation as exc:
            raise ParserDriftError(
                f"computeprices API item {i} has non-numeric price_per_hour_usd={price_str!r}"
                " — API schema may have changed"
            ) from exc

        normalized: dict[str, str] = {
            "provider": map_provider_slug(str(item["provider_slug"])),
            "hourly_usd": price_str,
        }
        if item.get("gpu"):
            normalized["gpu"] = str(item["gpu"])
        out.append(normalized)

    return out


def fetch_computeprices_gpu_prices(our_gpu_slug: str) -> list[dict[str, str]]:
    """Fetch on-demand GPU prices from the ComputePrices.com API.

    Maps our GPU slug to their API slug via GPU_SLUG_MAP. Returns [] if the GPU
    is not in the map (not tracked by ComputePrices).

    Reads COMPUTEPRICES_API_KEY from the environment for the free tier
    (5,000 req/hr). Without it, uses the public tier (60 req/hr per IP).
    """
    their_slug = GPU_SLUG_MAP.get(our_gpu_slug)
    if their_slug is None:
        logger.debug("GPU slug %r not in GPU_SLUG_MAP — skipping ComputePrices lookup", our_gpu_slug)
        return []

    headers: dict[str, str] = {}
    api_key = os.environ.get("COMPUTEPRICES_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = httpx.get(
        f"{_BASE_URL}/gpu-prices",
        params={"gpu": their_slug, "pricing_type": "on_demand"},
        headers=headers,
        timeout=30.0,
    )
    resp.raise_for_status()
    data: list[dict[str, Any]] = resp.json().get("data", [])
    return parse_computeprices_response(data)
