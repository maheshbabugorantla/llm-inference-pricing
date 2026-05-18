from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_parse_yields_h100_on_demand_price() -> None:
    html = FIXTURE.read_text()
    prices = parse_lambda_html(html)
    h100 = [p for p in prices if p.gpu_slug_hint == "NVIDIA H100 SXM"]
    assert len(h100) >= 1
    assert h100[0].tier == "on_demand"
    assert h100[0].hourly_usd > Decimal("0")


def test_parse_returns_decimal_prices() -> None:
    html = FIXTURE.read_text()
    prices = parse_lambda_html(html)
    assert all(isinstance(p.hourly_usd, Decimal) for p in prices)


def test_parse_drops_unmapped_gpus() -> None:
    row = (
        '<tr class="_pricingRow_3954x_36" data-plan="Unknown GPU 9000">'
        '<th scope="row">Unknown GPU 9000</th>'
        '<td data-label="VRAM/GPU">24 GB</td>'
        '<td data-label="PRICE/GPU/HR*">$9.99</td>'
        "</tr>"
    )
    html = _make_islands_html(row)
    prices = parse_lambda_html(html)
    assert len(prices) == 0


def test_parse_all_prices_are_on_demand_tier() -> None:
    html = FIXTURE.read_text()
    prices = parse_lambda_html(html)
    assert all(p.tier == "on_demand" for p in prices)


def test_lambda_html_hash_logged_at_info() -> None:
    html = FIXTURE.read_text()
    with patch("pricing.scrapers.lambda_labs.logger") as mock_logger:
        parse_lambda_html(html)
        assert mock_logger.info.called
        all_args = " ".join(str(a) for call in mock_logger.info.call_args_list for a in call.args)
        assert len(all_args) > 0


def test_parse_raises_drift_error_when_no_islands() -> None:
    html = "<html><body><p>no islands here</p></body></html>"
    with pytest.raises(ParserDriftError):
        parse_lambda_html(html)


def test_parse_raises_drift_error_when_no_pricing_rows() -> None:
    island = {
        "moduleName": "TabsIsland",
        "props": {
            "defaultTab": 0,
            "tabs": [{"label": "1x", "contentHtml": "<table><thead></thead><tbody></tbody></table>"}],
        },
    }
    html = f"<html><body><script>\nvar newIslands = {json.dumps([island])};\n</script></body></html>"
    with pytest.raises(ParserDriftError):
        parse_lambda_html(html)


def test_map_lambda_h100_sxm() -> None:
    assert map_lambda_gpu("NVIDIA H100 SXM") == "nvidia-h100-sxm-80"


def test_map_lambda_unknown_returns_none() -> None:
    assert map_lambda_gpu("Unknown GPU 9000") is None


def test_map_covers_expected_gpu_set() -> None:
    expected = {"NVIDIA H100 SXM", "NVIDIA H100 PCIe", "NVIDIA B200 SXM6"}
    assert expected.issubset(LAMBDA_GPU_MAP.keys())
