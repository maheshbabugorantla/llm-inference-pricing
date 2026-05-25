from __future__ import annotations

from django.test import TestCase

from pricing.models import CurrentCostCell

_MSG = "CurrentCostCell is read-only — backed by a materialized view"


class CurrentCostCellReadOnlyTest(TestCase):
    """Every write path on CurrentCostCell must raise TypeError immediately."""

    def test_queryset_create_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "read-only"):
            CurrentCostCell.objects.create(row_hash="abc")

    def test_queryset_update_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "read-only"):
            CurrentCostCell.objects.update(gpu_slug="x")

    def test_queryset_delete_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "read-only"):
            CurrentCostCell.objects.all().delete()

    def test_queryset_get_or_create_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "read-only"):
            CurrentCostCell.objects.get_or_create(row_hash="abc")

    def test_queryset_update_or_create_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "read-only"):
            CurrentCostCell.objects.update_or_create(row_hash="abc")

    def test_queryset_bulk_create_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "read-only"):
            CurrentCostCell.objects.bulk_create([])

    def test_queryset_bulk_update_raises(self) -> None:
        with self.assertRaisesRegex(TypeError, "read-only"):
            CurrentCostCell.objects.bulk_update([], fields=["gpu_slug"])

    def test_instance_save_raises(self) -> None:
        cell = CurrentCostCell()
        with self.assertRaisesRegex(TypeError, "read-only"):
            cell.save()

    def test_instance_delete_raises(self) -> None:
        cell = CurrentCostCell()
        with self.assertRaisesRegex(TypeError, "read-only"):
            cell.delete()
