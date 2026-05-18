from __future__ import annotations

from unittest.mock import patch

import pytest

from pricing.tasks import scrape_lambda, scrape_nebius, scrape_runpod, scrape_vast


@pytest.mark.django_db
def test_scrape_runpod_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=5) as mock_persist:
        with patch("pricing.tasks.runpod.scrape", return_value=[]):
            result = scrape_runpod.apply().get()
            assert result == 5
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_lambda_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=3) as mock_persist:
        with patch("pricing.tasks.lambda_labs.scrape", return_value=[]):
            result = scrape_lambda.apply().get()
            assert result == 3
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_vast_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=7) as mock_persist:
        with patch("pricing.tasks.vast.scrape", return_value=[]):
            result = scrape_vast.apply().get()
            assert result == 7
            mock_persist.assert_called_once()


@pytest.mark.django_db
def test_scrape_nebius_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=4) as mock_persist:
        with patch("pricing.tasks.nebius.scrape", return_value=[]):
            result = scrape_nebius.apply().get()
            assert result == 4
            mock_persist.assert_called_once()
