from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from catalog.tests.factories import BenchmarkPointFactory, GPUFactory
from pricing.tests.factories import PricingSnapshotFactory, ProviderFactory


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

    def test_snake_case_keys(self) -> None:
        response = self.client.get("/api/v1/cost-cells/")
        result = response.json()["results"][0]
        for key in (
            "gpu_slug",
            "model_slug",
            "quantization_slug",
            "provider_slug",
            "provider_type",
            "data_source_tier",
            "hourly_usd",
            "usd_per_m_output",
            "usd_per_m_input",
            "decode_tps_aggregate",
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
        costs = [Decimal(r["usd_per_m_output"]) for r in response.json()["results"]]
        self.assertEqual(costs, sorted(costs))

    def test_filter_by_provider_slug(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?provider_slug=lambda")
        data = response.json()
        for r in data["results"]:
            self.assertEqual(r["provider_slug"], "lambda")

    def test_filter_by_provider_type(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?provider_type=cloud")
        data = response.json()
        for r in data["results"]:
            self.assertEqual(r["provider_type"], "cloud")

    def test_filter_by_gpu_slug(self) -> None:
        response = self.client.get("/api/v1/cost-cells/?gpu_slug=nvidia-h100")
        data = response.json()
        for r in data["results"]:
            self.assertEqual(r["gpu_slug"], "nvidia-h100")

    def test_max_usd_per_m_output_cap_excludes_rows_above(self) -> None:
        # setUpTestData rows have usd_per_m_output ≈ 0.83 (hourly=$3, tps=1000).
        # A cap of 0.001 must exclude all of them — asserting empty is the only
        # way to detect a broken filter (iterating an empty list silently passes).
        response = self.client.get("/api/v1/cost-cells/?max_usd_per_m_output=0.001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_max_usd_per_m_output_cap_includes_rows_below(self) -> None:
        # A cap of 10.00 is above all seeded rows — all should be returned, and
        # every returned row must satisfy the cap.
        response = self.client.get("/api/v1/cost-cells/?max_usd_per_m_output=10.00")
        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(data["results"]), 0)
        for r in data["results"]:
            self.assertLessEqual(Decimal(r["usd_per_m_output"]), Decimal("10.00"))

    def test_malformed_max_cap_returns_400(self) -> None:
        # NumberFilter validates the value; a non-numeric string is a client error.
        response = self.client.get("/api/v1/cost-cells/?max_usd_per_m_output=notanumber")
        self.assertEqual(response.status_code, 400)

    def test_single_query(self) -> None:
        with self.assertNumQueries(1):
            self.client.get("/api/v1/cost-cells/")

    def test_filter_with_no_matches_returns_empty_list(self) -> None:
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
