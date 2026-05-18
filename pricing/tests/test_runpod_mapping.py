from __future__ import annotations

from pricing.scrapers.runpod import RUNPOD_GPU_MAP, map_runpod_gpu


def test_map_runpod_h100_sxm() -> None:
    assert map_runpod_gpu("H100 SXM") == "nvidia-h100-sxm-80"


def test_map_unknown_gpu_returns_none() -> None:
    assert map_runpod_gpu("UnobtaniumGPU 9000") is None


def test_map_covers_expected_gpu_set() -> None:
    expected = {
        "H100 SXM",
        "H100 PCIe",
        "A100 SXM",
        "L40S",
        "RTX 4090",
    }
    assert expected.issubset(RUNPOD_GPU_MAP.keys())
