"""ComputePrices.com GPU pricing aggregator scraper stub.

Source: https://computeprices.com/<gpu-slug>/
Approach: HTTP GET per-GPU page -> parse provider/price table -> return raw rows.

TOS REMINDER — before enabling fetch_computeprices_table in production, verify:
  1. ComputePrices.com terms of service permit automated scraping.
  2. Whether attribution or rate-limiting is required.
  3. Whether their data is updated frequently enough to be useful for drift detection.

This function is currently a stub that raises NotImplementedError.
Use parse_computeprices_table with a recorded fixture in tests and in the
drift-detection service (which patches fetch_computeprices_table at the boundary).
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from pricing.scrapers.base import ParserDriftError

logger = logging.getLogger("pricing.scrapers.computeprices")

PROVIDER_MAP: dict[str, str] = {
    "RunPod": "runpod",
    "Lambda Labs": "lambda",
    "Vast.ai": "vast",
    "Nebius": "nebius",
    "Google Cloud": "gcp",
    "AWS": "aws",
    "Microsoft Azure": "azure",
    "CoreWeave": "coreweave",
    "Crusoe Energy": "crusoe",
    "Oracle Cloud": "oci",
}

_REQUIRED_ROW_KEYS: frozenset[str] = frozenset({"provider", "hourly_usd"})


def map_computeprices_provider(provider_name: str) -> str | None:
    """Map a ComputePrices.com display name to our provider slug."""
    return PROVIDER_MAP.get(provider_name)


def parse_computeprices_table(rows_data: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalise raw rows scraped from a ComputePrices.com GPU page.

    Raises ParserDriftError if the input is empty or any row is missing required keys.
    Returns list of dicts with keys: provider, hourly_usd, and optionally gpu, region.
    """
    if not rows_data:
        raise ParserDriftError(
            "computeprices parser received empty rows_data — page structure may have changed"
        )

    out: list[dict[str, str]] = []
    for i, row in enumerate(rows_data):
        missing = _REQUIRED_ROW_KEYS - row.keys()
        if missing:
            raise ParserDriftError(
                f"computeprices row {i} missing required keys {missing!r} — page structure may have changed"
            )
        provider_name = str(row["provider"])
        our_slug = map_computeprices_provider(provider_name)
        if our_slug is None:
            logger.info("computeprices skipping unmapped provider: %s", provider_name)
            continue
        hourly_usd_raw = str(row["hourly_usd"])
        try:
            Decimal(hourly_usd_raw)
        except InvalidOperation as exc:
            raise ParserDriftError(
                f"computeprices row {i} has non-numeric hourly_usd={hourly_usd_raw!r}"
                " — page structure may have changed"
            ) from exc
        normalized: dict[str, str] = {
            "provider": our_slug,
            "hourly_usd": hourly_usd_raw,
        }
        if "gpu" in row:
            normalized["gpu"] = str(row["gpu"])
        if row.get("region"):
            normalized["region"] = str(row["region"])
        out.append(normalized)

    return out


def fetch_computeprices_table(gpu_slug: str) -> list[dict[str, str]]:
    """Scrape the per-GPU page on ComputePrices.com.

    NOT IMPLEMENTED — TOS not verified. Use parse_computeprices_table
    with a recorded fixture in tests.

    When TOS is cleared, implement as:
      GET https://computeprices.com/<gpu_slug>/
      Parse the provider/price table rows.
      Return parse_computeprices_table(extracted_rows).
    """
    raise NotImplementedError(
        "fetch_computeprices_table: TOS not verified — use fixture in tests. "
        "See module docstring for steps required before production use."
    )
