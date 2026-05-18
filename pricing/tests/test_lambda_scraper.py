from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from pricing.scrapers.base import ParserDriftError
from pricing.scrapers.lambda_labs import parse_lambda_html

FIXTURE = Path(__file__).parent / "fixtures" / "lambda_pricing.html"


def test_lambda_parse_yields_h100_on_demand_price() -> None:
    html = FIXTURE.read_text()
    prices = parse_lambda_html(html)
    h100 = [p for p in prices if p.gpu_slug_hint == "H100 (80 GB SXM5)"]
    assert len(h100) >= 1
    assert h100[0].tier == "on_demand"
    assert h100[0].hourly_usd > Decimal("0")


def test_lambda_parse_returns_decimal_prices() -> None:
    html = FIXTURE.read_text()
    prices = parse_lambda_html(html)
    assert all(isinstance(p.hourly_usd, Decimal) for p in prices)


def test_lambda_parse_drops_unmapped_gpus() -> None:
    html = "<table><tr><td>Unknown GPU 9000</td><td>$9.99/hr</td></tr></table>"
    prices = parse_lambda_html(html)
    assert len(prices) == 0


def test_lambda_parser_handles_missing_reserved_column_gracefully() -> None:
    html = FIXTURE.read_text()
    prices = parse_lambda_html(html)
    # All returned prices should be on_demand (reserved columns are absent/optional)
    assert all(p.tier == "on_demand" for p in prices)


def test_lambda_html_hash_logged_at_info() -> None:
    html = FIXTURE.read_text()
    with patch("pricing.scrapers.lambda_labs.logger") as mock_logger:
        parse_lambda_html(html)
        # logger.info should have been called with a hash somewhere in the args
        assert mock_logger.info.called
        all_args = " ".join(str(a) for call in mock_logger.info.call_args_list for a in call.args)
        assert len(all_args) > 0


def test_lambda_parser_raises_drift_error_on_empty_result() -> None:
    html = "<html><body><p>no pricing table here</p></body></html>"
    with pytest.raises(ParserDriftError):
        parse_lambda_html(html)
