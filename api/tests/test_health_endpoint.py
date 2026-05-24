from __future__ import annotations

from django.test import TestCase


class HealthEndpointTest(TestCase):
    def test_health_returns_200(self):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")

    def test_health_does_not_query_database(self):
        with self.assertNumQueries(0):
            self.client.get("/api/v1/health/")
