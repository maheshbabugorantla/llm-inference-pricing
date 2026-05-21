"""Google Cloud GPU pricing scraper.

Pricing source: Cloud Billing Catalog API — Compute Engine service
  GET https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus

Auth: GOOGLE_APPLICATION_CREDENTIALS (service-account JSON) or application
default credentials from `gcloud auth application-default login`.

Only standard on-demand and Committed-Use Discount (CUD) SKUs for
us-central1 are captured. DWS, Calendar Mode, Preemptible, vGPU partition,
and $0 placeholder SKUs are silently dropped.

GCP prices GPU-attached N1 SKUs per GPU-hour directly. A100/H100/L4 VMs
also expose per-GPU SKUs in the billing catalog with resourceGroup="GPU".

Note: this module intentionally imports google.auth lazily inside
fetch_gcp_skus() to avoid import-time failures in CI environments where
GOOGLE_APPLICATION_CREDENTIALS is not set (tests use the fixture directly).
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

logger = logging.getLogger("pricing.scrapers.gcp")

_SERVICE_ID = "6F81-5844-456A"
_SKUS_URL = f"https://cloudbilling.googleapis.com/v1/services/{_SERVICE_ID}/skus"
_REGION = "us-central1"

_USAGE_TYPE_TO_TIER: dict[str, str] = {
    "OnDemand": "on_demand",
    "Commit1Yr": "cud-1yr",
    "Commit3Yr": "cud-3yr",
}

# Substrings that mark SKUs to skip (DWS reservations, Calendar-Mode, preemptible-in-desc, vGPU).
# Preemptible via usageType is handled separately.
_SKIP_DESC_PATTERNS = ("DWS", "Calendar Mode", "Spot Preemptible", "vGPU", "Reserved ")

# Ordered list: most-specific patterns first to avoid false matches.
GCP_GPU_PATTERNS: list[tuple[str, str]] = [
    ("Nvidia Tesla A100 80GB", "nvidia-a100-sxm-80"),
    ("Nvidia Tesla A100", "nvidia-a100-sxm-40"),
    ("Nvidia H100 80GB", "nvidia-h100-sxm-80"),
    ("H200 141GB", "nvidia-h200"),
    ("Nvidia L4", "nvidia-l4"),
    ("Nvidia Tesla T4", "nvidia-t4"),
    ("Nvidia Tesla V100", "nvidia-v100"),
    ("Nvidia Tesla P100", "nvidia-p100"),
    ("Nvidia Tesla P4", "nvidia-p4"),
    ("Nvidia Tesla K80", "nvidia-k80"),
    ("Nvidia B200", "nvidia-b200"),
]


_GCP_KNOWN_SLUGS: frozenset[str] = frozenset(slug for _, slug in GCP_GPU_PATTERNS)


def map_gcp_gpu(description: str) -> str | None:
    # gpu_slug_hint is stored as the resolved slug in artifacts — pass it through directly
    if description in _GCP_KNOWN_SLUGS:
        return description
    for pattern, slug in GCP_GPU_PATTERNS:
        if pattern in description:
            return slug
    return None


def _parse_unit_price(sku: dict[str, Any]) -> Decimal | None:
    """Extract hourly USD price from a SKU pricingInfo block. Returns None on missing data."""
    pricing_info = sku.get("pricingInfo", [])
    if not pricing_info:
        return None
    rates = pricing_info[0].get("pricingExpression", {}).get("tieredRates", [])
    if not rates:
        return None
    unit_price = rates[0].get("unitPrice", {})
    units = Decimal(str(unit_price.get("units", 0)))
    nanos = Decimal(str(unit_price.get("nanos", 0)))
    return units + nanos / Decimal("1000000000")


def parse_gcp_skus(skus: list[dict[str, Any]]) -> list[ScrapedPrice]:
    """Parse a list of GPU SKU dicts (resourceGroup=GPU) → list[ScrapedPrice].

    Logs a SHA-256 of the input for drift detection.
    Raises ParserDriftError if no valid pricing rows are found.
    """
    page_hash = hashlib.sha256(json.dumps(skus, sort_keys=True).encode()).hexdigest()
    logger.info("gcp_skus sha256=%s count=%d", page_hash, len(skus))

    out: list[ScrapedPrice] = []

    for sku in skus:
        cat = sku.get("category", {})
        usage_type = cat.get("usageType", "")
        desc = sku.get("description", "")
        regions = sku.get("serviceRegions", [])

        tier = _USAGE_TYPE_TO_TIER.get(usage_type)
        if tier is None:
            continue

        if _REGION not in regions:
            continue

        if any(p in desc for p in _SKIP_DESC_PATTERNS):
            continue

        hourly = _parse_unit_price(sku)
        if hourly is None or hourly == Decimal(0):
            continue

        gpu_slug = map_gcp_gpu(desc)
        if gpu_slug is None:
            logger.info("gcp skipping unmapped sku: %s", desc)
            continue

        out.append(
            ScrapedPrice(
                provider_slug="gcp",
                gpu_slug_hint=gpu_slug,
                tier=tier,
                region=_REGION,
                hourly_usd=hourly,
                available=True,
                raw={"sku_id": sku.get("skuId", ""), "description": desc, "usage_type": usage_type},
            )
        )

    if not out:
        raise ParserDriftError(f"gcp parser found no pricing rows (region={_REGION}, sha256={page_hash})")

    return out


class _HttpxTransport:
    """Minimal google.auth Transport backed by httpx (avoids requests dep)."""

    def __call__(
        self,
        url: str,
        method: str = "POST",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        **_: object,
    ) -> _HttpxResponse:
        response = httpx.request(method, url, content=body, headers=headers or {}, timeout=timeout)
        return _HttpxResponse(response)


class _HttpxResponse:
    def __init__(self, r: httpx.Response) -> None:
        self.status = r.status_code
        self.headers = dict(r.headers)
        self.data = r.content


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_gcp_skus() -> list[dict[str, Any]]:
    """Fetch all GPU SKUs from the GCP Cloud Billing Catalog API (paginated)."""
    import google.auth  # lazy import — avoids import-time failure without GCP creds in CI

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"])
    credentials.refresh(_HttpxTransport())  # type: ignore[no-untyped-call]

    token: str = credentials.token  # type: ignore[assignment]
    auth_headers = {"Authorization": f"Bearer {token}"}

    all_gpu_skus: list[dict[str, Any]] = []
    next_page_token: str | None = None

    while True:
        params: dict[str, str] = {"pageSize": "5000"}
        if next_page_token:
            params["pageToken"] = next_page_token

        response = httpx.get(_SKUS_URL, params=params, headers=auth_headers, timeout=30.0)
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        skus = data.get("skus", [])
        # Pre-filter to GPU resource group to reduce downstream noise
        all_gpu_skus.extend(s for s in skus if s.get("category", {}).get("resourceGroup") == "GPU")

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return all_gpu_skus


def scrape() -> list[ScrapedPrice]:
    return parse_gcp_skus(fetch_gcp_skus())
