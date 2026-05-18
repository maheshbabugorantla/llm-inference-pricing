"""Lambda Labs GPU Cloud scraper.

Pricing page: https://lambda.ai/instances
Approach: HTTP GET → find 'var newIslands = [...]' script → extract TabsIsland
JSON → parse embedded HTML pricing tables.

The page embeds per-GPU pricing in a TabsIsland component with tabs for
different multi-GPU configurations (8x, 4x, 2x, 1x). Each row has a
data-plan attribute with the GPU name and a PRICE/GPU/HR* column.
Tier captured: on_demand only (Lambda has no spot/interruptible pricing).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from decimal import Decimal
from typing import Any

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from pricing.scrapers.base import ParserDriftError, ScrapedPrice

logger = logging.getLogger("pricing.scrapers.lambda_labs")

LAMBDA_GPU_MAP: dict[str, str] = {
    "NVIDIA B200 SXM6": "nvidia-b200",
    "NVIDIA H100 SXM": "nvidia-h100-sxm-80",
    "NVIDIA H100 PCIe": "nvidia-h100-pcie-80",
    "NVIDIA A100 SXM": "nvidia-a100-sxm-80",
}

_LAMBDA_URL = "https://lambda.ai/instances"
_PRICE_RE = re.compile(r"\$?([\d.]+)")
_ISLANDS_RE = re.compile(r"var newIslands = (\[.*?\]);", re.DOTALL)


def map_lambda_gpu(display_name: str) -> str | None:
    return LAMBDA_GPU_MAP.get(display_name)


def _extract_islands(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        txt = script.get_text()
        m = _ISLANDS_RE.search(txt)
        if m:
            return json.loads(m.group(1))  # type: ignore[no-any-return]
    return []


def parse_lambda_html(html: str) -> list[ScrapedPrice]:
    """Parse Lambda Labs pricing HTML → list[ScrapedPrice].

    Logs an INFO line with the SHA-256 of the page for drift detection.
    Raises ParserDriftError if no TabsIsland is found or if no pricing rows
    can be extracted from it.
    """
    page_hash = hashlib.sha256(html.encode()).hexdigest()
    logger.info("lambda_labs page sha256=%s", page_hash)

    islands = _extract_islands(html)
    tabs_island = next(
        (i for i in islands if i.get("moduleName") == "TabsIsland"),
        None,
    )
    if tabs_island is None:
        raise ParserDriftError(
            f"lambda_labs parser found no TabsIsland — page structure may have changed (sha256={page_hash})"
        )

    tabs = tabs_island.get("props", {}).get("tabs", [])
    out: list[ScrapedPrice] = []
    rows_found = 0

    for tab in tabs:
        tab_label = tab.get("label", "?")
        content_html = tab.get("contentHtml", "")
        tab_soup = BeautifulSoup(content_html, "html.parser")

        for row in tab_soup.select("tbody tr"):
            _plan_attr = row.get("data-plan")
            gpu_name: str = (
                _plan_attr if isinstance(_plan_attr, str) else (_plan_attr[0] if _plan_attr else "")
            )
            if not gpu_name:
                th = row.find("th")
                gpu_name = th.get_text(strip=True) if th else ""

            price_td = row.find("td", {"data-label": "PRICE/GPU/HR*"})
            if price_td is None:
                # Try last td as fallback
                tds = row.find_all("td")
                price_td = tds[-1] if tds else None

            if not gpu_name or price_td is None:
                continue

            rows_found += 1
            price_text = price_td.get_text(strip=True)
            match = _PRICE_RE.search(price_text)
            if match is None:
                logger.warning(
                    "lambda_labs no price in cell: %r gpu=%s tab=%s",
                    price_text,
                    gpu_name,
                    tab_label,
                )
                continue

            gpu_slug = map_lambda_gpu(gpu_name)
            if gpu_slug is None:
                if gpu_name:
                    logger.info("lambda_labs skipping unmapped gpu: %s", gpu_name)
                continue

            out.append(
                ScrapedPrice(
                    provider_slug="lambda",
                    gpu_slug_hint=gpu_name,
                    tier="on_demand",
                    region="",
                    hourly_usd=Decimal(match.group(1)),
                    available=True,
                    raw={"gpu": gpu_name, "tab": tab_label, "price_text": price_text},
                )
            )

    if rows_found == 0:
        raise ParserDriftError(f"lambda_labs parser found no pricing rows in TabsIsland (sha256={page_hash})")

    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_lambda_html() -> str:
    response = httpx.get(
        _LAMBDA_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; pricing-scraper/1.0)"},
    )
    response.raise_for_status()
    return response.text


def scrape() -> list[ScrapedPrice]:
    return parse_lambda_html(fetch_lambda_html())
