import django
from django.conf import settings


def pytest_configure(config: object) -> None:
    if not settings.configured:
        django.setup()
