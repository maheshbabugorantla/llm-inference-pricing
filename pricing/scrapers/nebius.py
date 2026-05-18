"""Nebius AI Cloud GPU scraper.

Pricing page: https://nebius.com/prices
Approach: HTTP GET → extract __NEXT_DATA__ JSON → navigate Apollo state →
find 'highlight-table-block' with title 'NVIDIA GPU Instances' → parse rows.

Tiers captured: on_demand and preemptible (both present in the table columns).
Prices prefixed with "from " are parsed; "Contact us" and "--" are skipped.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from pricing.scrapers.base import ParserDriftError, ScrapedPrice

logger = logging.getLogger("pricing.scrapers.nebius")

NEBIUS_GPU_MAP: dict[str, str] = {
    "NVIDIA HGX H100": "nvidia-h100-sxm-80",
    "NVIDIA HGX H200": "nvidia-h200",
    "NVIDIA L40S with Intel CPU": "nvidia-l40s",
    "NVIDIA L40S with AMD CPU": "nvidia-l40s",
}

_NEBIUS_URL = "https://nebius.com/prices"
_PRICE_RE = re.compile(r"\$?([\d.]+)")
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

_TIER_COLUMNS: dict[str, str] = {
    "Preemptible, GPU-hour": "preemptible",
    "On-demand, GPU-hour": "on_demand",
}


def map_nebius_gpu(display_name: str) -> str | None:
    return NEBIUS_GPU_MAP.get(display_name)


def _extract_gpu_table(next_data: dict[str, Any]) -> list[list[str]] | None:
    ap = next_data.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__", {})
    for obj in ap.values():
        if not isinstance(obj, dict):
            continue
        raw_content = obj.get("content")
        if not isinstance(raw_content, str):
            continue
        try:
            content = json.loads(raw_content)
        except (json.JSONDecodeError, ValueError):
            continue
        for block in content.get("blocks", []):
            if block.get("type") == "highlight-table-block" and block.get("title") == "NVIDIA GPU Instances":
                rows = block.get("table", {}).get("content", [])
                if rows:
                    return rows  # type: ignore[no-any-return]
    return None


def parse_nebius_html(html: str) -> list[ScrapedPrice]:
    """Parse Nebius pricing HTML → list[ScrapedPrice].

    Logs an INFO line with the SHA-256 of the page for drift detection.
    Raises ParserDriftError if no __NEXT_DATA__ or no GPU block is found.
    """
    page_hash = hashlib.sha256(html.encode()).hexdigest()
    logger.info("nebius page sha256=%s", page_hash)

    m = _NEXT_DATA_RE.search(html)
    if m is None:
        raise ParserDriftError(f"nebius parser found no __NEXT_DATA__ script tag (sha256={page_hash})")

    try:
        next_data: dict[str, Any] = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise ParserDriftError(f"nebius __NEXT_DATA__ is not valid JSON: {exc}") from exc

    rows = _extract_gpu_table(next_data)
    if rows is None:
        raise ParserDriftError(
            f"nebius parser found no 'NVIDIA GPU Instances' table block (sha256={page_hash})"
        )

    # First row is the header
    header = rows[0]
    tier_col_indices: dict[int, str] = {}
    for col_idx, col_name in enumerate(header):
        if col_name in _TIER_COLUMNS:
            tier_col_indices[col_idx] = _TIER_COLUMNS[col_name]

    out: list[ScrapedPrice] = []
    for row in rows[1:]:
        if not row:
            continue
        gpu_name = str(row[0]).strip()
        gpu_slug = map_nebius_gpu(gpu_name)
        if gpu_slug is None:
            if gpu_name:
                logger.info("nebius skipping unmapped gpu: %s", gpu_name)
            continue

        for col_idx, tier in tier_col_indices.items():
            if col_idx >= len(row):
                continue
            price_text = str(row[col_idx]).strip()
            match = _PRICE_RE.search(price_text)
            if match is None:
                continue

            out.append(
                ScrapedPrice(
                    provider_slug="nebius",
                    gpu_slug_hint=gpu_name,
                    tier=tier,
                    region="",
                    hourly_usd=Decimal(match.group(1)),
                    available=True,
                    raw={"gpu": gpu_name, "tier": tier, "price_text": price_text},
                )
            )

    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_nebius_html() -> str:
    response = httpx.get(
        _NEBIUS_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; pricing-scraper/1.0)"},
    )
    response.raise_for_status()
    return response.text


def scrape() -> list[ScrapedPrice]:
    return parse_nebius_html(fetch_nebius_html())
