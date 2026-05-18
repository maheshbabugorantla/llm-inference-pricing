from __future__ import annotations

import pytest
from django.db.utils import IntegrityError

from catalog.tests.factories import BenchmarkPointFactory


@pytest.mark.django_db
def test_benchmark_point_unique_compat_tuple_and_op_point():
    """Invariant I6: no two BenchmarkPoint rows share the same
    (model, gpu, quantization, tp_size, batch_size, context_length)."""
    bp = BenchmarkPointFactory()
    with pytest.raises(IntegrityError):
        BenchmarkPointFactory(
            model=bp.model,
            gpu=bp.gpu,
            quantization=bp.quantization,
            tp_size=bp.tp_size,
            batch_size=bp.batch_size,
            context_length=bp.context_length,
        )


@pytest.mark.django_db
def test_benchmark_point_throughputs_positive():
    bp = BenchmarkPointFactory(prefill_tps_aggregate=1000, decode_tps_aggregate=100)
    assert bp.prefill_tps_aggregate > 0
    assert bp.decode_tps_aggregate > 0
