from __future__ import annotations

from unittest.mock import patch

import pytest

from pricing.tasks import scrape_runpod


@pytest.mark.django_db
def test_scrape_runpod_task_calls_scrape_runner() -> None:
    with patch("pricing.tasks.persist_prices", return_value=5) as mock_persist:
        with patch("pricing.tasks.runpod.scrape", return_value=[]):
            result = scrape_runpod.apply().get()
            assert result == 5
            mock_persist.assert_called_once()
