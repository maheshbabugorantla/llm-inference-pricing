"""Microsoft Azure GPU pricing scraper.

Pricing source: Azure Retail Prices API (public, no auth required)
  GET https://prices.azure.com/api/retail/prices?$filter=...

Pagination: follow nextPageLink in response.
Per-GPU division: Standard_ND96isr_H100_v5 is 8x H100 — instance price divided by GPU count.

Tiers emitted: on_demand, reserved-1yr, reserved-3yr
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from pricing.scrapers.base import ParserDriftError, ScrapedPrice

logger = logging.getLogger("pricing.scrapers.azure")

_BASE_URL = "https://prices.azure.com/api/retail/prices"
_FILTER = "serviceName eq 'Virtual Machines' and priceType eq 'Consumption'"

# ARM SKU prefix → (gpu_slug, gpus_per_vm)
_SKU_GPU_MAP: dict[str, tuple[str, int]] = {
    # H100 — NDv5 series
    "Standard_ND96isr_H100_v5": ("nvidia-h100-sxm-80", 8),
    "Standard_ND96isr_MI300X_v5": ("amd-mi300x", 8),
    # A100 — NDv4 series
    "Standard_ND96amsr_A100_v4": ("nvidia-a100-sxm-80", 8),
    "Standard_ND96asr_v4": ("nvidia-a100-sxm-40", 8),
    # A10 — NVv5 series
    "Standard_NV72ads_A10_v5": ("nvidia-a10g", 2),
    "Standard_NV36ads_A10_v5": ("nvidia-a10g", 1),
    "Standard_NV18ads_A10_v5": ("nvidia-a10g", 1),
    # V100 — NDv2 / NCv3
    "Standard_ND40rs_v2": ("nvidia-v100", 8),
    "Standard_NC6s_v3": ("nvidia-v100", 1),
    "Standard_NC12s_v3": ("nvidia-v100", 2),
    "Standard_NC24s_v3": ("nvidia-v100", 4),
    "Standard_NC24rs_v3": ("nvidia-v100", 4),
    # T4 — NCasT4 series
    "Standard_NC4as_T4_v3": ("nvidia-t4", 1),
    "Standard_NC8as_T4_v3": ("nvidia-t4", 1),
    "Standard_NC16as_T4_v3": ("nvidia-t4", 1),
    "Standard_NC64as_T4_v3": ("nvidia-t4", 4),
    # L40S — NVadsA10 series (reusing A10G slug for now)
    "Standard_NV6ads_A10_v5": ("nvidia-a10g", 1),
}

# reservationTerm value → tier name (empty string = on-demand)
_RESERVATION_TIER: dict[str, str] = {
    "": "on_demand",
    "1 Year": "reserved-1yr",
    "3 Years": "reserved-3yr",
}

_DEFAULT_REGION = "eastus"


def map_azure_gpu(arm_sku_name: str) -> str | None:
    entry = _SKU_GPU_MAP.get(arm_sku_name)
    return entry[0] if entry else None


def _gpus_per_vm(arm_sku_name: str) -> int:
    entry = _SKU_GPU_MAP.get(arm_sku_name)
    return entry[1] if entry else 1


def parse_azure_prices(items: list[dict[str, Any]]) -> list[ScrapedPrice]:
    """Parse Azure Retail Prices API items → list[ScrapedPrice].

    Raises ParserDriftError if no rows are produced.
    """
    payload_hash = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    logger.info("azure_prices sha256=%s count=%d", payload_hash, len(items))

    out: list[ScrapedPrice] = []
    for item in items:
        sku_name = item.get("armSkuName", "")
        if sku_name not in _SKU_GPU_MAP:
            logger.info("azure skipping unmapped sku: %s", sku_name)
            continue

        gpu_slug, num_gpus = _SKU_GPU_MAP[sku_name]
        reservation_term = item.get("reservationTerm", "")
        tier = _RESERVATION_TIER.get(reservation_term)
        if tier is None:
            continue

        retail_price = item.get("retailPrice")
        if retail_price is None:
            continue

        hourly_usd = Decimal(str(retail_price))
        if hourly_usd == Decimal(0):
            continue

        per_gpu_hourly = hourly_usd / Decimal(num_gpus)
        region = item.get("armRegionName", _DEFAULT_REGION)

        out.append(
            ScrapedPrice(
                provider_slug="azure",
                gpu_slug_hint=gpu_slug,
                tier=tier,
                region=region,
                hourly_usd=per_gpu_hourly,
                available=True,
                raw={
                    "arm_sku_name": sku_name,
                    "meter_name": item.get("meterName", ""),
                    "num_gpus": num_gpus,
                    "instance_hourly_usd": str(hourly_usd),
                    "reservation_term": reservation_term,
                },
            )
        )

    if not out:
        raise ParserDriftError(f"azure parser found no pricing rows (sha256={payload_hash})")

    return out


_SKU_CHUNK_SIZE = 5  # OData `or` chains get long; keep each request URL manageable


def _fetch_azure_sku_chunk(skus: list[str]) -> list[dict[str, Any]]:
    """Fetch Azure prices for one chunk of SKUs (paginated). Uses OData `or` (not `|`)."""
    sku_filter = " or ".join(f"armSkuName eq '{s}'" for s in skus)
    filter_query = f"({_FILTER}) and ({sku_filter})"
    params: dict[str, str] = {"api-version": "2023-01-01-preview", "$filter": filter_query}

    all_items: list[dict[str, Any]] = []
    url: str | None = _BASE_URL

    while url:
        if url == _BASE_URL:
            response = httpx.get(url, params=params, timeout=30.0)
        else:
            response = httpx.get(url, timeout=30.0)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        all_items.extend(data.get("Items", []))
        url = data.get("NextPageLink")

    return all_items


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_azure_prices() -> list[dict[str, Any]]:
    """Fetch GPU VM prices from Azure Retail Prices API.

    Splits SKU list into chunks of _SKU_CHUNK_SIZE and uses OData `or` (not `|`)
    so each request stays within Azure's filter length limits.
    """
    sku_list = list(_SKU_GPU_MAP.keys())
    all_items: list[dict[str, Any]] = []
    for i in range(0, len(sku_list), _SKU_CHUNK_SIZE):
        chunk = sku_list[i : i + _SKU_CHUNK_SIZE]
        all_items.extend(_fetch_azure_sku_chunk(chunk))
    return all_items


def scrape() -> list[ScrapedPrice]:
    return parse_azure_prices(fetch_azure_prices())
