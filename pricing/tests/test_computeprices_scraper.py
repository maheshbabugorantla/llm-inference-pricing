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
import os
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _make_api_response(items: list[dict]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": items, "meta": {"count": len(items)}}
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# map_provider_slug — ComputePrices slug → our slug
# ---------------------------------------------------------------------------


class MapProviderSlugTest(unittest.TestCase):
    def test_map_provider_slug_passes_through_matching_slugs(self) -> None:
        """Slugs that already match our internal slugs must be returned unchanged."""
        self.assertEqual(map_provider_slug("coreweave"), "coreweave")
        self.assertEqual(map_provider_slug("runpod"), "runpod")
        self.assertEqual(map_provider_slug("lambda"), "lambda")
        self.assertEqual(map_provider_slug("crusoe"), "crusoe")
        self.assertEqual(map_provider_slug("aws"), "aws")
        self.assertEqual(map_provider_slug("azure"), "azure")
        self.assertEqual(map_provider_slug("vast"), "vast")
        self.assertEqual(map_provider_slug("nebius"), "nebius")

    def test_map_provider_slug_remaps_oracle_to_oci(self) -> None:
        """ComputePrices uses 'oracle'; our slug is 'oci' — must be remapped."""
        self.assertEqual(map_provider_slug("oracle"), "oci")

    def test_map_provider_slug_remaps_google_to_gcp(self) -> None:
        """ComputePrices uses 'google'; our slug is 'gcp' — must be remapped."""
        self.assertEqual(map_provider_slug("google"), "gcp")

    def test_map_provider_slug_returns_unknown_slug_unchanged(self) -> None:
        """Providers we don't track are returned as-is (filtered later by drift service)."""
        self.assertEqual(map_provider_slug("hypercloud-ai"), "hypercloud-ai")


# ---------------------------------------------------------------------------
# GPU_SLUG_MAP — our slugs → ComputePrices slugs
# ---------------------------------------------------------------------------


class GpuSlugMapTest(unittest.TestCase):
    def test_gpu_slug_map_covers_seeded_gpus(self) -> None:
        """Every GPU slug in seeds/gpus.yaml that ComputePrices tracks must be in GPU_SLUG_MAP."""
        cases = [
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
        ]
        for our_slug, their_slug in cases:
            with self.subTest(our_slug=our_slug, their_slug=their_slug):
                self.assertEqual(GPU_SLUG_MAP.get(our_slug), their_slug)


# ---------------------------------------------------------------------------
# parse_computeprices_response — happy path via fixture
# ---------------------------------------------------------------------------


class ParseComputepricesResponseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(FIXTURE.read_text())

    def test_parse_computeprices_response_returns_rows_for_known_providers(self) -> None:
        """Fixture contains CoreWeave, Crusoe, Lambda, RunPod, Oracle — all mapped providers.
        The parser must return one normalised row per item."""
        result = parse_computeprices_response(self.data)
        provider_slugs = {r["provider"] for r in result}
        self.assertIn("coreweave", provider_slugs)
        self.assertIn("crusoe", provider_slugs)
        self.assertIn("lambda", provider_slugs)
        self.assertIn("runpod", provider_slugs)
        self.assertIn("oci", provider_slugs)  # oracle → oci

    def test_parse_computeprices_response_applies_provider_slug_remap(self) -> None:
        """Oracle Cloud's provider_slug 'oracle' must be remapped to 'oci'."""
        result = parse_computeprices_response(self.data)
        slugs = {r["provider"] for r in result}
        self.assertNotIn("oracle", slugs)
        self.assertIn("oci", slugs)

    def test_parse_computeprices_response_includes_unmapped_provider_slugs(self) -> None:
        """Unknown provider slugs like 'hypercloud-ai' are returned as-is.
        Filtering to known providers is the drift service's responsibility."""
        result = parse_computeprices_response(self.data)
        slugs = {r["provider"] for r in result}
        self.assertIn("hypercloud-ai", slugs)

    def test_parse_computeprices_response_hourly_usd_uses_price_per_hour_usd(self) -> None:
        """hourly_usd in the output must be price_per_hour_usd (per-GPU). The drift service
        derives a per-GPU curated rate from per_active_hour_usd by dividing by gpus_per_node."""
        result = parse_computeprices_response(self.data)
        cw = next(r for r in result if r["provider"] == "coreweave")
        self.assertEqual(Decimal(cw["hourly_usd"]), Decimal("2.39"))

    def test_parse_computeprices_response_hourly_usd_parseable_as_decimal(self) -> None:
        """Every hourly_usd value returned must be Decimal()-parseable without error."""
        result = parse_computeprices_response(self.data)
        self.assertGreater(len(result), 0)
        for row in result:
            parsed = Decimal(row["hourly_usd"])
            self.assertGreater(parsed, Decimal("0"))

    def test_parse_computeprices_response_includes_gpu_field_when_present(self) -> None:
        """When the API item has a non-null 'gpu' field it must appear in the output row."""
        data = [{"provider_slug": "coreweave", "gpu": "H100 SXM", "price_per_hour_usd": 2.39}]
        result = parse_computeprices_response(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["gpu"], "H100 SXM")

    def test_parse_computeprices_response_omits_gpu_field_when_absent(self) -> None:
        """'gpu' is optional — items without it must produce rows without the key."""
        data = [{"provider_slug": "coreweave", "price_per_hour_usd": 2.39}]
        result = parse_computeprices_response(data)
        self.assertEqual(len(result), 1)
        self.assertNotIn("gpu", result[0])

    def test_parse_computeprices_response_omits_gpu_field_when_null(self) -> None:
        """'gpu': null must be treated as absent — no 'gpu' key in the output row."""
        data = [{"provider_slug": "coreweave", "gpu": None, "price_per_hour_usd": 2.39}]
        result = parse_computeprices_response(data)
        self.assertEqual(len(result), 1)
        self.assertNotIn("gpu", result[0])

    def test_parse_computeprices_response_skips_null_price(self) -> None:
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
        self.assertEqual(result, [])

    def test_parse_computeprices_response_carries_source_url_when_present(self) -> None:
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
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_url"], "https://computeprices.com/providers/coreweave/h100")

    def test_parse_computeprices_response_omits_source_url_when_absent(self) -> None:
        """When source_url is absent from the API item, the key must not appear in the output."""
        data = [{"provider_slug": "coreweave", "gpu": "H100 SXM", "price_per_hour_usd": 2.39}]
        result = parse_computeprices_response(data)
        self.assertEqual(len(result), 1)
        self.assertNotIn("source_url", result[0])

    def test_parse_computeprices_response_raises_drift_error_when_data_is_empty(self) -> None:
        """Empty data list means the API returned no listings — raise ParserDriftError
        so the drift service doesn't silently skip all alerts."""
        with self.assertRaises(ParserDriftError):
            parse_computeprices_response([])

    def test_parse_computeprices_response_raises_drift_error_when_provider_slug_missing(self) -> None:
        """If an item lacks 'provider_slug' the API schema has changed — fail loudly."""
        data = [{"gpu": "H100 SXM", "price_per_hour_usd": 2.39}]
        with self.assertRaisesRegex(ParserDriftError, "missing required fields"):
            parse_computeprices_response(data)

    def test_parse_computeprices_response_raises_drift_error_when_provider_slug_is_null(self) -> None:
        """provider_slug=None coerces to 'None' string and silently breaks provider matching.
        Must raise ParserDriftError so the task fails loudly instead of silently missing alerts."""
        data = [{"provider_slug": None, "price_per_hour_usd": 2.39, "gpu": "H100"}]
        with self.assertRaisesRegex(ParserDriftError, "null or blank provider_slug"):
            parse_computeprices_response(data)

    def test_parse_computeprices_response_raises_drift_error_when_provider_slug_is_blank(self) -> None:
        """provider_slug='' silently breaks provider matching — must raise ParserDriftError."""
        data = [{"provider_slug": "", "price_per_hour_usd": 2.39, "gpu": "H100"}]
        with self.assertRaisesRegex(ParserDriftError, "null or blank provider_slug"):
            parse_computeprices_response(data)

    def test_parse_computeprices_response_raises_drift_error_when_provider_slug_is_non_string(self) -> None:
        """provider_slug=0 (integer) must not pass as a valid slug — schema requires a non-empty string.
        Allowing it would coerce to '0' and silently miss all drift matches."""
        data = [{"provider_slug": 0, "price_per_hour_usd": 2.39, "gpu": "H100"}]
        with self.assertRaisesRegex(ParserDriftError, "null or blank provider_slug"):
            parse_computeprices_response(data)

    def test_parse_computeprices_response_raises_drift_error_when_provider_slug_is_whitespace_only(
        self,
    ) -> None:
        """provider_slug='  ' passes a truthiness check but silently breaks provider matching.
        Must be rejected as blank after stripping."""
        data = [{"provider_slug": "   ", "price_per_hour_usd": 2.39, "gpu": "H100"}]
        with self.assertRaisesRegex(ParserDriftError, "null or blank provider_slug"):
            parse_computeprices_response(data)

    def test_parse_computeprices_response_strips_whitespace_from_provider_slug(self) -> None:
        """Padded slugs like '  coreweave  ' must be stripped before mapping,
        so they match correctly instead of silently missing drift comparisons."""
        data = [{"provider_slug": "  coreweave  ", "price_per_hour_usd": 2.39, "gpu": "H100 SXM"}]
        result = parse_computeprices_response(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["provider"], "coreweave")

    def test_parse_computeprices_response_raises_drift_error_when_price_is_non_numeric(self) -> None:
        """Non-numeric price_per_hour_usd must raise ParserDriftError at the parser boundary."""
        data = [{"provider_slug": "coreweave", "price_per_hour_usd": "N/A", "gpu": "H100"}]
        with self.assertRaisesRegex(ParserDriftError, "non-numeric"):
            parse_computeprices_response(data)


# ---------------------------------------------------------------------------
# fetch_computeprices_gpu_prices — HTTP call and auth
# ---------------------------------------------------------------------------


class FetchComputepricesGpuPricesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_key = os.environ.pop("COMPUTEPRICES_API_KEY", None)

    def tearDown(self) -> None:
        if self._orig_key is not None:
            os.environ["COMPUTEPRICES_API_KEY"] = self._orig_key
        else:
            os.environ.pop("COMPUTEPRICES_API_KEY", None)

    def test_fetch_computeprices_gpu_prices_calls_correct_endpoint(self) -> None:
        """The client must call /api/v1/gpu-prices with gpu=h100 and pricing_type=on_demand
        when given our slug nvidia-h100-sxm-80."""
        mock_item = {
            "provider_slug": "coreweave",
            "gpu": "H100 SXM",
            "price_per_hour_usd": 2.39,
            "total_hourly_usd": 19.12,
        }
        with patch(_FETCH_TARGET, return_value=_make_api_response([mock_item])) as mock_get:
            result = fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

        call_kwargs = mock_get.call_args
        self.assertIn("gpu-prices", call_kwargs.args[0])
        self.assertEqual(call_kwargs.kwargs["params"]["gpu"], "h100")
        self.assertEqual(call_kwargs.kwargs["params"]["pricing_type"], "on_demand")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["provider"], "coreweave")

    def test_fetch_computeprices_gpu_prices_sends_auth_header_when_api_key_set(self) -> None:
        """When COMPUTEPRICES_API_KEY is set, the request must include an Authorization
        header so we use the free tier (5,000 req/hr) instead of the public tier (60/hr)."""
        os.environ["COMPUTEPRICES_API_KEY"] = "cp_live_testkey123"
        mock_item = {
            "provider_slug": "coreweave",
            "gpu": "H100 SXM",
            "price_per_hour_usd": 2.39,
            "total_hourly_usd": 19.12,
        }
        with patch(_FETCH_TARGET, return_value=_make_api_response([mock_item])) as mock_get:
            fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

        headers = mock_get.call_args.kwargs["headers"]
        self.assertEqual(headers.get("Authorization"), "Bearer cp_live_testkey123")

    def test_fetch_computeprices_gpu_prices_omits_auth_header_when_no_api_key(self) -> None:
        """Without COMPUTEPRICES_API_KEY, no Authorization header should be sent."""
        mock_item = {
            "provider_slug": "coreweave",
            "price_per_hour_usd": 2.39,
            "total_hourly_usd": 19.12,
        }
        with patch(_FETCH_TARGET, return_value=_make_api_response([mock_item])) as mock_get:
            fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

        headers = mock_get.call_args.kwargs["headers"]
        self.assertNotIn("Authorization", headers)

    def test_fetch_computeprices_gpu_prices_returns_empty_list_for_unmapped_gpu(self) -> None:
        """GPUs not in GPU_SLUG_MAP return [] immediately — no HTTP call made."""
        with patch(_FETCH_TARGET) as mock_get:
            result = fetch_computeprices_gpu_prices("nvidia-p100")

        self.assertEqual(result, [])
        mock_get.assert_not_called()

    def test_fetch_computeprices_gpu_prices_raises_parser_drift_error_on_non_dict_response(self) -> None:
        """If the API returns a non-dict body (e.g. a list or HTML), raise ParserDriftError
        so the drift service catches it at the boundary and notifies Sentry."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"provider_slug": "coreweave", "price_per_hour_usd": 2.39}]
        mock_resp.raise_for_status.return_value = None
        with patch(_FETCH_TARGET, return_value=mock_resp):
            with self.assertRaisesRegex(ParserDriftError, "unexpected response shape"):
                fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

    def test_fetch_computeprices_gpu_prices_raises_parser_drift_error_when_data_is_not_list(self) -> None:
        """If the API returns a dict but 'data' is not a list, raise ParserDriftError."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": "unexpected string", "meta": {}}
        mock_resp.raise_for_status.return_value = None
        with patch(_FETCH_TARGET, return_value=mock_resp):
            with self.assertRaisesRegex(ParserDriftError, "unexpected response shape"):
                fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

    def test_fetch_computeprices_gpu_prices_raises_parser_drift_error_on_invalid_json(self) -> None:
        """If the response body is not valid JSON (e.g. HTML error page with 200 status),
        raise ParserDriftError — not ValueError — so the task handles it via the right path."""
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("No JSON object could be decoded")
        mock_resp.raise_for_status.return_value = None
        with patch(_FETCH_TARGET, return_value=mock_resp):
            with self.assertRaisesRegex(ParserDriftError, "non-JSON"):
                fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

    def test_fetch_computeprices_gpu_prices_raises_parser_drift_error_when_data_item_not_dict(self) -> None:
        """If the API returns a list where items are not dicts, raise ParserDriftError
        so the drift service handles it cleanly rather than getting an AttributeError."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": ["not-a-dict", 42], "meta": {}}
        mock_resp.raise_for_status.return_value = None
        with patch(_FETCH_TARGET, return_value=mock_resp):
            with self.assertRaisesRegex(ParserDriftError, "not a dict"):
                fetch_computeprices_gpu_prices("nvidia-h100-sxm-80")

    def test_base_url_is_public_constant(self) -> None:
        """BASE_URL must be importable as a public constant for use by the drift service."""
        self.assertEqual(BASE_URL, "https://computeprices.com/api/v1")
