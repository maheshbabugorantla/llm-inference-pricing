"""Tests for the ComputePrices.com API client (M10.T02 — rewritten for REST API).

Business scenario: the drift detection pipeline needs to compare curated
Tier 3 provider prices against the ComputePrices.com JSON API. These tests
prove that the client correctly maps GPU/provider slugs, parses API responses
into the format the drift service expects, rejects malformed responses with
ParserDriftError, and sends the optional API key header when configured.

All tests that would make real HTTP calls use unittest.mock to patch httpx.get.
No real network calls are made.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pricing.scrapers.base import ParserDriftError
from pricing.scrapers.computeprices import (
    BASE_URL,
    GPU_SLUG_MAP,
    fetch_computeprices_gpu_prices,
    map_provider_slug,
    parse_computeprices_response,
)

FIXTURE = Path(__file__).parent / "fixtures" / "computeprices_h100_sxm.json"

_FETCH_TARGET = "pricing.scrapers.computeprices.httpx.get"


# ---------------------------------------------------------------------------
# map_provider_slug — ComputePrices slug → our slug
# ---------------------------------------------------------------------------


def test_map_provider_slug_passes_through_matching_slugs() -> None:
    """Slugs that already match our internal slugs must be returned unchanged."""
    assert map_provider_slug("coreweave") == "coreweave"
    assert map_provider_slug("runpod") == "runpod"
    assert map_provider_slug("lambda") == "lambda"
    assert map_provider_slug("crusoe") == "crusoe"
    assert map_provider_slug("aws") == "aws"
    assert map_provider_slug("azure") == "azure"
    assert map_provider_slug("vast") == "vast"
    assert map_provider_slug("nebius") == "nebius"


def test_map_provider_slug_remaps_oracle_to_oci() -> None:
    """ComputePrices uses 'oracle'; our slug is 'oci' — must be remapped."""
    assert map_provider_slug("oracle") == "oci"


def test_map_provider_slug_remaps_google_to_gcp() -> None:
    """ComputePrices uses 'google'; our slug is 'gcp' — must be remapped."""
    assert map_provider_slug("google") == "gcp"


def test_map_provider_slug_returns_unknown_slug_unchanged() -> None:
    """Providers we don't track are returned as-is (filtered later by drift service)."""
    assert map_provider_slug("hypercloud-ai") == "hypercloud-ai"


# ---------------------------------------------------------------------------
# GPU_SLUG_MAP — our slugs → ComputePrices slugs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("our_slug", "their_slug"),
    [
        ("nvidia-h100-sxm-80", "h100"),
        ("nvidia-h100-pcie-80", "h100pcie"),
        ("nvidia-h200", "h200"),
        ("nvidia-a100-sxm-80", "a100sxm"),
        ("nvidia-a100-sxm-40", "a100sxm"),
        ("nvidia-l40s", "l40s"),
        ("nvidia-l4", "l4"),
        ("nvidia-rtx-4090", "rtx4090"),
        ("nvidia-rtx-6000-ada", "rtx6000ada"),
        ("nvidia-b200", "b200"),
        ("nvidia-t4", "t4"),
        ("nvidia-v100", "v100"),
        ("amd-mi300x", "mi300x"),
        ("amd-mi250x", "mi250x"),
    ],
)
def test_gpu_slug_map_covers_seeded_gpus(our_slug: str, their_slug: str) -> None:
    """Every GPU slug in seeds/gpus.yaml that ComputePrices tracks must be in GPU_SLUG_MAP."""
    assert GPU_SLUG_MAP.get(our_slug) == their_slug


# ---------------------------------------------------------------------------
# parse_computeprices_response — happy path via fixture
# ---------------------------------------------------------------------------


def test_parse_computeprices_response_returns_rows_for_known_providers() -> None:
    """Fixture contains CoreWeave, Crusoe, Lambda, RunPod, Oracle — all mapped providers.
    The parser must return one normalised row per item."""
    data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_response(data)
    provider_slugs = {r["provider"] for r in result}
    assert "coreweave" in provider_slugs
    assert "crusoe" in provider_slugs
    assert "lambda" in provider_slugs
    assert "runpod" in provider_slugs
    assert "oci" in provider_slugs  # oracle → oci


def test_parse_computeprices_response_applies_provider_slug_remap() -> None:
    """Oracle Cloud's provider_slug 'oracle' must be remapped to 'oci'."""
    data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_response(data)
    slugs = {r["provider"] for r in result}
    assert "oracle" not in slugs
    assert "oci" in slugs


def test_parse_computeprices_response_includes_unmapped_provider_slugs() -> None:
    """Unknown provider slugs like 'hypercloud-ai' are returned as-is.
    Filtering to known providers is the drift service's responsibility."""
    data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_response(data)
    slugs = {r["provider"] for r in result}
    assert "hypercloud-ai" in slugs


def test_parse_computeprices_response_hourly_usd_uses_price_per_hour_usd() -> None:
    """hourly_usd in the output must be price_per_hour_usd (per-GPU). The drift service
    derives a per-GPU curated rate from per_active_hour_usd by dividing by gpus_per_node."""
    data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_response(data)
    cw = next(r for r in result if r["provider"] == "coreweave")
    assert Decimal(cw["hourly_usd"]) == Decimal("2.39")


