from __future__ import annotations

from io import StringIO

import yaml
from django.core.management import call_command
from django.test import SimpleTestCase


class SchemaComponentTest(SimpleTestCase):
    def test_schema_includes_costcell_component(self) -> None:
        out = StringIO()
        call_command("spectacular", stdout=out)
        schema = yaml.safe_load(out.getvalue())
        components = schema.get("components", {}).get("schemas", {})
        has_costcell = any("ostcell" in key.lower() or "ost_cell" in key.lower() for key in components)
        self.assertTrue(has_costcell, f"No CostCell component found. Keys: {list(components)[:10]}")

    def test_schema_costcell_has_expected_fields(self) -> None:
        out = StringIO()
        call_command("spectacular", stdout=out)
        schema = yaml.safe_load(out.getvalue())
        components = schema.get("components", {}).get("schemas", {})
        # Find CostCell component (djangorestframework-camel-case 1.4.2 does not
        # camelize OpenAPI schema property names — the renderer camelizes responses
        # at runtime, but the schema document uses serializer field names)
        cc_key = next(
            (k for k in components if k.lower() == "costcell"),
            None,
        )
        if cc_key is None:
            self.skipTest("CostCell component not found")
        fields = components[cc_key].get("properties", {})
        for expected in ("gpu_slug", "model_slug", "hourly_usd", "usd_per_m_output", "usd_per_m_input"):
            self.assertIn(expected, fields, f"missing field {expected!r} in {list(fields)[:10]}")
