"""Nebius AI Cloud GPU scraper.

Pricing page: https://nebius.com/prices (GPU Platform section)
Approach: HTTP GET → BeautifulSoup table parse → ScrapedPrice list.
Tier captured: on_demand only (reserved pricing is negotiated).

The Nebius pricing table has columns: Platform | GPU type | vCPU | RAM | Price/hr.
The GPU type column (index 1) is used for name-to-slug mapping.
"""

from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from pricing.scrapers.base import ParserDriftError, ScrapedPrice

logger = logging.getLogger("pricing.scrapers.nebius")

NEBIUS_GPU_MAP: dict[str, str] = {
    "NVIDIA H100 SXM (80 GB)": "nvidia-h100-sxm-80",
    "NVIDIA H100 PCIe (80 GB)": "nvidia-h100-pcie-80",
    "NVIDIA H200 SXM (96 GB)": "nvidia-h200",
    "NVIDIA L40S (48 GB)": "nvidia-l40s",
}

_NEBIUS_URL = "https://nebius.com/prices"
_PRICE_RE = re.compile(r"\$?([\d.]+)")


def map_nebius_gpu(display_name: str) -> str | None:
    return NEBIUS_GPU_MAP.get(display_name)


def parse_nebius_html(html: str) -> list[ScrapedPrice]:
    """Parse Nebius pricing HTML → list[ScrapedPrice].

    Logs an INFO line with the SHA-256 of the page for drift detection.
    Raises ParserDriftError if no table rows are found (structure changed).
    """
    page_hash = hashlib.sha256(html.encode()).hexdigest()
    logger.info("nebius page sha256=%s", page_hash)

    soup = BeautifulSoup(html, "html.parser")
    out: list[ScrapedPrice] = []
    rows_found = 0

    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        rows_found += 1

        # GPU type is in the second column (index 1); price in the last column
        gpu_name = cells[1].get_text(strip=True)
        price_text = cells[-1].get_text(strip=True)

        gpu_slug = map_nebius_gpu(gpu_name)
        if gpu_slug is None:
            if gpu_name:
                logger.info("nebius skipping unmapped gpu: %s", gpu_name)
            continue

        match = _PRICE_RE.search(price_text)
        if match is None:
            logger.warning("nebius no price found in cell: %r for gpu=%s", price_text, gpu_name)
            continue

        out.append(
            ScrapedPrice(
                provider_slug="nebius",
                gpu_slug_hint=gpu_name,
                tier="on_demand",
                region="",
                hourly_usd=Decimal(match.group(1)),
                available=True,
                raw={"gpu": gpu_name, "price_text": price_text},
            )
        )

    if rows_found == 0:
        raise ParserDriftError(
            f"nebius parser found no pricing rows — page structure may have changed (sha256={page_hash})"
        )

    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_nebius_html() -> str:
    response = httpx.get(_NEBIUS_URL, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def scrape() -> list[ScrapedPrice]:
    return parse_nebius_html(fetch_nebius_html())
