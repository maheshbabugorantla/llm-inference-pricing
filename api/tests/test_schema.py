from __future__ import annotations

from django.test import SimpleTestCase


class OpenAPISchemaTest(SimpleTestCase):
    def test_schema_returns_200(self) -> None:
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)

    def test_schema_content_type(self) -> None:
        response = self.client.get("/api/schema/")
        self.assertIn("application/vnd.oai.openapi", response["Content-Type"])

    def test_schema_includes_cost_cells_path(self) -> None:
        import yaml

        response = self.client.get("/api/schema/")
        schema = yaml.safe_load(response.content)
        self.assertIn("/api/v1/cost-cells/", schema.get("paths", {}))

    def test_swagger_ui_returns_200(self) -> None:
        response = self.client.get("/api/docs/")
        self.assertEqual(response.status_code, 200)

    def test_swagger_ui_contains_swagger_ui(self) -> None:
        response = self.client.get("/api/docs/")
        self.assertIn(b"swagger-ui", response.content)

    def test_redoc_returns_200(self) -> None:
        response = self.client.get("/api/redoc/")
        self.assertEqual(response.status_code, 200)

    def test_redoc_contains_redoc(self) -> None:
        response = self.client.get("/api/redoc/")
        self.assertIn(b"redoc", response.content.lower())


class CORSTest(SimpleTestCase):
    def test_cors_allows_localhost_4200(self) -> None:
        response = self.client.get(
            "/api/v1/health/",
            HTTP_ORIGIN="http://localhost:4200",
        )
        self.assertEqual(response.get("Access-Control-Allow-Origin"), "http://localhost:4200")

    def test_cors_blocks_unknown_origin(self) -> None:
        response = self.client.get(
            "/api/v1/health/",
            HTTP_ORIGIN="http://evil.example.com",
        )
        self.assertIsNone(response.get("Access-Control-Allow-Origin"))

    def test_cors_no_credentials(self) -> None:
        response = self.client.get(
            "/api/v1/health/",
            HTTP_ORIGIN="http://localhost:4200",
        )
        self.assertNotEqual(response.get("Access-Control-Allow-Credentials", "false"), "true")
