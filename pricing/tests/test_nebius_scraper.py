from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from pricing.scrapers.base import ParserDriftError
from pricing.scrapers.nebius import NEBIUS_GPU_MAP, map_nebius_gpu, parse_nebius_html

FIXTURE = Path(__file__).parent / "fixtures" / "nebius_pricing.html"


def _make_next_data_html(rows: list[list[str]]) -> str:
    block = {
        "type": "highlight-table-block",
        "title": "NVIDIA GPU Instances",
        "table": {
            "content": [["Item", "vCPUs", "RAM, GB", "Preemptible, GPU-hour", "On-demand, GPU-hour"], *rows]
        },
    }
    content = json.dumps({"blocks": [block]})
    state = {'pages:{"id":"99"}': {"__typename": "pages", "content": content}}
    minimal = {
        "props": {"pageProps": {"__APOLLO_STATE__": state}},
        "page": "/prices",
        "query": {},
        "buildId": "fixture",
    }
    return f'<html><head></head><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(minimal)}</script></body></html>'


class NebiusScraperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.html = FIXTURE.read_text()

    def test_parse_yields_h100_on_demand_price(self) -> None:
        prices = parse_nebius_html(self.html)
        h100_od = [p for p in prices if p.gpu_slug_hint == "NVIDIA HGX H100" and p.tier == "on_demand"]
        self.assertGreaterEqual(len(h100_od), 1)
        self.assertGreater(h100_od[0].hourly_usd, Decimal("0"))

    def test_parse_yields_preemptible_tier(self) -> None:
        prices = parse_nebius_html(self.html)
        h100_tiers = {p.tier for p in prices if p.gpu_slug_hint == "NVIDIA HGX H100"}
        self.assertIn("preemptible", h100_tiers)
        self.assertIn("on_demand", h100_tiers)

    def test_parse_returns_decimal_prices(self) -> None:
        prices = parse_nebius_html(self.html)
        self.assertTrue(all(isinstance(p.hourly_usd, Decimal) for p in prices))

    def test_parse_drops_unmapped_gpus(self) -> None:
        html = _make_next_data_html([["Unknown GPU 9000", "8", "64", "$0.99", "$1.99"]])
        prices = parse_nebius_html(html)
        self.assertEqual(len(prices), 0)

    def test_parse_skips_contact_us_prices(self) -> None:
        html = _make_next_data_html(
            [
                ["NVIDIA HGX H100", "16", "200", "$1.25", "[Contact us](#form)"],
            ]
        )
        prices = parse_nebius_html(html)
        # on_demand is "Contact us" → skipped; preemptible is $1.25 → kept
        tiers = {p.tier for p in prices if p.gpu_slug_hint == "NVIDIA HGX H100"}
        self.assertNotIn("on_demand", tiers)
        self.assertIn("preemptible", tiers)

    def test_parse_handles_from_prefix_in_price(self) -> None:
        prices = parse_nebius_html(self.html)
        l40s = [p for p in prices if p.gpu_slug_hint == "NVIDIA L40S with Intel CPU"]
        self.assertGreaterEqual(len(l40s), 1)
        self.assertGreater(l40s[0].hourly_usd, Decimal("0"))

    def test_nebius_html_hash_logged_at_info(self) -> None:
        with patch("pricing.scrapers.nebius.logger") as mock_logger:
            parse_nebius_html(self.html)
            self.assertTrue(mock_logger.info.called)

    def test_parse_raises_drift_error_when_no_next_data(self) -> None:
        html = "<html><body><p>no data here</p></body></html>"
        with self.assertRaises(ParserDriftError):
            parse_nebius_html(html)

    def test_parse_raises_drift_error_when_no_gpu_block(self) -> None:
        block = {"type": "header-block", "title": "Welcome"}
        content = json.dumps({"blocks": [block]})
        state = {'pages:{"id":"99"}': {"__typename": "pages", "content": content}}
        minimal = {
            "props": {"pageProps": {"__APOLLO_STATE__": state}},
            "page": "/prices",
            "query": {},
            "buildId": "x",
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(minimal)}</script>'
        with self.assertRaises(ParserDriftError):
            parse_nebius_html(html)

    def test_map_nebius_h100(self) -> None:
        self.assertEqual(map_nebius_gpu("NVIDIA HGX H100"), "nvidia-h100-sxm-80")

    def test_map_nebius_unknown_returns_none(self) -> None:
        self.assertIsNone(map_nebius_gpu("Unknown GPU 9000"))

    def test_map_covers_expected_gpu_set(self) -> None:
        expected = {"NVIDIA HGX H100", "NVIDIA HGX H200"}
        self.assertTrue(expected.issubset(NEBIUS_GPU_MAP.keys()))
