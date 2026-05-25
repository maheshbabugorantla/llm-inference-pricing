from __future__ import annotations

from django.conf import settings
from django.test import SimpleTestCase


class DRFSettingsTest(SimpleTestCase):
    def test_drf_installed(self):
        self.assertIn("rest_framework", settings.INSTALLED_APPS)

    def test_drf_spectacular_installed(self):
        self.assertIn("drf_spectacular", settings.INSTALLED_APPS)

    def test_corsheaders_installed(self):
        self.assertIn("corsheaders", settings.INSTALLED_APPS)

    def test_api_app_installed(self):
        self.assertIn("api", settings.INSTALLED_APPS)

    def test_cors_middleware_before_common(self):
        mw = settings.MIDDLEWARE
        cors_idx = mw.index("corsheaders.middleware.CorsMiddleware")
        common_idx = mw.index("django.middleware.common.CommonMiddleware")
        self.assertLess(cors_idx, common_idx)

    def test_anon_throttle_configured(self):
        throttle_classes = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_CLASSES", [])
        self.assertIn("rest_framework.throttling.AnonRateThrottle", throttle_classes)
        rates = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})
        self.assertIn("anon", rates)

    def test_json_renderer_configured(self):
        renderers = settings.REST_FRAMEWORK.get("DEFAULT_RENDERER_CLASSES", [])
        self.assertIn("rest_framework.renderers.JSONRenderer", renderers)

    def test_json_parser_configured(self):
        parsers = settings.REST_FRAMEWORK.get("DEFAULT_PARSER_CLASSES", [])
        self.assertIn("rest_framework.parsers.JSONParser", parsers)

    def test_spectacular_schema_class_configured(self):
        self.assertEqual(
            settings.REST_FRAMEWORK.get("DEFAULT_SCHEMA_CLASS"),
            "drf_spectacular.openapi.AutoSchema",
        )

    def test_cors_allows_localhost_4200(self):
        self.assertIn("http://localhost:4200", settings.CORS_ALLOWED_ORIGINS)

    def test_cors_credentials_disabled(self):
        self.assertFalse(settings.CORS_ALLOW_CREDENTIALS)
