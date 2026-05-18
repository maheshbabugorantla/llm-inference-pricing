from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pricing.scrapers.vast import parse_vast_response

FIXTURE = Path(__file__).parent / "fixtures" / "vast_bundles_sample.json"


def test_vast_aggregates_bundles_to_p50_hourly() -> None:
    payload = json.loads(FIXTURE.read_text())
    prices = parse_vast_response(payload)
    h100_od = next(p for p in prices if p.gpu_slug_hint == "H100 SXM" and p.tier == "on_demand")
    # on-demand bundles: 2.05, 2.15, 2.25, 2.40 → p50 ≈ median of 4 = (2.15+2.25)/2 = 2.20
    assert Decimal("2.0") < h100_od.hourly_usd < Decimal("2.5")


def test_vast_separates_on_demand_and_interruptible() -> None:
    payload = json.loads(FIXTURE.read_text())
    prices = parse_vast_response(payload)
    rtx_tiers = {p.tier for p in prices if p.gpu_slug_hint == "RTX 4090"}
    assert "on_demand" in rtx_tiers
    assert "interruptible" in rtx_tiers


def test_vast_drops_bundles_for_unmapped_gpus() -> None:
    payload = json.loads(FIXTURE.read_text())
    prices = parse_vast_response(payload)
    slugs = {p.gpu_slug_hint for p in prices}
    assert "Unknown Titan 9000" not in slugs


def test_vast_handles_empty_bundle_list_gracefully() -> None:
    prices = parse_vast_response({"offers": []})
    assert prices == []


def test_vast_raw_payload_contains_full_distribution() -> None:
    payload = json.loads(FIXTURE.read_text())
    prices = parse_vast_response(payload)
    rtx_od = next(p for p in prices if p.gpu_slug_hint == "RTX 4090" and p.tier == "on_demand")
    assert "p50" in rtx_od.raw
    assert "p90" in rtx_od.raw
    assert "min" in rtx_od.raw
    assert "count" in rtx_od.raw