def test_parse_computeprices_response_hourly_usd_parseable_as_decimal() -> None:
    """Every hourly_usd value returned must be Decimal()-parseable without error."""
    data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_response(data)
    assert len(result) > 0
    for row in result:
        parsed = Decimal(row["hourly_usd"])
        assert parsed > Decimal("0")


def test_parse_computeprices_response_preserves_gpu_field() -> None:
    """Optional 'gpu' field must be forwarded so the drift service can log the exact SKU."""
    data = json.loads(FIXTURE.read_text())
    result = parse_computeprices_response(data)
    for row in result:
        assert "gpu" in row


def test_parse_computeprices_response_skips_null_price() -> None:
    """Items with price_per_hour_usd=null must be skipped — no price to compare."""
    data = [
        {
            "provider_slug": "runpod",
            "gpu": "H100 SXM",
            "price_per_hour_usd": None,
            "total_hourly_usd": None,
        }
    ]
    result = parse_computeprices_response(data)
    assert result == []


# ---------------------------------------------------------------------------
# parse_computeprices_response — failure / drift modes
# ---------------------------------------------------------------------------


def test_parse_computeprices_response_carries_source_url_when_present() -> None:
    """source_url from the API item must be forwarded so the drift service can use it
    as the alert source URL instead of constructing a fabricated fallback."""
    data = [
        {
            "provider_slug": "coreweave",
            "gpu": "H100 SXM",
            "price_per_hour_usd": 2.39,
            "source_url": "https://computeprices.com/providers/coreweave/h100",
        }
    ]
    result = parse_computeprices_response(data)
    assert len(result) == 1
    assert result[0]["source_url"] == "https://computeprices.com/providers/coreweave/h100"


def test_parse_computeprices_response_omits_source_url_when_absent() -> None:
    """When source_url is absent from the API item, the key must not appear in the output."""
    data = [
        {
            "provider_slug": "coreweave",
            "gpu": "H100 SXM",
            "price_per_hour_usd": 2.39,
        }
    ]
    result = parse_computeprices_response(data)
    assert len(result) == 1
    assert "source_url" not in result[0]


def test_parse_computeprices_response_raises_drift_error_when_data_is_empty() -> None:
    """Empty data list means the API returned no listings — raise ParserDriftError
    so the drift service doesn't silently skip all alerts."""
    with pytest.raises(ParserDriftError):
        parse_computeprices_response([])


def test_parse_computeprices_response_raises_drift_error_when_provider_slug_missing() -> None:
    """If an item lacks 'provider_slug' the API schema has changed — fail loudly."""
    data = [{"gpu": "H100 SXM", "price_per_hour_usd": 2.39}]
    with pytest.raises(ParserDriftError, match="missing required fields"):
        parse_computeprices_response(data)


def test_parse_computeprices_response_raises_drift_error_when_provider_slug_is_null() -> None:
    """provider_slug=None coerces to 'None' string and silently breaks provider matching.
    Must raise ParserDriftError so the task fails loudly instead of silently missing alerts."""
    data = [{"provider_slug": None, "price_per_hour_usd": 2.39, "gpu": "H100"}]
    with pytest.raises(ParserDriftError, match="null or blank provider_slug"):
        parse_computeprices_response(data)


def test_parse_computeprices_response_raises_drift_error_when_provider_slug_is_blank() -> None:
    """provider_slug='' silently breaks provider matching — must raise ParserDriftError."""
    data = [{"provider_slug": "", "price_per_hour_usd": 2.39, "gpu": "H100"}]
    with pytest.raises(ParserDriftError, match="null or blank provider_slug"):
        parse_computeprices_response(data)


def test_parse_computeprices_response_raises_drift_error_when_provider_slug_is_non_string() -> None:
    """provider_slug=0 (integer) must not pass as a valid slug — schema requires a non-empty string.
    Allowing it would coerce to '0' and silently miss all drift matches."""
    data = [{"provider_slug": 0, "price_per_hour_usd": 2.39, "gpu": "H100"}]
    with pytest.raises(ParserDriftError, match="null or blank provider_slug"):
        parse_computeprices_response(data)


def test_parse_computeprices_response_raises_drift_error_when_price_is_non_numeric() -> None:
    """Non-numeric price_per_hour_usd must raise ParserDriftError at the parser boundary."""
    data = [{"provider_slug": "coreweave", "price_per_hour_usd": "N/A", "gpu": "H100"}]
    with pytest.raises(ParserDriftError, match="non-numeric"):
        parse_computeprices_response(data)


# ---------------------------------------------------------------------------
# fetch_computeprices_gpu_prices — HTTP call and auth
# ---------------------------------------------------------------------------


