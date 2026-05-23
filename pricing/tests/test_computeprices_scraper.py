"""Tests for the ComputePrices.com scraper stub (M10.T02).

Business scenario: the drift detection pipeline needs to compare curated
Tier 3 provider prices against an external aggregator. These tests prove
that the parser normalises aggregator rows into the format the drift service
expects, rejects structurally broken pages with ParserDriftError, and that
the HTTP fetch is deliberately disabled until TOS is verified.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from pricing.scrapers.base import ParserDriftError
from pricing.scrapers.computeprices import (
    fetch_computeprices_table,
    map_computeprices_provider,
    parse_computeprices_table,
)

FIXTURE = Path(__file__).parent / "fixtures" / "computeprices_h100_sxm.json"


# ---------------------------------------------------------------------------
# map_computeprices_provider
# ---------------------------------------------------------------------------


def test_map_computeprices_provider_returns_slug_for_known_provider() -> None:
    """CoreWeave is a Tier 3 manual-curation provider; the drift service must map it."""
    assert map_computeprices_provider("CoreWeave") == "coreweave"


def test_map_computeprices_provider_returns_none_for_unknown_provider() -> None:
    """An aggregator listing a cloud we don't track must be silently skipped, not error."""
    assert map_computeprices_provider("HyperCloud AI") is None


@pytest.mark.parametrize(
    "display_name,expected_slug",
    [
        ("RunPod", "runpod"),
        ("Lambda Labs", "lambda"),
        ("Vast.ai", "vast"),
        ("Nebius", "nebius"),
        ("Google Cloud", "gcp"),
        ("AWS", "aws"),
        ("Microsoft Azure", "azure"),
        ("CoreWeave", "coreweave"),
        ("Crusoe Energy", "crusoe"),
        ("Oracle Cloud", "oci"),
    ],
)
def test_provider_map_covers_all_seeded_providers(display_name: str, expected_slug: str) -> None:
    """Every provider slug in seeds/providers.yaml must be reachable via PROVIDER_MAP.

    Note: this list is a hardcoded copy of providers.yaml slugs. If a new provider
    is added to seeds/providers.yaml and PROVIDER_MAP, add it here too.
    """
    assert map_computeprices_provider(display_name) == expected_slug


# ---------------------------------------------------------------------------
# fetch_computeprices_table — stub behaviour
# ---------------------------------------------------------------------------


def test_fetch_computeprices_table_raises_not_implemented_until_tos_verified() -> None:
    """The HTTP fetch must be disabled by default — running it in production before
    TOS review would be a compliance violation."""
    with pytest.raises(NotImplementedError, match="TOS not verified"):
        fetch_computeprices_table("nvidia-h100-sxm-80")


# ---------------------------------------------------------------------------
# parse_computeprices_table — happy path via fixture
# ---------------------------------------------------------------------------


def test_parse_computeprices_table_returns_rows_for_h100_from_known_providers() -> None:
    """Fixture contains CoreWeave, Crusoe, Lambda, RunPod, Oracle — all mapped providers.
    The parser must return one normalised row per mapped provider row."""
    rows_data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_table(rows_data)
    provider_slugs = {r["provider"] for r in result}
    assert "coreweave" in provider_slugs
    assert "crusoe" in provider_slugs
    assert "lambda" in provider_slugs
    assert "runpod" in provider_slugs
    assert "oci" in provider_slugs


def test_parse_computeprices_table_drops_unmapped_provider() -> None:
    """HyperCloud AI is in the fixture but not in PROVIDER_MAP — must be silently dropped."""
    rows_data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_table(rows_data)
    provider_slugs = {r["provider"] for r in result}
    assert "HyperCloud AI" not in provider_slugs


def test_parse_computeprices_table_hourly_usd_is_parseable_as_decimal() -> None:
    """Every hourly_usd value returned must be a string that Decimal() accepts without error.
    This is the invariant the drift service relies on when computing drift_pct."""
    rows_data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_table(rows_data)
    assert len(result) > 0
    for row in result:
        parsed = Decimal(row["hourly_usd"])
        assert parsed > Decimal("0")


def test_parse_computeprices_table_preserves_gpu_field_when_present() -> None:
    """Optional 'gpu' field must be forwarded so the drift service can log the exact SKU."""
    rows_data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_table(rows_data)
    for row in result:
        assert "gpu" in row


def test_parse_computeprices_table_omits_region_when_empty_string() -> None:
    """Blank region values must be omitted — forwarding an empty string produces
    misleading drift logs ('region: ''') as if a real region were specified."""
    rows_data = [{"provider": "RunPod", "hourly_usd": "2.69", "gpu": "H100", "region": ""}]
    result = parse_computeprices_table(rows_data)
    assert len(result) == 1
    assert "region" not in result[0]


def test_parse_computeprices_table_preserves_region_when_non_empty() -> None:
    """Non-empty region strings must be forwarded intact."""
    rows_data = [{"provider": "CoreWeave", "hourly_usd": "2.39", "gpu": "H100", "region": "us-east1"}]
    result = parse_computeprices_table(rows_data)
    assert result[0]["region"] == "us-east1"


# ---------------------------------------------------------------------------
# parse_computeprices_table — failure / drift modes
# ---------------------------------------------------------------------------


def test_parse_computeprices_table_raises_drift_error_when_rows_are_empty() -> None:
    """An empty row list means the page structure changed — must raise ParserDriftError,
    not silently return an empty list (which would suppress all drift alerts)."""
    with pytest.raises(ParserDriftError):
        parse_computeprices_table([])


def test_parse_computeprices_table_raises_drift_error_when_provider_key_missing() -> None:
    """If a row lacks 'provider' the page structure has drifted — fail loudly."""
    rows_data = [{"hourly_usd": "2.39", "gpu": "H100 SXM"}]
    with pytest.raises(ParserDriftError, match="missing required keys"):
        parse_computeprices_table(rows_data)


def test_parse_computeprices_table_raises_drift_error_when_hourly_usd_key_missing() -> None:
    """If a row lacks 'hourly_usd' the page structure has drifted — fail loudly."""
    rows_data = [{"provider": "CoreWeave", "gpu": "H100 SXM"}]
    with pytest.raises(ParserDriftError, match="missing required keys"):
        parse_computeprices_table(rows_data)


def test_parse_computeprices_table_handles_all_unmapped_providers_by_returning_empty_list() -> None:
    """All unmapped providers yields empty list — valid (no Tier 3 match), not a drift error."""
    rows_data = [
        {"provider": "HyperCloud AI", "hourly_usd": "2.50", "gpu": "H100"},
        {"provider": "MegaGPU Co", "hourly_usd": "2.75", "gpu": "H100"},
    ]
    result = parse_computeprices_table(rows_data)
    assert result == []


def test_parse_computeprices_table_raises_drift_error_when_hourly_usd_is_non_numeric() -> None:
    """Non-numeric hourly_usd (e.g. 'N/A', '$2.39') must raise ParserDriftError at the
    parser boundary, not propagate an InvalidOperation into the drift service."""
    rows_data = [{"provider": "CoreWeave", "hourly_usd": "N/A", "gpu": "H100"}]
    with pytest.raises(ParserDriftError, match="non-numeric hourly_usd"):
        parse_computeprices_table(rows_data)
