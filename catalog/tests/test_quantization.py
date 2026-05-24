from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.tests.factories import QuantizationFactory


class QuantizationTest(TestCase):
    def test_quantization_slug_must_be_unique(self) -> None:
        QuantizationFactory(slug="fp8-e4m3")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QuantizationFactory(slug="fp8-e4m3")

    def test_quantization_str_returns_display_name(self) -> None:
        q = QuantizationFactory(display_name="FP8 (E4M3)")
        self.assertEqual(str(q), "FP8 (E4M3)")

    def test_quantization_weight_bits_constrained_to_valid_values(self) -> None:
        """Invariant I7."""
        q = QuantizationFactory(weight_bits=16)
        q.full_clean()
        q.weight_bits = 7
        with self.assertRaises(ValidationError):
            q.full_clean()
