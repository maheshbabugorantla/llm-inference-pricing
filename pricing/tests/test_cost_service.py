from __future__ import annotations

from decimal import Decimal

import pytest

from pricing.services.cost import cost_per_million_tokens


def test_cost_per_million_qwen32b_h100_fp8_decode_matches_prd():
    # H100 community @ $1.99/hr, tp_size=1, decode_tps=920
    # cost_per_second = 1.99/3600 = 0.000552778
    # usd_per_m = 0.000552778 * 1_000_000 / 920 ≈ 0.6008
    result = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("1.99"),
        tp_size=1,
        tps_aggregate=Decimal("920"),
    )
    assert Decimal("0.55") < result < Decimal("0.65")


def test_cost_scales_linearly_with_hourly_rate():
    base = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("1.00"),
        tp_size=1,
        tps_aggregate=Decimal("1000"),
    )
    doubled = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("2.00"),
        tp_size=1,
        tps_aggregate=Decimal("1000"),
    )
    assert doubled == base * 2


def test_cost_scales_linearly_with_tp_size():
    base = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("2.00"),
        tp_size=1,
        tps_aggregate=Decimal("1000"),
    )
    quad = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("2.00"),
        tp_size=4,
        tps_aggregate=Decimal("1000"),
    )
    # allow 1 ULP of quantize rounding error (4 decimal places)
    assert abs(quad - base * 4) <= Decimal("0.0002")


def test_cost_scales_inversely_with_throughput():
    fast = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("2.00"),
        tp_size=1,
        tps_aggregate=Decimal("2000"),
    )
    slow = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("2.00"),
        tp_size=1,
        tps_aggregate=Decimal("1000"),
    )
    assert fast == slow / 2


def test_cost_per_million_rejects_zero_throughput():
    with pytest.raises(ValueError, match="tps_aggregate"):
        cost_per_million_tokens(
            hourly_usd_per_gpu=Decimal("2.00"),
            tp_size=1,
            tps_aggregate=Decimal("0"),
        )


def test_cost_returns_decimal_not_float():
    result = cost_per_million_tokens(
        hourly_usd_per_gpu=Decimal("1.99"),
        tp_size=1,
        tps_aggregate=Decimal("920"),
    )
    assert isinstance(result, Decimal)