def _make_api_response(items: list[dict]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": items, "meta": {"count": len(items)}}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def test_fetch_computeprices_gpu_prices_calls_correct_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client must call /api/v1/gpu-prices with gpu=h100 and pricing_type=on_demand
    when given our slug nvidia-h100-sxm-80."""
    monkeypatch.delenv("COMPUTEPRICES_API_KEY", raising=False)
    mock_item = {
        "provider_slug": "coreweave",
        "gpu": "H100 SXM",
        "price_per_hour_usd": 2.39,
        "total_hourly_usd": 19.12,
    }
    with patch(_FETCH_TARGET, return_value=_make_api_response([mock_item])) as mock_get:
        result = fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

    call_kwargs = mock_get.call_args
    assert "gpu-prices" in call_kwargs.args[0]
    assert call_kwargs.kwargs["params"]["gpu"] == "h100"
    assert call_kwargs.kwargs["params"]["pricing_type"] == "on_demand"
    assert len(result) == 1
    assert result[0]["provider"] == "coreweave"


def test_fetch_computeprices_gpu_prices_sends_auth_header_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When COMPUTEPRICES_API_KEY is set, the request must include an Authorization
    header so we use the free tier (5,000 req/hr) instead of the public tier (60/hr)."""
    monkeypatch.setenv("COMPUTEPRICES_API_KEY", "cp_live_testkey123")
    mock_item = {
        "provider_slug": "coreweave",
        "gpu": "H100 SXM",
        "price_per_hour_usd": 2.39,
        "total_hourly_usd": 19.12,
    }
    with patch(_FETCH_TARGET, return_value=_make_api_response([mock_item])) as mock_get:
        fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

    headers = mock_get.call_args.kwargs["headers"]
    assert headers.get("Authorization") == "Bearer cp_live_testkey123"


def test_fetch_computeprices_gpu_prices_omits_auth_header_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without COMPUTEPRICES_API_KEY, no Authorization header should be sent."""
    monkeypatch.delenv("COMPUTEPRICES_API_KEY", raising=False)
    mock_item = {
        "provider_slug": "coreweave",
        "price_per_hour_usd": 2.39,
        "total_hourly_usd": 19.12,
    }
    with patch(_FETCH_TARGET, return_value=_make_api_response([mock_item])) as mock_get:
        fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

    headers = mock_get.call_args.kwargs["headers"]
    assert "Authorization" not in headers


def test_fetch_computeprices_gpu_prices_returns_empty_list_for_unmapped_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPUs not in GPU_SLUG_MAP return [] immediately — no HTTP call made."""
    monkeypatch.delenv("COMPUTEPRICES_API_KEY", raising=False)
    with patch(_FETCH_TARGET) as mock_get:
        result = fetch_computeprices_gpu_prices("nvidia-p100")

    assert result == []
    mock_get.assert_not_called()


def test_fetch_computeprices_gpu_prices_raises_parser_drift_error_on_non_dict_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the API returns a non-dict body (e.g. a list or HTML), raise ParserDriftError
    so the drift service catches it at the boundary and notifies Sentry."""
    monkeypatch.delenv("COMPUTEPRICES_API_KEY", raising=False)
    mock_resp = MagicMock()
    mock_resp.json.return_value = [{"provider_slug": "coreweave", "price_per_hour_usd": 2.39}]
    mock_resp.raise_for_status.return_value = None
    with patch(_FETCH_TARGET, return_value=mock_resp):
        with pytest.raises(ParserDriftError, match="unexpected response shape"):
            fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")


def test_fetch_computeprices_gpu_prices_raises_parser_drift_error_when_data_is_not_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the API returns a dict but 'data' is not a list, raise ParserDriftError."""
    monkeypatch.delenv("COMPUTEPRICES_API_KEY", raising=False)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": "unexpected string", "meta": {}}
    mock_resp.raise_for_status.return_value = None
    with patch(_FETCH_TARGET, return_value=mock_resp):
        with pytest.raises(ParserDriftError, match="unexpected response shape"):
            fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")


def test_fetch_computeprices_gpu_prices_raises_parser_drift_error_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the response body is not valid JSON (e.g. HTML error page with 200 status),
    raise ParserDriftError — not ValueError — so the task handles it via the right path."""
    monkeypatch.delenv("COMPUTEPRICES_API_KEY", raising=False)
    mock_resp = MagicMock()
    mock_resp.json.side_effect = ValueError("No JSON object could be decoded")
    mock_resp.raise_for_status.return_value = None
    with patch(_FETCH_TARGET, return_value=mock_resp):
        with pytest.raises(ParserDriftError, match="non-JSON"):
            fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")


def test_fetch_computeprices_gpu_prices_raises_parser_drift_error_when_data_item_not_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the API returns a list where items are not dicts, raise ParserDriftError
    so the drift service handles it cleanly rather than getting an AttributeError."""
    monkeypatch.delenv("COMPUTEPRICES_API_KEY", raising=False)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": ["not-a-dict", 42], "meta": {}}
    mock_resp.raise_for_status.return_value = None
    with patch(_FETCH_TARGET, return_value=mock_resp):
        with pytest.raises(ParserDriftError, match="not a dict"):
            fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")


def test_base_url_is_public_constant() -> None:
    """BASE_URL must be importable as a public constant for use by the drift service."""
    assert BASE_URL == "https://computeprices.com/api/v1"
