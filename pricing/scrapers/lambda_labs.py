"""Lambda Labs GPU Cloud scraper.

Pricing page: https://lambdalabs.com/service/gpu-cloud#pricing
Approach: HTTP GET → BeautifulSoup table parse → ScrapedPrice list.
Tier captured: on_demand only (reserved pricing is negotiated separately).
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

logger = logging.getLogger("pricing.scrapers.lambda_labs")

LAMBDA_GPU_MAP: dict[str, str] = {
    "H100 (80 GB SXM5)": "nvidia-h100-sxm-80",
    "H100 (80 GB PCIe)": "nvidia-h100-pcie-80",
    "A100 (80 GB SXM4)": "nvidia-a100-sxm-80",
    "A100 (40 GB SXM4)": "nvidia-a100-sxm-40",
    "A6000 (48 GB)": "nvidia-rtx-6000-ada",
    "H200 (96 GB SXM)": "nvidia-h200",
    "L40S (48 GB)": "nvidia-l40s",
}

_LAMBDA_URL = "https://lambdalabs.com/service/gpu-cloud#pricing"
_PRICE_RE = re.compile(r"\$?([\d.]+)\s*/\s*hr", re.IGNORECASE)


def map_lambda_gpu(display_name: str) -> str | None:
    return LAMBDA_GPU_MAP.get(display_name)


def parse_lambda_html(html: str) -> list[ScrapedPrice]:
    """Parse Lambda Labs pricing HTML → list[ScrapedPrice].

    Logs an INFO line with the SHA-256 of the page for drift detection.
    Raises ParserDriftError if no prices can be extracted.
    """
    page_hash = hashlib.sha256(html.encode()).hexdigest()
    logger.info("lambda_labs page sha256=%s", page_hash)

    soup = BeautifulSoup(html, "html.parser")
    out: list[ScrapedPrice] = []
    rows_found = 0

    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        rows_found += 1

        # First cell = GPU name, last cell = price
        gpu_name = cells[0].get_text(strip=True)
        price_text = cells[-1].get_text(strip=True)

        gpu_slug = map_lambda_gpu(gpu_name)
        if gpu_slug is None:
            if gpu_name:
                logger.info("lambda_labs skipping unmapped gpu: %s", gpu_name)
            continue

        match = _PRICE_RE.search(price_text)
        if match is None:
            logger.warning("lambda_labs no price found in cell: %r for gpu=%s", price_text, gpu_name)
            continue

        out.append(
            ScrapedPrice(
                provider_slug="lambda",
                gpu_slug_hint=gpu_name,
                tier="on_demand",
                region="",
                hourly_usd=Decimal(match.group(1)),
                available=True,
                raw={"gpu": gpu_name, "price_text": price_text},
            )
        )

    # Drift: no table rows found at all means the page structure changed
    if rows_found == 0:
        raise ParserDriftError(
            f"lambda_labs parser found no pricing rows — page structure may have changed (sha256={page_hash})"
        )

    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_lambda_html() -> str:
    response = httpx.get(_LAMBDA_URL, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def scrape() -> list[ScrapedPrice]:
    return parse_lambda_html(fetch_lambda_html())
