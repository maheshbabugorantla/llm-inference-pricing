from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pricing.scrapers.runpod import parse_runpod_response

FIXTURE = Path(__file__).parent / "fixtures" / "runpod_gputypes_response.json"


def test_parse_yields_community_and_secure_tiers() -> None:
    payload = json.loads(FIXTURE.read_text())
    prices = parse_runpod_response(payload)
    tiers = {p.tier for p in prices if p.gpu_slug_hint == "NVIDIA H100 SXM"}
    assert "community" in tiers
    assert "secure" in tiers


def test_parse_yields_reserved_tiers_when_present() -> None:
    payload = json.loads(FIXTURE.read_text())
    prices = parse_runpod_response(payload)
    tiers = {p.tier for p in prices if p.gpu_slug_hint == "NVIDIA H100 SXM"}
    assert "reserved-1mo" in tiers
    assert "reserved-1yr" in tiers


def test_parse_drops_unmapped_gpus() -> None:
    payload = {
        "data": {
            "gpuTypes": [
                {
                    "displayName": "Unknown GPU",
                    "communityPrice": 1.0,
                    "securePrice": 2.0,
                    "memoryInGb": 80,
                }
            ]
        }
    }
    prices = parse_runpod_response(payload)
    assert len(prices) == 0


def test_parse_returns_decimals_not_floats() -> None:
    payload = json.loads(FIXTURE.read_text())
    prices = parse_runpod_response(payload)
    assert all(isinstance(p.hourly_usd, Decimal) for p in prices)


def test_parse_skips_null_price_fields() -> None:
    payload = json.loads(FIXTURE.read_text())
    prices = parse_runpod_response(payload)
    rtx_tiers = {p.tier for p in prices if p.gpu_slug_hint == "NVIDIA RTX 4090"}
    assert "reserved-1mo" not in rtx_tiers
    assert "community" in rtx_tiers
