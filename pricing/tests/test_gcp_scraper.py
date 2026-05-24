from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from pricing.scrapers.base import ParserDriftError
from pricing.scrapers.gcp import GCP_GPU_PATTERNS, map_gcp_gpu, parse_gcp_skus

FIXTURE = Path(__file__).parent / "fixtures" / "gcp_skus_a3.json"


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text())["skus"]


class GcpScraperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.skus = _load_fixture()

    def test_parse_h100_on_demand_price(self) -> None:
        prices = parse_gcp_skus(self.skus)
        h100 = [
            p
            for p in prices
            if p.gpu_slug_hint == "nvidia-h100-sxm-80"
            and p.tier == "on_demand"
            and p.raw.get("description") == "Nvidia H100 80GB GPU running in Americas"
        ]
        self.assertEqual(len(h100), 1)
        self.assertEqual(h100[0].hourly_usd, Decimal("9.796550569"))

    def test_parse_h100_plus_maps_to_h100_slug(self) -> None:
        prices = parse_gcp_skus(self.skus)
        h100_plus = [p for p in prices if "Plus" in p.raw.get("description", "") and p.tier == "on_demand"]
        self.assertGreaterEqual(len(h100_plus), 1)
        self.assertEqual(h100_plus[0].gpu_slug_hint, "nvidia-h100-sxm-80")

    def test_parse_a100_40gb_maps_to_correct_slug(self) -> None:
        prices = parse_gcp_skus(self.skus)
        a100_40 = [p for p in prices if p.gpu_slug_hint == "nvidia-a100-sxm-40" and p.tier == "on_demand"]
        self.assertGreaterEqual(len(a100_40), 1)
        self.assertEqual(a100_40[0].hourly_usd, Decimal("2.933908"))

    def test_parse_a100_80gb_maps_to_correct_slug(self) -> None:
        prices = parse_gcp_skus(self.skus)
        a100_80 = [p for p in prices if p.gpu_slug_hint == "nvidia-a100-sxm-80" and p.tier == "on_demand"]
        self.assertGreaterEqual(len(a100_80), 1)
        self.assertEqual(a100_80[0].hourly_usd, Decimal("3.928080"))

    def test_parse_l4_on_demand_price(self) -> None:
        prices = parse_gcp_skus(self.skus)
        l4 = [p for p in prices if p.gpu_slug_hint == "nvidia-l4" and p.tier == "on_demand"]
        self.assertGreaterEqual(len(l4), 1)
        self.assertEqual(l4[0].hourly_usd, Decimal("0.560040239"))

    def test_parse_t4_on_demand_price(self) -> None:
        prices = parse_gcp_skus(self.skus)
        t4 = [p for p in prices if p.gpu_slug_hint == "nvidia-t4" and p.tier == "on_demand"]
        self.assertGreaterEqual(len(t4), 1)
        self.assertEqual(t4[0].hourly_usd, Decimal("0.350000"))

    def test_parse_cud1yr_rows_present(self) -> None:
        prices = parse_gcp_skus(self.skus)
        cud1 = [p for p in prices if p.tier == "cud-1yr"]
        self.assertGreaterEqual(len(cud1), 2)
        h100_cud1 = [p for p in cud1 if p.gpu_slug_hint == "nvidia-h100-sxm-80"]
        self.assertGreaterEqual(len(h100_cud1), 1)
        self.assertEqual(h100_cud1[0].hourly_usd, Decimal("6.79880857"))

    def test_parse_cud3yr_rows_present(self) -> None:
        prices = parse_gcp_skus(self.skus)
        cud3 = [p for p in prices if p.tier == "cud-3yr"]
        self.assertGreaterEqual(len(cud3), 2)
        a100_80_cud3 = [p for p in cud3 if p.gpu_slug_hint == "nvidia-a100-sxm-80"]
        self.assertGreaterEqual(len(a100_80_cud3), 1)
        self.assertEqual(a100_80_cud3[0].hourly_usd, Decimal("3.09659"))

    def test_parse_returns_decimals(self) -> None:
        prices = parse_gcp_skus(self.skus)
        self.assertTrue(all(isinstance(p.hourly_usd, Decimal) for p in prices))

    def test_parse_drops_dws_skus(self) -> None:
        prices = parse_gcp_skus(self.skus)
        dws = [p for p in prices if "DWS" in p.raw.get("description", "")]
        self.assertEqual(len(dws), 0)

    def test_parse_skips_preemptible_skus(self) -> None:
        prices = parse_gcp_skus(self.skus)
        preemptible = [p for p in prices if "Spot Preemptible" in p.raw.get("description", "")]
        self.assertEqual(len(preemptible), 0)

    def test_parse_skips_zero_price_reserved_placeholders(self) -> None:
        prices = parse_gcp_skus(self.skus)
        zero = [p for p in prices if p.hourly_usd == Decimal("0")]
        self.assertEqual(len(zero), 0)

    def test_parse_drops_vgpu_partition_skus(self) -> None:
        prices = parse_gcp_skus(self.skus)
        vgpu = [p for p in prices if "vGPU" in p.raw.get("description", "")]
        self.assertEqual(len(vgpu), 0)

    def test_parse_all_rows_use_us_central1_region(self) -> None:
        prices = parse_gcp_skus(self.skus)
        self.assertTrue(all(p.region == "us-central1" for p in prices))

    def test_parse_all_rows_use_gcp_provider_slug(self) -> None:
        prices = parse_gcp_skus(self.skus)
        self.assertTrue(all(p.provider_slug == "gcp" for p in prices))

    def test_parse_raises_drift_when_no_gpu_skus(self) -> None:
        with self.assertRaises(ParserDriftError):
            parse_gcp_skus([])

    def test_parse_raises_drift_when_all_skus_filtered_out(self) -> None:
        skus = [
            {
                "skuId": "XXXX",
                "description": "Some other resource running in Americas",
                "category": {"resourceFamily": "Compute", "resourceGroup": "GPU", "usageType": "OnDemand"},
                "serviceRegions": ["us-central1"],
                "pricingInfo": [
                    {"pricingExpression": {"tieredRates": [{"unitPrice": {"units": "1", "nanos": 0}}]}}
                ],
            }
        ]
        with self.assertRaises(ParserDriftError):
            parse_gcp_skus(skus)

    def test_gcp_skus_hash_logged_at_info(self) -> None:
        with patch("pricing.scrapers.gcp.logger") as mock_logger:
            parse_gcp_skus(self.skus)
        self.assertTrue(mock_logger.info.called)
        all_args = " ".join(str(a) for call in mock_logger.info.call_args_list for a in call.args)
        self.assertIn("sha256", all_args)

    def test_map_gcp_h100(self) -> None:
        self.assertEqual(map_gcp_gpu("Nvidia H100 80GB GPU running in Americas"), "nvidia-h100-sxm-80")

    def test_map_gcp_h100_plus(self) -> None:
        self.assertEqual(map_gcp_gpu("Nvidia H100 80GB Plus GPU running in Americas"), "nvidia-h100-sxm-80")

    def test_map_gcp_a100_80gb_vs_40gb_distinction(self) -> None:
        self.assertEqual(map_gcp_gpu("Nvidia Tesla A100 80GB GPU running in Americas"), "nvidia-a100-sxm-80")
        self.assertEqual(map_gcp_gpu("Nvidia Tesla A100 GPU running in Americas"), "nvidia-a100-sxm-40")

    def test_map_gcp_l4(self) -> None:
        self.assertEqual(map_gcp_gpu("Nvidia L4 GPU running in Americas"), "nvidia-l4")

    def test_map_gcp_t4(self) -> None:
        self.assertEqual(map_gcp_gpu("Nvidia Tesla T4 GPU running in Americas"), "nvidia-t4")

    def test_map_gcp_unknown_returns_none(self) -> None:
        self.assertIsNone(map_gcp_gpu("Some Future GPU running in Americas"))

    def test_map_covers_expected_gpu_set(self) -> None:
        slugs = {slug for _, slug in GCP_GPU_PATTERNS}
        expected = {
            "nvidia-h100-sxm-80",
            "nvidia-h200",
            "nvidia-a100-sxm-80",
            "nvidia-a100-sxm-40",
            "nvidia-l4",
            "nvidia-t4",
            "nvidia-v100",
            "nvidia-p100",
            "nvidia-p4",
        }
        self.assertTrue(expected.issubset(slugs))
