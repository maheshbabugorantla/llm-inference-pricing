from __future__ import annotations

import shutil

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from catalog.tests.seed_helpers import make_tmp_seeds_dir


class ValidateCatalogCommandTest(TestCase):
    def setUp(self):
        self.seeds_dir = make_tmp_seeds_dir()

    def tearDown(self):
        shutil.rmtree(self.seeds_dir, ignore_errors=True)

    def test_validate_catalog_passes_on_good_yaml(self):
        call_command("validate_catalog", "--seeds-dir", str(self.seeds_dir))

    def test_validate_catalog_rejects_bad_yaml(self):
        (self.seeds_dir / "quantizations.yaml").write_text(
            "- slug: bad\n  weight_bits: 7\n  kv_cache_bits: 16\n  display_name: X\n"
        )
        with self.assertRaises(CommandError):
            call_command("validate_catalog", "--seeds-dir", str(self.seeds_dir))
