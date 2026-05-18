from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_validate_catalog_passes_on_good_yaml(tmp_seeds_dir):
    call_command("validate_catalog", "--seeds-dir", str(tmp_seeds_dir))


def test_validate_catalog_rejects_bad_yaml(tmp_seeds_dir):
    (tmp_seeds_dir / "quantizations.yaml").write_text(
        "- slug: bad\n  weight_bits: 7\n  kv_cache_bits: 16\n  display_name: X\n"
    )
    with pytest.raises(CommandError):
        call_command("validate_catalog", "--seeds-dir", str(tmp_seeds_dir))
