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
    """
    Verify that CORS headers are emitted only for /api/ paths.

    CORS_URLS_REGEX = r"^/api/.*$" means the corsheaders middleware skips
    non-API paths entirely, so an allowed origin hitting /admin/ or any other
    non-API route must NOT receive Access-Control-Allow-Origin.
    """

    ALLOWED_ORIGIN = "http://localhost:4200"
    UNKNOWN_ORIGIN = "http://evil.example.com"

    # --- API paths: header must be present for allowed origin ---

    def test_api_path_allowed_origin_gets_acao_header(self) -> None:
        response = self.client.get("/api/v1/health/", HTTP_ORIGIN=self.ALLOWED_ORIGIN)
        self.assertEqual(response.get("Access-Control-Allow-Origin"), self.ALLOWED_ORIGIN)

    def test_api_schema_path_allowed_origin_gets_acao_header(self) -> None:
        response = self.client.get("/api/schema/", HTTP_ORIGIN=self.ALLOWED_ORIGIN)
        self.assertEqual(response.get("Access-Control-Allow-Origin"), self.ALLOWED_ORIGIN)

    def test_api_path_preflight_allowed_origin_gets_acao_header(self) -> None:
        response = self.client.options(
            "/api/v1/health/",
            HTTP_ORIGIN=self.ALLOWED_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        self.assertEqual(response.get("Access-Control-Allow-Origin"), self.ALLOWED_ORIGIN)

    # --- API paths: header must be absent for unknown origin ---

    def test_api_path_unknown_origin_no_acao_header(self) -> None:
        response = self.client.get("/api/v1/health/", HTTP_ORIGIN=self.UNKNOWN_ORIGIN)
        self.assertIsNone(response.get("Access-Control-Allow-Origin"))

    # --- Non-API paths: header must be absent even for allowed origin ---

    def test_non_api_path_allowed_origin_no_acao_header(self) -> None:
        # /admin/ is not under /api/ — corsheaders must not touch the response.
        response = self.client.get("/admin/", HTTP_ORIGIN=self.ALLOWED_ORIGIN)
        self.assertIsNone(response.get("Access-Control-Allow-Origin"))

    def test_non_api_root_allowed_origin_no_acao_header(self) -> None:
        response = self.client.get("/", HTTP_ORIGIN=self.ALLOWED_ORIGIN)
        self.assertIsNone(response.get("Access-Control-Allow-Origin"))

    def test_non_api_path_preflight_no_acao_header(self) -> None:
        # Even a preflight OPTIONS to a non-API path must not get CORS headers.
        response = self.client.options(
            "/admin/",
            HTTP_ORIGIN=self.ALLOWED_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        self.assertIsNone(response.get("Access-Control-Allow-Origin"))

    # --- Credentials must never be allowed ---

    def test_api_path_no_allow_credentials_header(self) -> None:
        response = self.client.get("/api/v1/health/", HTTP_ORIGIN=self.ALLOWED_ORIGIN)
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
        # Use the same predicate as test_schema_includes_costcell_component so
        # this test always runs against the same component, regardless of whether
        # drf-spectacular names it "CostCell", "CurrentCostCell", etc.
        cc_key = next(
            (k for k in components if "costcell" in k.lower() or "cost_cell" in k.lower()),
            None,
        )
        self.assertIsNotNone(cc_key, f"No CostCell component found. Keys: {list(components)[:10]}")
        fields = components[cc_key].get("properties", {})  # type: ignore[index]
        for expected in ("gpu_slug", "model_slug", "hourly_usd", "usd_per_m_output", "usd_per_m_input"):
            self.assertIn(expected, fields, f"missing field {expected!r} in {list(fields)[:10]}")
