from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response

if TYPE_CHECKING:
    from rest_framework.request import Request


@api_view(["GET"])  # type: ignore[untyped-decorator]
@throttle_classes([])  # type: ignore[untyped-decorator]
def health(request: Request) -> Response:
    return Response({"status": "ok"})
