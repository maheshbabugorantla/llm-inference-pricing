from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from catalog.tests.factories import GPUFactory
from pricing.tests.factories import PricingSnapshotFactory, ProviderFactory


class ProviderListEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.active = ProviderFactory(slug="runpod", is_active=True, provider_type="cloud")
        cls.inactive = ProviderFactory(slug="vast", is_active=False, provider_type="cloud")
        cls.gpu = GPUFactory()
        cls.snap = PricingSnapshotFactory(
            provider=cls.active,
            gpu=cls.gpu,
            scraped_at=timezone.now(),
        )

    def test_returns_200(self) -> None:
        response = self.client.get("/api/v1/providers/")
        self.assertEqual(response.status_code, 200)

    def test_returns_active_and_inactive(self) -> None:
        response = self.client.get("/api/v1/providers/")
        slugs = {r["slug"] for r in response.json()["results"]}
        self.assertIn("runpod", slugs)
        self.assertIn("vast", slugs)

    def test_snake_case_keys(self) -> None:
        response = self.client.get("/api/v1/providers/")
        result = response.json()["results"][0]
        self.assertIn("provider_type", result)
        self.assertIn("data_source_tier", result)
        self.assertIn("has_api", result)
        self.assertIn("is_active", result)
        self.assertIn("last_scraped_at", result)

    def test_last_scraped_at_non_null_for_active(self) -> None:
        response = self.client.get("/api/v1/providers/")
        results = {r["slug"]: r for r in response.json()["results"]}
        self.assertIsNotNone(results["runpod"]["last_scraped_at"])

    def test_last_scraped_at_null_when_no_snapshots(self) -> None:
        response = self.client.get("/api/v1/providers/")
        results = {r["slug"]: r for r in response.json()["results"]}
        self.assertIsNone(results["vast"]["last_scraped_at"])

    def test_no_extra_queries(self) -> None:
        with self.assertNumQueries(1):
            self.client.get("/api/v1/providers/")
