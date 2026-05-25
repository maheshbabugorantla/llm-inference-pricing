from __future__ import annotations

from django.core.management.base import BaseCommand

from pricing.services.cost import refresh_cost_cells


class Command(BaseCommand):
    help = "Refresh the current_cost_cells materialized view"

    def handle(self, *args: object, **options: object) -> None:
        refresh_cost_cells()
        self.stdout.write(self.style.SUCCESS("Refreshed current_cost_cells"))
