from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from pricing.scrapers.base import ParserDriftError
from pricing.scrapers.lambda_labs import LAMBDA_GPU_MAP, map_lambda_gpu, parse_lambda_html

FIXTURE = Path(__file__).parent / "fixtures" / "lambda_pricing.html"


def _make_islands_html(rows_html: str, tab_label: str = "8x") -> str:
    content = (
        f'<table class="_pricingTable_3954x_13">'
        f"<thead><tr><th>Plan</th><th>VRAM/GPU</th><th>PRICE/GPU/HR*</th></tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        f"</table>"
    )
    island = {
        "moduleName": "TabsIsland",
        "props": {"defaultTab": 0, "tabs": [{"label": tab_label, "contentHtml": content}]},
    }
    return f"<html><body><script>\nvar newIslands = {json.dumps([island])};\n</script></body></html>"


class LambdaScraperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.html = FIXTURE.read_text()

    def test_parse_yields_h100_on_demand_price(self) -> None:
        prices = parse_lambda_html(self.html)
        h100 = [p for p in prices if p.gpu_slug_hint == "NVIDIA H100 SXM"]
        self.assertGreaterEqual(len(h100), 1)
        self.assertEqual(h100[0].tier, "on_demand")
        self.assertGreater(h100[0].hourly_usd, Decimal("0"))

    def test_parse_returns_decimal_prices(self) -> None:
        prices = parse_lambda_html(self.html)
        self.assertTrue(all(isinstance(p.hourly_usd, Decimal) for p in prices))

    def test_parse_drops_unmapped_gpus(self) -> None:
        row = (
            '<tr class="_pricingRow_3954x_36" data-plan="Unknown GPU 9000">'
            '<th scope="row">Unknown GPU 9000</th>'
            '<td data-label="VRAM/GPU">24 GB</td>'
            '<td data-label="PRICE/GPU/HR*">$9.99</td>'
            "</tr>"
        )
        html = _make_islands_html(row)
        prices = parse_lambda_html(html)
        self.assertEqual(len(prices), 0)

    def test_parse_all_prices_are_on_demand_tier(self) -> None:
        prices = parse_lambda_html(self.html)
        self.assertTrue(all(p.tier == "on_demand" for p in prices))

    def test_lambda_html_hash_logged_at_info(self) -> None:
        with patch("pricing.scrapers.lambda_labs.logger") as mock_logger:
            parse_lambda_html(self.html)
            self.assertTrue(mock_logger.info.called)
            all_args = " ".join(str(a) for call in mock_logger.info.call_args_list for a in call.args)
            self.assertGreater(len(all_args), 0)

    def test_parse_raises_drift_error_when_no_islands(self) -> None:
        html = "<html><body><p>no islands here</p></body></html>"
        with self.assertRaises(ParserDriftError):
            parse_lambda_html(html)

    def test_parse_raises_drift_error_when_no_pricing_rows(self) -> None:
        island = {
            "moduleName": "TabsIsland",
            "props": {
                "defaultTab": 0,
                "tabs": [{"label": "1x", "contentHtml": "<table><thead></thead><tbody></tbody></table>"}],
            },
        }
        html = f"<html><body><script>\nvar newIslands = {json.dumps([island])};\n</script></body></html>"
        with self.assertRaises(ParserDriftError):
            parse_lambda_html(html)

    def test_map_lambda_h100_sxm(self) -> None:
        self.assertEqual(map_lambda_gpu("NVIDIA H100 SXM"), "nvidia-h100-sxm-80")

    def test_map_lambda_unknown_returns_none(self) -> None:
        self.assertIsNone(map_lambda_gpu("Unknown GPU 9000"))

    def test_map_covers_expected_gpu_set(self) -> None:
        expected = {"NVIDIA H100 SXM", "NVIDIA H100 PCIe", "NVIDIA B200 SXM6"}
        self.assertTrue(expected.issubset(LAMBDA_GPU_MAP.keys()))
