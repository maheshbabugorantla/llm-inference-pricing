"""Tests for Azure GPU pricing scraper (M05.5b.T04)."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from pricing.scrapers.azure import (
    _SKU_CHUNK_SIZE,
    _fetch_azure_sku_chunk,
    map_azure_gpu,
    parse_azure_prices,
)
from pricing.scrapers.base import ParserDriftError

FIXTURES = Path(__file__).parent / "fixtures"


class AzureScraperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.nd_series_items = json.loads((FIXTURES / "azure_prices_nd_series.json").read_text())

    def test_azure_parse_h100_on_demand_yields_per_gpu_hourly(self) -> None:
        prices = parse_azure_prices(self.nd_series_items)
        h100 = next(p for p in prices if p.gpu_slug_hint == "nvidia-h100-sxm-80" and p.tier == "on_demand")
        # $98.32 / 8 GPUs
        self.assertEqual(h100.hourly_usd, Decimal("98.32") / Decimal(8))
        self.assertEqual(h100.provider_slug, "azure")
        self.assertEqual(h100.region, "eastus")

    def test_azure_parse_reserved_1yr_tier(self) -> None:
        prices = parse_azure_prices(self.nd_series_items)
        h100_reserved = next(
            p for p in prices if p.gpu_slug_hint == "nvidia-h100-sxm-80" and p.tier == "reserved-1yr"
        )
        self.assertEqual(h100_reserved.hourly_usd, Decimal("72.11") / Decimal(8))

    def test_azure_parse_reserved_3yr_tier(self) -> None:
        prices = parse_azure_prices(self.nd_series_items)
        h100_3yr = next(
            p for p in prices if p.gpu_slug_hint == "nvidia-h100-sxm-80" and p.tier == "reserved-3yr"
        )
        self.assertEqual(h100_3yr.hourly_usd, Decimal("60.45") / Decimal(8))

    def test_azure_parse_t4_single_gpu_no_division(self) -> None:
        prices = parse_azure_prices(self.nd_series_items)
        t4 = next(p for p in prices if p.gpu_slug_hint == "nvidia-t4")
        # NC4as_T4_v3 has 1 GPU — no division
        self.assertEqual(t4.hourly_usd, Decimal("0.526"))

    def test_azure_parse_returns_decimals(self) -> None:
        prices = parse_azure_prices(self.nd_series_items)
        for p in prices:
            self.assertIsInstance(p.hourly_usd, Decimal)

    def test_azure_parse_unknown_sku_raises_parser_drift_error(self) -> None:
        unknown_items = [
            {
                "armSkuName": "Standard_Unknown_GPU_v99",
                "armRegionName": "eastus",
                "retailPrice": 1.0,
                "reservationTerm": "",
                "priceType": "Consumption",
            }
        ]
        with self.assertRaises(ParserDriftError):
            parse_azure_prices(unknown_items)

    def test_azure_parse_zero_price_skipped(self) -> None:
        items = [
            {
                "armSkuName": "Standard_ND96isr_H100_v5",
                "armRegionName": "eastus",
                "retailPrice": 0.0,
                "reservationTerm": "",
                "priceType": "Consumption",
            }
        ]
        with self.assertRaises(ParserDriftError):
            parse_azure_prices(items)

    def test_azure_map_gpu_known_sku(self) -> None:
        self.assertEqual(map_azure_gpu("Standard_ND96isr_H100_v5"), "nvidia-h100-sxm-80")

    def test_azure_map_gpu_unknown_sku_returns_none(self) -> None:
        self.assertIsNone(map_azure_gpu("Standard_D4s_v3"))

    def test_azure_fetch_uses_odata_or_not_pipe_in_filter(self) -> None:
        """Azure Retail Prices API requires OData `or` for logical OR — not `|` (bitwise OR).

        Sending `|` in the OData $filter returns HTTP 400; the CI scrape was failing because
        the filter used the wrong operator. This test proves _fetch_azure_sku_chunk constructs
        a valid OData filter string before any network call goes out.
        """
        captured_filter: list[str] = []

        def fake_get(url: str, **kwargs: object) -> MagicMock:
            params = kwargs.get("params", {})
            if isinstance(params, dict):
                captured_filter.append(params.get("$filter", ""))
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = {"Items": [], "NextPageLink": None}
            return mock_resp

        test_skus = ["Standard_ND96isr_H100_v5", "Standard_ND96amsr_A100_v4"]
        with patch("pricing.scrapers.azure.httpx.get", side_effect=fake_get):
            _fetch_azure_sku_chunk(test_skus)

        self.assertTrue(captured_filter, "No request was made")
        filter_str = captured_filter[0]
        self.assertIn(" or ", filter_str, f"Expected OData 'or', got: {filter_str!r}")
        self.assertNotIn("|", filter_str, f"Pipe '|' must not appear in OData filter: {filter_str!r}")

    def test_azure_sku_chunk_size_keeps_url_manageable(self) -> None:
        """Each chunk must be small enough to avoid Azure filter-length issues.
        _SKU_CHUNK_SIZE > 8 would create filters that have historically returned 400."""
        self.assertLessEqual(
            _SKU_CHUNK_SIZE, 8, f"_SKU_CHUNK_SIZE={_SKU_CHUNK_SIZE} risks Azure URL length errors"
        )
