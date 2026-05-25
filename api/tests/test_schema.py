from __future__ import annotations

from io import StringIO

import yaml
from django.core.management import call_command
from django.test import SimpleTestCase


class OpenAPISchemaTest(SimpleTestCase):
    def test_schema_returns_200(self) -> None:
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)

    def test_schema_content_type(self) -> None:
        response = self.client.get("/api/schema/")
        self.assertIn("application/vnd.oai.openapi", response["Content-Type"])

    def test_schema_includes_cost_cells_path(self) -> None:
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


class SchemaComponentTest(SimpleTestCase):
    def test_schema_includes_costcell_component(self) -> None:
        out = StringIO()
        call_command("spectacular", stdout=out)
        schema = yaml.safe_load(out.getvalue())
        components = schema.get("components", {}).get("schemas", {})
        has_costcell = any("costcell" in key.lower() or "cost_cell" in key.lower() for key in components)
        self.assertTrue(has_costcell, f"No CostCell component found. Keys: {list(components)[:10]}")

    def test_schema_costcell_has_expected_fields(self) -> None:
        out = StringIO()
        call_command("spectacular", stdout=out)
        schema = yaml.safe_load(out.getvalue())
        components = schema.get("components", {}).get("schemas", {})
        cc_key = next(
            (k for k in components if k.lower() == "costcell"),
            None,
        )
        if cc_key is None:
            self.skipTest("CostCell component not found")
        fields = components[cc_key].get("properties", {})
        for expected in ("gpu_slug", "model_slug", "hourly_usd", "usd_per_m_output", "usd_per_m_input"):
            self.assertIn(expected, fields, f"missing field {expected!r} in {list(fields)[:10]}")
