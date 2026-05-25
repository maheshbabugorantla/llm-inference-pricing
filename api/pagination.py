from __future__ import annotations

from rest_framework.pagination import CursorPagination


class SlugCursorPagination(CursorPagination):  # type: ignore[misc]
    page_size = 50
    ordering = "slug"


class CostCellCursorPagination(CursorPagination):  # type: ignore[misc]
    page_size = 50
    ordering = ("usd_per_m_output", "row_hash")
