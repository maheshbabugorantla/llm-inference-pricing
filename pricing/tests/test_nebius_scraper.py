from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from pricing.scrapers.base import ParserDriftError
from pricing.scrapers.nebius import parse_nebius_html

FIXTURE = Path(__file__).parent / "fixtures" / "nebius_pricing.html"


def test_nebius_parse_yields_h100_on_demand_price() -> None:
    html = FIXTURE.read_text()
    prices = parse_nebius_html(html)
    h100 = [p for p in prices if p.gpu_slug_hint == "NVIDIA H100 SXM (80 GB)"]
    assert len(h100) >= 1
    assert h100[0].tier == "on_demand"
    assert h100[0].hourly_usd > Decimal("0")


def test_nebius_parse_returns_decimal_prices() -> None:
    html = FIXTURE.read_text()
    prices = parse_nebius_html(html)
    assert all(isinstance(p.hourly_usd, Decimal) for p in prices)


def test_nebius_parse_drops_unmapped_gpus() -> None:
    html = FIXTURE.read_text()
    prices = parse_nebius_html(html)
    slugs = {p.gpu_slug_hint for p in prices}
    assert "Some Future GPU (128 GB)" not in slugs


def test_nebius_parse_all_on_demand_tier() -> None:
    html = FIXTURE.read_text()
    prices = parse_nebius_html(html)
    assert all(p.tier == "on_demand" for p in prices)


def test_nebius_html_hash_logged_at_info() -> None:
    html = FIXTURE.read_text()
    with patch("pricing.scrapers.nebius.logger") as mock_logger:
        parse_nebius_html(html)
        assert mock_logger.info.called


def test_nebius_parser_raises_drift_error_on_empty_result() -> None:
    html = "<html><body><p>no pricing table here</p></body></html>"
    with pytest.raises(ParserDriftError):
        parse_nebius_html(html)
