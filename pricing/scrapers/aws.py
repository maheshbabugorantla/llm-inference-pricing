"""AWS GPU pricing scraper.

Pricing sources:
  - Price List bulk JSON: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.json
    (publicly accessible — no credentials required)
  - Capacity Block Offerings: EC2 describe_capacity_block_offerings API
    (requires AWS credentials; gracefully skipped if absent)

GPU-per-instance division: AWS prices per *instance*, not per GPU.
For example, p5.48xlarge is 8x H100 — a $98/hr instance price becomes $12.25/hr per GPU.
The per-GPU division is applied in parse_aws_prices().
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from pricing.scrapers.base import ParserDriftError, ScrapedPrice

logger = logging.getLogger("pricing.scrapers.aws")

# Instance type → (gpu_slug, gpus_per_instance)
_INSTANCE_GPU_MAP: dict[str, tuple[str, int]] = {
    # H100 families
    "p5.48xlarge": ("nvidia-h100-sxm-80", 8),
    "p5e.48xlarge": ("nvidia-h100-sxm-80", 8),
    # A100 families
    "p4d.24xlarge": ("nvidia-a100-sxm-40", 8),
    "p4de.24xlarge": ("nvidia-a100-sxm-80", 8),
    # L40S
    "p5n.48xlarge": ("nvidia-l40s", 8),
    # A10G families
    "g5.xlarge": ("nvidia-a10g", 1),
    "g5.2xlarge": ("nvidia-a10g", 1),
    "g5.4xlarge": ("nvidia-a10g", 1),
    "g5.8xlarge": ("nvidia-a10g", 1),
    "g5.12xlarge": ("nvidia-a10g", 4),
    "g5.16xlarge": ("nvidia-a10g", 1),
    "g5.24xlarge": ("nvidia-a10g", 4),
    "g5.48xlarge": ("nvidia-a10g", 8),
    # L4 families
    "g6.xlarge": ("nvidia-l4", 1),
    "g6.2xlarge": ("nvidia-l4", 1),
    "g6.4xlarge": ("nvidia-l4", 1),
    "g6.8xlarge": ("nvidia-l4", 1),
    "g6.12xlarge": ("nvidia-l4", 4),
    "g6.16xlarge": ("nvidia-l4", 1),
    "g6.24xlarge": ("nvidia-l4", 4),
    "g6.48xlarge": ("nvidia-l4", 8),
    # V100 families
    "p3.2xlarge": ("nvidia-v100", 1),
    "p3.8xlarge": ("nvidia-v100", 4),
    "p3.16xlarge": ("nvidia-v100", 8),
    "p3dn.24xlarge": ("nvidia-v100", 8),
    # T4 families
    "g4dn.xlarge": ("nvidia-t4", 1),
    "g4dn.2xlarge": ("nvidia-t4", 1),
    "g4dn.4xlarge": ("nvidia-t4", 1),
    "g4dn.8xlarge": ("nvidia-t4", 1),
    "g4dn.12xlarge": ("nvidia-t4", 4),
    "g4dn.16xlarge": ("nvidia-t4", 1),
    "g4dn.metal": ("nvidia-t4", 8),
}

_KNOWN_INSTANCE_TYPES: frozenset[str] = frozenset(_INSTANCE_GPU_MAP)

_DEFAULT_REGION = "us-east-1"


def map_aws_gpu(instance_type: str) -> str | None:
    entry = _INSTANCE_GPU_MAP.get(instance_type)
    return entry[0] if entry else None


def _gpus_per_instance(instance_type: str) -> int:
    entry = _INSTANCE_GPU_MAP.get(instance_type)
    return entry[1] if entry else 1


def parse_aws_prices(products: list[dict[str, Any]]) -> list[ScrapedPrice]:
    """Parse Price List API product dicts → list[ScrapedPrice].

    Each product must contain: attributes.instanceType, OnDemand term with USD per-hour price.
    Raises ParserDriftError if no rows are produced.
    """
    payload_hash = hashlib.sha256(json.dumps(products, sort_keys=True).encode()).hexdigest()
    logger.info("aws_price_list sha256=%s count=%d", payload_hash, len(products))

    out: list[ScrapedPrice] = []
    for product in products:
        attrs = product.get("attributes", {})
        instance_type = attrs.get("instanceType", "")
        if instance_type not in _INSTANCE_GPU_MAP:
            logger.info("aws skipping unmapped instance type: %s", instance_type)
            continue

        gpu_slug, num_gpus = _INSTANCE_GPU_MAP[instance_type]
        region = attrs.get("regionCode", _DEFAULT_REGION)

        hourly_usd: Decimal | None = None
        for _term_key, term_val in product.get("terms", {}).get("OnDemand", {}).items():
            for _pd_key, pd in term_val.get("priceDimensions", {}).items():
                price_per_unit = pd.get("pricePerUnit", {}).get("USD", "")
                if price_per_unit:
                    hourly_usd = Decimal(price_per_unit)
                    break
            if hourly_usd is not None:
                break

        if hourly_usd is None or hourly_usd == Decimal(0):
            continue

        per_gpu_hourly = hourly_usd / Decimal(num_gpus)

        out.append(
            ScrapedPrice(
                provider_slug="aws",
                gpu_slug_hint=gpu_slug,
                tier="on_demand",
                region=region,
                hourly_usd=per_gpu_hourly,
                available=True,
                raw={
                    "instance_type": instance_type,
                    "num_gpus": num_gpus,
                    "instance_hourly_usd": str(hourly_usd),
                },
            )
        )

    if not out:
        raise ParserDriftError(f"aws parser found no pricing rows (sha256={payload_hash})")

    return out


def parse_aws_capacity_blocks(offerings: list[dict[str, Any]]) -> list[ScrapedPrice]:
    """Parse describe_capacity_block_offerings response → list[ScrapedPrice].

    Converts upfront fee / (instance_count * duration_hours) → per-GPU hourly equivalent.
    Tier: capacity-block-{duration_days}d
    """
    if not offerings:
        return []

    out: list[ScrapedPrice] = []
    for offering in offerings:
        instance_type = offering.get("InstanceType", "")
        if instance_type not in _INSTANCE_GPU_MAP:
            logger.info("aws capacity block skipping unmapped instance type: %s", instance_type)
            continue

        gpu_slug, num_gpus = _INSTANCE_GPU_MAP[instance_type]
        upfront_fee = offering.get("UpfrontFee", "0")
        instance_count = offering.get("InstanceCount", 1)
        duration_hours = offering.get("CapacityBlockDurationHours", 0)

        if not duration_hours or not instance_count:
            continue

        upfront = Decimal(str(upfront_fee))
        per_instance_hourly = upfront / (Decimal(instance_count) * Decimal(duration_hours))
        per_gpu_hourly = per_instance_hourly / Decimal(num_gpus)

        duration_days = duration_hours // 24
        tier = f"capacity-block-{duration_days}d"

        out.append(
            ScrapedPrice(
                provider_slug="aws",
                gpu_slug_hint=gpu_slug,
                tier=tier,
                region=offering.get("AvailabilityZone", _DEFAULT_REGION),
                hourly_usd=per_gpu_hourly,
                available=True,
                raw={
                    "instance_type": instance_type,
                    "instance_count": instance_count,
                    "duration_hours": duration_hours,
                    "upfront_fee_usd": str(upfront),
                },
            )
        )

    return out


_PRICING_URL = (
    "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.json"
)

_ONDEMAND_FILTERS = {
    "operatingSystem": "Linux",
    "tenancy": "Shared",
    "capacitystatus": "Used",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_aws_ondemand_skus() -> list[dict[str, Any]]:
    """Fetch GPU instance on-demand prices from the public AWS Price List bulk JSON.

    No credentials required — the pricing endpoint is publicly accessible.
    Returns a list of dicts with 'attributes' and 'terms' keys, matching the
    format expected by parse_aws_prices().
    """
    response = httpx.get(_PRICING_URL, timeout=120.0)
    response.raise_for_status()
    data: dict[str, Any] = response.json()

    raw_products: dict[str, Any] = data.get("products", {})
    ondemand_terms: dict[str, Any] = data.get("terms", {}).get("OnDemand", {})

    products: list[dict[str, Any]] = []
    for sku, product in raw_products.items():
        attrs = product.get("attributes", {})
        if attrs.get("instanceType", "") not in _INSTANCE_GPU_MAP:
            continue
        if any(attrs.get(k) != v for k, v in _ONDEMAND_FILTERS.items()):
            continue
        products.append(
            {
                "attributes": attrs,
                "terms": {"OnDemand": ondemand_terms.get(sku, {})},
            }
        )

    return products


# AWS capacity blocks are sold in discrete durations. Querying each duration
# separately because describe_capacity_block_offerings requires CapacityDurationHours.
_CAPACITY_BLOCK_DURATIONS_HOURS = [24, 48, 72, 168, 336]  # 1d, 2d, 3d, 7d, 14d


def fetch_aws_capacity_blocks(
    instance_type: str = "p5.48xlarge",
    num_instances: int = 1,
) -> list[dict[str, Any]]:
    """Fetch capacity block offerings via EC2 API (requires AWS credentials).

    Returns an empty list and logs a warning if credentials are not available.
    Iterates over common block durations because CapacityDurationHours is required.
    """
    from datetime import datetime, timedelta

    try:
        import boto3
        import botocore.exceptions
    except ImportError:
        return []

    try:
        ec2 = boto3.client("ec2", region_name=_DEFAULT_REGION)
        start = datetime.now(tz=UTC)
        end = start + timedelta(days=60)

        offerings: list[dict[str, Any]] = []
        for duration_hours in _CAPACITY_BLOCK_DURATIONS_HOURS:
            paginator = ec2.get_paginator("describe_capacity_block_offerings")
            try:
                for page in paginator.paginate(
                    InstanceType=instance_type,
                    CapacityDurationHours=duration_hours,
                    InstanceCount=num_instances,
                    StartDateRange=start,
                    EndDateRange=end,
                ):
                    offerings.extend(page.get("CapacityBlockOfferings", []))
            except botocore.exceptions.ClientError as exc:
                logger.debug("aws capacity block duration=%dh error: %s", duration_hours, exc)
                continue
        return offerings
    except botocore.exceptions.NoCredentialsError:
        logger.info("aws capacity blocks skipped: no credentials configured")
        return []


def scrape() -> list[ScrapedPrice]:
    products = fetch_aws_ondemand_skus()
    prices = parse_aws_prices(products)
    cb_offerings = fetch_aws_capacity_blocks()
    prices.extend(parse_aws_capacity_blocks(cb_offerings))
    return prices
