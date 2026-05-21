from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.db import connection

from pricing.scrapers.base import ParserDriftError

# ---------------------------------------------------------------------------
# T01 — Continuous aggregate
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_daily_median_view_exists_in_timescaledb() -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates "
            "WHERE view_name = 'pricing_daily_median'"
        )
        assert c.fetchone() is not None, "pricing_daily_median continuous aggregate missing"


# ---------------------------------------------------------------------------
# T02 — Retention policy
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_retention_policy_registered_for_pricing_snapshot() -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT config FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_retention' "
            "AND hypertable_name = 'pricing_pricingsnapshot'"
        )
        row = c.fetchone()
        assert row is not None, "retention policy missing on pricing_pricingsnapshot"
        assert "90 days" in str(row[0])


# ---------------------------------------------------------------------------
# T03 — ParserDriftError raised on zero results
# ---------------------------------------------------------------------------


def test_lambda_parser_raises_drift_error_on_zero_pricing_rows() -> None:
    """Lambda parser raises ParserDriftError when TabsIsland has no valid pricing rows."""
    from pricing.scrapers.lambda_labs import parse_lambda_html

    islands = [
        {
            "moduleName": "TabsIsland",
            "props": {"tabs": [{"label": "8x", "contentHtml": "<table><tbody></tbody></table>"}]},
        }
    ]
    fake_html = f"<script>var newIslands = {json.dumps(islands)};</script>"
    with pytest.raises(ParserDriftError, match="no pricing rows"):
        parse_lambda_html(fake_html)


# ---------------------------------------------------------------------------
# T03 — Celery task captures exception to Sentry
# ---------------------------------------------------------------------------


def test_scrape_task_captures_exception_to_sentry_on_unexpected_error() -> None:
    """scrape_runpod calls sentry_sdk.capture_exception when a non-drift error occurs."""
    with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_capture:
        with patch("pricing.tasks.runpod.scrape", side_effect=RuntimeError("network down")):
            with patch("pricing.tasks.persist_prices"):
                from pricing.tasks import scrape_runpod

                with pytest.raises(RuntimeError):
                    scrape_runpod.apply().get()
                assert mock_capture.call_count >= 1


def test_scrape_task_does_not_retry_on_parser_drift_error() -> None:
    """scrape_lambda sends Sentry message and does NOT retry on ParserDriftError."""
    with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
        with patch("pricing.tasks.lambda_labs.scrape", side_effect=ParserDriftError("drift")):
            with patch("pricing.tasks.persist_prices"):
                from pricing.tasks import scrape_lambda

                with pytest.raises(ParserDriftError):
                    scrape_lambda.apply().get()
                mock_msg.assert_called_once()
                _, kwargs = mock_msg.call_args
                assert kwargs.get("level") == "error"
