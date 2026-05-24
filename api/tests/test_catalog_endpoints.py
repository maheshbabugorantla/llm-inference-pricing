from __future__ import annotations

from django.test import TestCase

from catalog.tests.factories import GPUFactory, ModelFactory, QuantizationFactory


class GPUListEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.gpu_a = GPUFactory(slug="amd-mi300x", vendor="amd")
        cls.gpu_b = GPUFactory(slug="nvidia-h100", vendor="nvidia")

    def test_returns_200(self) -> None:
        response = self.client.get("/api/v1/gpus/")
        self.assertEqual(response.status_code, 200)

    def test_returns_all_gpus(self) -> None:
        response = self.client.get("/api/v1/gpus/")
        data = response.json()
        self.assertEqual(len(data["results"]), 2)

    def test_sorted_by_slug(self) -> None:
        response = self.client.get("/api/v1/gpus/")
        slugs = [r["slug"] for r in response.json()["results"]]
        self.assertEqual(slugs, sorted(slugs))

    def test_camel_case_keys(self) -> None:
        response = self.client.get("/api/v1/gpus/")
        result = response.json()["results"][0]
        self.assertIn("vramGb", result)
        self.assertIn("memoryBandwidthGbs", result)
        self.assertIn("tdpWatts", result)
        self.assertIn("fp16Tflops", result)

    def test_no_extra_queries(self) -> None:
        with self.assertNumQueries(1):
            self.client.get("/api/v1/gpus/")

    def test_pagination_keys_present(self) -> None:
        response = self.client.get("/api/v1/gpus/")
        data = response.json()
        self.assertIn("next", data)
        self.assertIn("previous", data)


class ModelListEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.model_a = ModelFactory(slug="deepseek-r2")
        cls.model_b = ModelFactory(slug="llama-4-scout")

    def test_returns_200(self) -> None:
        response = self.client.get("/api/v1/models/")
        self.assertEqual(response.status_code, 200)

    def test_returns_all_models(self) -> None:
        response = self.client.get("/api/v1/models/")
        data = response.json()
        self.assertEqual(len(data["results"]), 2)

    def test_sorted_by_slug(self) -> None:
        response = self.client.get("/api/v1/models/")
        slugs = [r["slug"] for r in response.json()["results"]]
        self.assertEqual(slugs, sorted(slugs))

    def test_camel_case_keys(self) -> None:
        response = self.client.get("/api/v1/models/")
        result = response.json()["results"][0]
        self.assertIn("displayName", result)
        self.assertIn("totalParamsB", result)
        self.assertIn("activeParamsB", result)
        self.assertIn("maxContext", result)

    def test_no_extra_queries(self) -> None:
        with self.assertNumQueries(1):
            self.client.get("/api/v1/models/")


class QuantizationListEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.q_a = QuantizationFactory(slug="fp16")
        cls.q_b = QuantizationFactory(slug="int4")

    def test_returns_200(self) -> None:
        response = self.client.get("/api/v1/quantizations/")
        self.assertEqual(response.status_code, 200)

    def test_returns_all_quantizations(self) -> None:
        response = self.client.get("/api/v1/quantizations/")
        data = response.json()
        self.assertEqual(len(data["results"]), 2)

    def test_sorted_by_slug(self) -> None:
        response = self.client.get("/api/v1/quantizations/")
        slugs = [r["slug"] for r in response.json()["results"]]
        self.assertEqual(slugs, sorted(slugs))

    def test_camel_case_keys(self) -> None:
        response = self.client.get("/api/v1/quantizations/")
        result = response.json()["results"][0]
        self.assertIn("displayName", result)
        self.assertIn("weightBits", result)
        self.assertIn("kvCacheBits", result)

    def test_no_extra_queries(self) -> None:
        with self.assertNumQueries(1):
            self.client.get("/api/v1/quantizations/")
