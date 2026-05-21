from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from catalog.tests.factories import BenchmarkPointFactory, GPUFactory, ModelFactory, QuantizationFactory
from pricing.services.cost import refresh_cost_cells
from pricing.tests.factories import PricingSnapshotFactory, ProviderFactory


@pytest.mark.django_db(transaction=True)
def test_cost_cell_produced_from_benchmark_and_snapshot():
    quant = QuantizationFactory(slug="fp8")
    gpu = GPUFactory(slug="nvidia-h100-sxm-80", vram_gb=80)
    model = ModelFactory(slug="qwen", recommended_quant=quant)
    bp = BenchmarkPointFactory(
        model=model,
        gpu=gpu,
        quantization=quant,
        tp_size=1,
        batch_size=8,
        context_length=32768,
        prefill_tps_aggregate=28400,
        decode_tps_aggregate=920,
    )
    provider = ProviderFactory(slug="runpod")
    PricingSnapshotFactory(
        provider=provider,
        gpu=gpu,
        tier="community",
        hourly_usd=Decimal("1.99"),
        scraped_at=timezone.now(),
    )

    refresh_cost_cells(concurrently=False)

    with connection.cursor() as c:
        c.execute(
            """
            SELECT usd_per_m_input, usd_per_m_output
            FROM pricing_current_cost_cells
            WHERE benchmark_point_id = %s AND provider_id = %s AND pricing_tier = 'community'
        """,
            [bp.id, provider.id],
        )
        row = c.fetchone()

    assert row is not None
    usd_in, usd_out = Decimal(str(row[0])), Decimal(str(row[1]))
    # PRD §7.6: ≈ $0.019/M input, ≈ $0.60/M output
    assert Decimal("0.01") < usd_in < Decimal("0.05")
    assert Decimal("0.55") < usd_out < Decimal("0.65")


@pytest.mark.django_db(transaction=True)
def test_cost_cell_picks_latest_snapshot():
    gpu = GPUFactory(slug="nvidia-a100-sxm-80")
    provider = ProviderFactory(slug="runpod-latest")
    bp = BenchmarkPointFactory(gpu=gpu, tp_size=1, prefill_tps_aggregate=10000, decode_tps_aggregate=500)

    old_time = timezone.now() - __import__("datetime").timedelta(hours=2)
    PricingSnapshotFactory(
        provider=provider, gpu=gpu, tier="on_demand", hourly_usd=Decimal("9.99"), scraped_at=old_time
    )
    PricingSnapshotFactory(
        provider=provider, gpu=gpu, tier="on_demand", hourly_usd=Decimal("2.00"), scraped_at=timezone.now()
    )

    refresh_cost_cells(concurrently=False)

    with connection.cursor() as c:
        c.execute(
            """
            SELECT hourly_usd FROM pricing_current_cost_cells
            WHERE benchmark_point_id = %s AND provider_id = %s AND pricing_tier = 'on_demand'
        """,
            [bp.id, provider.id],
        )
        row = c.fetchone()

    assert row is not None
    assert Decimal(str(row[0])) == Decimal("2.00")


@pytest.mark.django_db(transaction=True)
def test_cost_cell_excludes_unavailable_snapshots():
    gpu = GPUFactory(slug="nvidia-t4")
    provider = ProviderFactory(slug="provider-unavail")
    bp = BenchmarkPointFactory(gpu=gpu, tp_size=1, prefill_tps_aggregate=5000, decode_tps_aggregate=300)
    PricingSnapshotFactory(
        provider=provider,
        gpu=gpu,
        tier="on_demand",
        hourly_usd=Decimal("0.50"),
        available=False,
        scraped_at=timezone.now(),
    )

    refresh_cost_cells(concurrently=False)

    with connection.cursor() as c:
        c.execute(
            """
            SELECT COUNT(*) FROM pricing_current_cost_cells
            WHERE benchmark_point_id = %s AND provider_id = %s
        """,
            [bp.id, provider.id],
        )
        count = c.fetchone()[0]

    assert count == 0


@pytest.mark.django_db(transaction=True)
def test_invariant_i5_no_nulls_in_cost_cells():
    gpu = GPUFactory(slug="nvidia-l4")
    provider = ProviderFactory(slug="provider-nullcheck")
    BenchmarkPointFactory(gpu=gpu, tp_size=1, prefill_tps_aggregate=8000, decode_tps_aggregate=400)
    PricingSnapshotFactory(
        provider=provider, gpu=gpu, tier="on_demand", hourly_usd=Decimal("0.80"), scraped_at=timezone.now()
    )

    refresh_cost_cells(concurrently=False)

    with connection.cursor() as c:
        c.execute("""
            SELECT COUNT(*) FROM pricing_current_cost_cells
            WHERE usd_per_m_input IS NULL OR usd_per_m_output IS NULL
        """)
        assert c.fetchone()[0] == 0
