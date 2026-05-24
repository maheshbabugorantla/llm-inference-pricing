"""Ops hardening tests — pure-Python / no-DB tests.

TimescaleDB continuous aggregate and retention policy tests live in
test_ops_hardening_transaction.py (TransactionTestCase) so they can query
schema-level metadata without conflicting with TestCase savepoints.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.test import SimpleTestCase

from pricing.scrapers.base import ParserDriftError


class ParserDriftErrorTest(SimpleTestCase):
    def test_lambda_parser_raises_drift_error_on_zero_pricing_rows(self) -> None:
        """Lambda parser raises ParserDriftError when TabsIsland has no valid pricing rows."""
        from pricing.scrapers.lambda_labs import parse_lambda_html

        islands = [
            {
                "moduleName": "TabsIsland",
                "props": {"tabs": [{"label": "8x", "contentHtml": "<table><tbody></tbody></table>"}]},
            }
        ]
        fake_html = f"<script>var newIslands = {json.dumps(islands)};</script>"
        with self.assertRaisesRegex(ParserDriftError, "no pricing rows"):
            parse_lambda_html(fake_html)


class SentryCaptureTest(SimpleTestCase):
    def test_scrape_task_captures_exception_to_sentry_on_unexpected_error(self) -> None:
        """scrape_runpod calls sentry_sdk.capture_exception when a non-drift error occurs."""
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_capture:
            with patch("pricing.tasks.runpod.scrape", side_effect=RuntimeError("network down")):
                with patch("pricing.tasks.persist_prices"):
                    from pricing.tasks import scrape_runpod

                    with self.assertRaises(RuntimeError):
                        scrape_runpod.apply().get()
                    self.assertGreaterEqual(mock_capture.call_count, 1)

    def test_scrape_task_does_not_retry_on_parser_drift_error(self) -> None:
        """scrape_lambda sends Sentry message and does NOT retry on ParserDriftError."""
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with patch("pricing.tasks.lambda_labs.scrape", side_effect=ParserDriftError("drift")):
                with patch("pricing.tasks.persist_prices"):
                    from pricing.tasks import scrape_lambda

                    with self.assertRaises(ParserDriftError):
                        scrape_lambda.apply().get()
                    mock_msg.assert_called_once()
                    _, kwargs = mock_msg.call_args
                    self.assertEqual(kwargs.get("level"), "error")
