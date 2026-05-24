"""RunPod GPU display-name → canonical slug mapping tests.

Design: the mapping table is the single point where RunPod's display names
become the GPU slugs that join PricingSnapshot rows to catalog.GPU rows.
An incorrect or missing mapping means a real GPU price is silently dropped —
never landing in the cost-cell output the customer sees. Tests also verify
the tier taxonomy so pipeline consumers receive the expected tier strings.
"""

from __future__ import annotations

import unittest

from pricing.scrapers.runpod import RUNPOD_GPU_MAP, TIER_FIELDS, map_runpod_gpu


class RunpodMapGpuTest(unittest.TestCase):
    def test_h100_sxm_hint_resolves_to_canonical_nvidia_h100_sxm_80_slug(self) -> None:
        """H100 SXM is RunPod's primary H100 offering. Its canonical slug must match
        the catalog GPU slug so PricingSnapshot rows join correctly to cost cells."""
        self.assertEqual(map_runpod_gpu("H100 SXM"), "nvidia-h100-sxm-80")

    def test_amd_mi300x_hint_resolves_to_amd_canonical_slug(self) -> None:
        """MI300X is an AMD GPU — its slug prefix must be 'amd-', not 'nvidia-'.
        A wrong vendor prefix would make it invisible in AMD-filtered cost queries."""
        slug = map_runpod_gpu("MI300X")
        self.assertIsNotNone(slug)
        self.assertTrue(slug.startswith("amd-"))

    def test_h100_pcie_and_h100_sxm_resolve_to_distinct_canonical_slugs(self) -> None:
        """PCIe and SXM variants have different memory bandwidth and pricing.
        They must map to distinct slugs so they're never merged into the same
        cost cell."""
        sxm = map_runpod_gpu("H100 SXM")
        pcie = map_runpod_gpu("H100 PCIe")
        self.assertIsNotNone(sxm)
        self.assertIsNotNone(pcie)
        self.assertNotEqual(sxm, pcie)

    def test_unknown_gpu_hint_returns_none(self) -> None:
        """An unmapped GPU hint must return None — not raise, not return a default slug.
        The caller decides whether to skip or alert; the mapper must not guess."""
        self.assertIsNone(map_runpod_gpu("UnobtaniumGPU 9000"))

    def test_all_mapped_hints_produce_non_empty_canonical_slug(self) -> None:
        """Every entry in the mapping table must resolve to a non-empty slug.
        An empty or None value in the table would silently drop a real GPU tier
        from all cost-cell calculations."""
        for hint in RUNPOD_GPU_MAP.keys():
            with self.subTest(hint=hint):
                slug = map_runpod_gpu(hint)
                self.assertTrue(slug, f"hint {hint!r} mapped to empty or None slug")

    def test_a100_sxm_80gb_and_40gb_hints_resolve_to_different_slugs(self) -> None:
        """A100 SXM comes in two capacities with different pricing. If both display names
        mapped to the same slug, the 40 GB and 80 GB prices would overwrite each other
        and one variant would disappear from the cost grid."""
        slug_80 = map_runpod_gpu("A100 SXM")
        slug_40 = map_runpod_gpu("A100 SXM 40GB")
        self.assertIsNotNone(slug_80)
        self.assertIsNotNone(slug_40)
        self.assertNotEqual(slug_80, slug_40, "A100 SXM 80GB and 40GB must map to distinct slugs")
        self.assertIn("80", slug_80)
        self.assertIn("40", slug_40)

    def test_all_mapped_slugs_use_nvidia_or_amd_vendor_prefix(self) -> None:
        """Every canonical slug must be prefixed with 'nvidia-' or 'amd-' so GPU rows
        can be looked up by vendor for cost grouping and hardware filtering.
        A slug without a vendor prefix would fall outside all vendor-scoped queries."""
        for hint, slug in RUNPOD_GPU_MAP.items():
            with self.subTest(hint=hint, slug=slug):
                self.assertTrue(
                    slug.startswith("nvidia-") or slug.startswith("amd-"),
                    f"slug {slug!r} for hint {hint!r} has unrecognised vendor prefix",
                )


class RunpodTierFieldsTest(unittest.TestCase):
    def test_runpod_tier_fields_map_to_expected_cost_cell_tier_names(self) -> None:
        """The TIER_FIELDS dict translates RunPod API field names to the tier taxonomy
        used throughout the pipeline. These exact tier strings appear in PricingSnapshot
        rows and must match what the cost-cell query filters on."""
        expected_tiers = {
            "community",
            "secure",
            "community-spot",
            "secure-spot",
            "reserved-1mo",
            "reserved-3mo",
            "reserved-6mo",
            "reserved-1yr",
        }
        actual_tiers = set(TIER_FIELDS.values())
        self.assertEqual(
            actual_tiers,
            expected_tiers,
            f"Tier taxonomy mismatch.\nExpected: {expected_tiers}\nActual: {actual_tiers}",
        )
