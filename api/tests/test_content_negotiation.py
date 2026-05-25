from __future__ import annotations

from django.test import SimpleTestCase


class JSONOnlyRendererTest(SimpleTestCase):
    """
    Verify that the API never serves an HTML page, regardless of Accept header.

    With BrowsableAPIRenderer disabled, two outcomes are possible:
    - Accept header includes */* or application/json → 200 application/json
    - Accept header is text/html only (no fallback) → 406 application/json

    Neither outcome is an HTML page. The invariant is "never HTML", not
    "always 200 JSON".
    """

    def _assert_never_serves_html(self, path: str, accept: str) -> None:
        """Assert the response content-type is never text/html."""
        response = self.client.get(path, HTTP_ACCEPT=accept)
        content_type = response.get("Content-Type", "")
        self.assertNotIn(
            "text/html",
            content_type,
            msg=f"Got HTML response for {path} with Accept: {accept}",
        )
        self.assertIn(
            "application",
            content_type,
            msg=f"Expected application/* content type for {path} with Accept: {accept}",
        )

    def test_health_never_serves_html_for_accept_any(self) -> None:
        self._assert_never_serves_html("/api/v1/health/", "*/*")

    def test_health_never_serves_html_for_accept_html_only(self) -> None:
        # text/html only → 406, but the error body is application/json, not HTML.
        self._assert_never_serves_html("/api/v1/health/", "text/html")

    def test_health_never_serves_html_for_browser_accept(self) -> None:
        # Browser default: text/html preferred but */* included as fallback.
        self._assert_never_serves_html(
            "/api/v1/health/",
            "text/html,application/xhtml+xml,*/*;q=0.8",
        )

    def test_health_returns_json_200_for_no_accept_header(self) -> None:
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.get("Content-Type", ""))

    def test_schema_never_serves_html(self) -> None:
        response = self.client.get("/api/schema/", HTTP_ACCEPT="text/html")
        self.assertNotIn("text/html", response.get("Content-Type", ""))

    def test_html_only_accept_returns_406_not_html_page(self) -> None:
        # DRF returns 406 when the client cannot accept the only available
        # representation (JSON). The 406 body itself is application/json.
        response = self.client.get("/api/v1/health/", HTTP_ACCEPT="text/html")
        self.assertEqual(response.status_code, 406)
        self.assertNotIn("text/html", response.get("Content-Type", ""))

    def test_browser_accept_header_returns_json_200(self) -> None:
        # */* fallback in browser Accept headers lets DRF negotiate JSON → 200.
        accept = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        response = self.client.get("/api/v1/health/", HTTP_ACCEPT=accept)
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.get("Content-Type", ""))
        self.assertEqual(response.json()["status"], "ok")
