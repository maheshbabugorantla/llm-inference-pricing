from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from pricing.scrapers.base import ParserDriftError
from pricing.tasks import (
    refresh_current_cost_cells,
    regenerate_on_prem_snapshots_task,
    scrape_aws,
    scrape_azure,
    scrape_lambda,
    scrape_nebius,
    scrape_runpod,
    scrape_vast,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _apply(task, **kwargs):
    """Run a Celery task eagerly and return the raw result (raises on failure)."""
    return task.apply(**kwargs).get()


def _apply_expect_error(task, exc_type, **kwargs):
    """Run eagerly with max_retries=0 so retry raises immediately; assert exc_type raised."""
    with patch.object(task, "max_retries", 0):
        with pytest.raises(exc_type):
            _apply(task, **kwargs)


# ── scrape_runpod ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_runpod_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=5) as mock_persist:
        with patch("pricing.tasks.runpod.scrape", return_value=[]):
            assert _apply(scrape_runpod) == 5
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_runpod_parser_drift_error_captured_and_reraised() -> None:
    exc = ParserDriftError("runpod schema changed")
    with patch("pricing.tasks.runpod.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                _apply(scrape_runpod)
            mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_runpod_generic_exception_captured_and_retried() -> None:
    exc = RuntimeError("connection reset")
    with patch("pricing.tasks.runpod.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            _apply_expect_error(scrape_runpod, Exception)
            mock_cap.assert_called()


# ── scrape_lambda ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_lambda_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=3) as mock_persist:
        with patch("pricing.tasks.lambda_labs.scrape", return_value=[]):
            assert _apply(scrape_lambda) == 3
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_lambda_parser_drift_error_captured_and_reraised() -> None:
    exc = ParserDriftError("lambda schema changed")
    with patch("pricing.tasks.lambda_labs.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                _apply(scrape_lambda)
            mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_lambda_generic_exception_captured_and_retried() -> None:
    exc = RuntimeError("timeout")
    with patch("pricing.tasks.lambda_labs.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            _apply_expect_error(scrape_lambda, Exception)
            mock_cap.assert_called()


# ── scrape_vast ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_vast_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=7) as mock_persist:
        with patch("pricing.tasks.vast.scrape", return_value=[]):
            assert _apply(scrape_vast) == 7
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_vast_parser_drift_error_captured_and_reraised() -> None:
    exc = ParserDriftError("vast schema changed")
    with patch("pricing.tasks.vast.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                _apply(scrape_vast)
            mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_vast_generic_exception_captured_and_retried() -> None:
    exc = RuntimeError("vast down")
    with patch("pricing.tasks.vast.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            _apply_expect_error(scrape_vast, Exception)
            mock_cap.assert_called()


# ── scrape_nebius ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_nebius_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=4) as mock_persist:
        with patch("pricing.tasks.nebius.scrape", return_value=[]):
            assert _apply(scrape_nebius) == 4
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_nebius_parser_drift_error_captured_and_reraised() -> None:
    exc = ParserDriftError("nebius schema changed")
    with patch("pricing.tasks.nebius.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                _apply(scrape_nebius)
            mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_nebius_generic_exception_captured_and_retried() -> None:
    exc = RuntimeError("nebius down")
    with patch("pricing.tasks.nebius.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            _apply_expect_error(scrape_nebius, Exception)
            mock_cap.assert_called()


# ── scrape_aws ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_aws_happy_path() -> None:
    with patch("pricing.tasks.persist_prices", return_value=12) as mock_persist:
        with patch("pricing.tasks.aws.scrape", return_value=[]):
            assert _apply(scrape_aws) == 12
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_aws_parser_drift_error_captured_and_reraised() -> None:
    exc = ParserDriftError("aws schema changed")
    with patch("pricing.tasks.aws.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                _apply(scrape_aws)
            mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_aws_client_error_captured_and_retried() -> None:
    import botocore.exceptions

    client_error = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "not authorized"}},
        "DescribeCapacityReservations",
    )
    with patch("pricing.tasks.aws.scrape", side_effect=client_error):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            _apply_expect_error(scrape_aws, Exception)
            mock_cap.assert_called()


@pytest.mark.django_db
def test_scrape_aws_generic_exception_captured_and_retried() -> None:
    exc = RuntimeError("aws bulk pricing fetch failed")
    with patch("pricing.tasks.aws.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            _apply_expect_error(scrape_aws, Exception)
            mock_cap.assert_called()


# ── scrape_azure ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_scrape_azure_happy_path() -> None:
    with patch("pricing.tasks.persist_prices", return_value=9) as mock_persist:
        with patch("pricing.tasks.azure.scrape", return_value=[]):
            assert _apply(scrape_azure) == 9
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_azure_parser_drift_error_captured_and_reraised() -> None:
    exc = ParserDriftError("azure schema changed")
    with patch("pricing.tasks.azure.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_message") as mock_msg:
            with pytest.raises(ParserDriftError):
                _apply(scrape_azure)
            mock_msg.assert_called_once_with(str(exc), level="error")


@pytest.mark.django_db
def test_scrape_azure_http_status_error_captured_and_retried() -> None:
    request = httpx.Request("GET", "https://prices.azure.com/api/retail/prices")
    response = httpx.Response(429, request=request)
    http_err = httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)
    with patch("pricing.tasks.azure.scrape", side_effect=http_err):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            _apply_expect_error(scrape_azure, Exception)
            mock_cap.assert_called()


@pytest.mark.django_db
def test_scrape_azure_generic_exception_captured_and_retried() -> None:
    exc = RuntimeError("azure retail API unreachable")
    with patch("pricing.tasks.azure.scrape", side_effect=exc):
        with patch("pricing.tasks.sentry_sdk.capture_exception") as mock_cap:
            _apply_expect_error(scrape_azure, Exception)
            mock_cap.assert_called()


# ── regenerate_on_prem_snapshots_task ─────────────────────────────────────────


@pytest.mark.django_db
def test_regenerate_on_prem_snapshots_task_returns_count() -> None:
    # regenerate_on_prem_snapshots is imported lazily inside the task body;
    # patch at the generator module level.
    with patch("pricing.generators.on_prem.regenerate_on_prem_snapshots", return_value=4) as mock_gen:
        result = regenerate_on_prem_snapshots_task.apply().get()
        mock_gen.assert_called_once()
        assert result == 4


# ── refresh_current_cost_cells ────────────────────────────────────────────────


@pytest.mark.django_db
def test_refresh_current_cost_cells_task_calls_service() -> None:
    with patch("pricing.tasks.refresh_cost_cells") as mock_refresh:
        refresh_current_cost_cells.apply().get()
        mock_refresh.assert_called_once()
