from __future__ import annotations

from decimal import Decimal

from django.db import connection


def cost_per_million_tokens(
    *,
    hourly_usd_per_gpu: Decimal,
    tp_size: int,
    tps_aggregate: Decimal,
) -> Decimal:
    if tps_aggregate <= 0:
        raise ValueError("tps_aggregate must be positive")
    seconds_per_million = Decimal(1_000_000) / tps_aggregate
    node_seconds_per_million = seconds_per_million * tp_size
    return (hourly_usd_per_gpu * node_seconds_per_million / Decimal(3600)).quantize(Decimal("0.0001"))


def refresh_cost_cells(*, concurrently: bool = True) -> None:
    sql = "REFRESH MATERIALIZED VIEW {}pricing_current_cost_cells".format(
        "CONCURRENTLY " if concurrently else ""
    )
    with connection.cursor() as c:
        c.execute(sql)


def get_current_cost_cells_queryset() -> object:
    from pricing.models import CurrentCostCell

    return CurrentCostCell.objects.all()
