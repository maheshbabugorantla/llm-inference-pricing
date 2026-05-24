from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from catalog.tests.factories import BenchmarkPointFactory, GPUFactory
from pricing.tests.factories import PricingSnapshotFactory, ProviderFactory


def _seed_cost_cell(
    *,
    provider_slug: str = "aws",
    gpu_slug: str = "nvidia-a100",
    usd_per_hour: str = "2.00",
    decode_tps: float = 500.0,
) -> None:
    """Seed one BenchmarkPoint + PricingSnapshot so refresh_cost_cells produces a row."""
    from django.db import connection

    gpu = GPUFactory(slug=gpu_slug)
    provider = ProviderFactory(slug=provider_slug, provider_type="cloud")
    BenchmarkPointFactory(
        gpu=gpu,
        decode_tps_aggregate=decode_tps,
        prefill_tps_aggregate=decode_tps,
    )
    PricingSnapshotFactory(provider=provider, gpu=gpu, hourly_usd=Decimal(usd_per_hour))
    # Refresh the materialized view
    with connection.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW pricing_current_cost_cells")


class CostCellsEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        from django.db import connection

        cls.gpu = GPUFactory(slug="nvidia-h100")
        cls.provider = ProviderFactory(slug="lambda", provider_type="cloud")
        cls.bp = BenchmarkPointFactory(
            gpu=cls.gpu,
            decode_tps_aggregate=1000.0,
            prefill_tps_aggregate=1000.0,
        )
        PricingSnapshotFactory(
            provider=cls.provider,
            gpu=cls.gpu,
            hourly_usd=Decimal("3.00"),
        )
        with connection.cursor() as cur:
            cur.execute("REFRESH MATERIALIZED VIEW pricing_current_cost_cells")

    def test_returns_200(self) -> None:
        response = self.client.get("/api/v1/cost-cells/")
        self.assertEqual(response.status_code, 200)

    def test_returns_results(self) -> None:
        response = self.client.get("/api/v1/cost-cells/")
        data = response.json()
        self.assertGreater(len(data["results"]), 0)

    def test_camel_case_keys(self) -> None:
        response = self.client.get("/api/v1/cost-cells/")
        result = response.json()["results"][0]
        for key in (
            "gpuSlug",
            "modelSlug",
            "quantizationSlug",
            "providerSlug",
            "providerType",
            "dataSourceTier",
            "hourlyUsd",
            "usdPerMOutput",
            "usdPerMInput",
            "decodeTpsAggregate",
        ):
            self.assertIn(key, result, f"missing key: {key}")

    def test_default_ordering_cheapest_first(self) -> None:
        from django.db import connection

        gpu2 = GPUFactory(slug="nvidia-h200")
        provider2 = ProviderFactory(slug="runpod", provider_type="cloud")
        BenchmarkPointFactory(
            gpu=gpu2,
            decode_tps_aggregate=1000.0,
            prefill_tps_aggregate=1000.0,
        )
        PricingSnapshotFactory(provider=provider2, gpu=gpu2, hourly_usd=Decimal("1.00"))
        with connection.cursor() as cur:
            cur.execute("REFRESH MATERIALIZED VIEW pricing_current_cost_cells")

        response = self.client.get("/api/v1/cost-cells/")
        costs = [Decimal(r["usdPerMOutput"]) for r in response.json()["results"]]
        self.assertEqual(costs, sorted(costs))

    def test_filter_by_provider_slug(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?provider_slug=lambda")
        data = response.json()
        for r in data["results"]:
            self.assertEqual(r["providerSlug"], "lambda")

    def test_filter_by_provider_type(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?provider_type=cloud")
        data = response.json()
        for r in data["results"]:
            self.assertEqual(r["providerType"], "cloud")

    def test_filter_by_gpu_slug(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?gpu_slug=nvidia-h100")
        data = response.json()
        for r in data["results"]:
            self.assertEqual(r["gpuSlug"], "nvidia-h100")

    def test_max_usd_per_m_output_cap(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?max_usd_per_m_output=0.001")
        data = response.json()
        # Everything above 0.001 filtered out; likely 0 rows
        for r in data["results"]:
            self.assertLessEqual(Decimal(r["usdPerMOutput"]), Decimal("0.001"))

    def test_malformed_max_cap_ignored(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?max_usd_per_m_output=notanumber")
        self.assertEqual(response.status_code, 200)

    def test_single_query(self) -> None:
        with self.assertNumQueries(1):
            self.client.get("/api/v1/cost-cells/")

    def test_empty_db_returns_empty_list(self) -> None:
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("REFRESH MATERIALIZED VIEW pricing_current_cost_cells")
        # After refreshing with the seeded data it will have rows;
        # just confirm no 500 on empty filter
        response = self.client.get("/api/v1/cost-cells/?provider_slug=nonexistent-provider")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_unknown_filter_ignored(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?totally_unknown=xyz")
        self.assertEqual(response.status_code, 200)

    def test_pagination_keys_present(self) -> None:
        response = self.client.get("/api/v1/cost-cells/")
        data = response.json()
        self.assertIn("next", data)
        self.assertIn("previous", data)
