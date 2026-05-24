from __future__ import annotations

from django.core.management import call_command
from django.test import TestCase

from catalog.models import GPU, Model, Quantization


class RealSeedsLoadTest(TestCase):
    def test_real_seeds_load(self) -> None:
        """Smoke test: seed_catalog loads 16 GPUs, 5 models, 8 quantizations."""
        call_command("seed_catalog")
        self.assertEqual(GPU.objects.count(), 16)
        self.assertEqual(Model.objects.count(), 5)
        self.assertEqual(Quantization.objects.count(), 8)
        for slug in ("nvidia-t4", "nvidia-v100", "nvidia-p100", "nvidia-p4"):
            self.assertTrue(GPU.objects.filter(slug=slug).exists(), f"missing GPU slug {slug!r}")
