from __future__ import annotations

from django.test import SimpleTestCase


class JSONOnlyRendererTest(SimpleTestCase):
    """
    Verify that every public API endpoint returns JSON regardless of the
    client's Accept header. BrowsableAPIRenderer is intentionally disabled
    so that browser requests or Accept: text/html never receive an HTML page.
    """

    API_PATHS = [
        "/api/v1/health/",
        "/api/schema/",
    ]

    def _assert_json_response(self, path: str, accept: str) -> None:
        response = self.client.get(path, HTTP_ACCEPT=accept)
        content_type = response.get("Content-Type", "")
        self.assertNotIn(
            "text/html",
            content_type,
            msg=f"Expected JSON but got HTML for {path} with Accept: {accept}",
        )
        self.assertIn(
            "application",
            content_type,
            msg=f"Expected application/* content type for {path} with Accept: {accept}",
        )

    def test_health_returns_json_for_accept_any(self) -> None:
        self._assert_json_response("/api/v1/health/", "*/*")

    def test_health_returns_json_for_accept_html(self) -> None:
        self._assert_json_response("/api/v1/health/", "text/html")

    def test_health_returns_json_for_accept_html_priority(self) -> None:
        # Browser-style Accept header that prefers HTML over JSON
        self._assert_json_response("/api/v1/health/", "text/html,application/xhtml+xml,*/*;q=0.8")

    def test_health_returns_json_for_no_accept_header(self) -> None:
        response = self.client.get("/api/v1/health/")
        self.assertIn("application/json", response.get("Content-Type", ""))

    def test_schema_returns_openapi_not_html(self) -> None:
        # /api/schema/ uses SpectacularAPIView — confirm it never serves HTML
        response = self.client.get("/api/schema/", HTTP_ACCEPT="text/html")
        self.assertNotIn("text/html", response.get("Content-Type", ""))

    def test_html_only_accept_returns_406_not_html_page(self) -> None:
        # A client that accepts only text/html gets 406 Not Acceptable — the
        # correct response when JSON is the only renderer. The key invariant is
        # that the client never receives a browsable-API HTML page; a JSON 406
        # error body satisfies that.
        response = self.client.get("/api/v1/health/", HTTP_ACCEPT="text/html")
        self.assertEqual(response.status_code, 406)
        self.assertNotIn("text/html", response.get("Content-Type", ""))

    def test_browser_accept_header_returns_json_200(self) -> None:
        # Real browsers send text/html first but include */* as a fallback.
        # DRF should negotiate JSON and return 200, not an HTML page.
        accept = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        response = self.client.get("/api/v1/health/", HTTP_ACCEPT=accept)
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.get("Content-Type", ""))
        self.assertEqual(response.json()["status"], "ok")
