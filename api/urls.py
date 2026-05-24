from __future__ import annotations

from django.urls import path

from api.views.health import health

urlpatterns = [
    path("v1/health/", health, name="health"),
]
