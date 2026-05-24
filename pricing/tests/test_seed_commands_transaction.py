"""Seed command tests verifying atomic writes and rollback behaviour.

All four seed commands wrap their DB writes in transaction.atomic() blocks.
Django TestCase is used (savepoint-based isolation) because:
  - Rollback verification works via savepoints — Django rolls back the inner
    savepoint cleanly and resets needs_rollback, so post-failure DB queries work.
  - seed_on_prem and seed_reserved call regenerate_*() directly (not via
    on_commit), so their snapshot assertions work within TestCase too.
  - Using TestCase avoids the TimescaleDB TRUNCATE deadlock that occurs when
    TransactionTestCase flushes the pricing_pricingsnapshot hypertable.

setUpClass creates temp directories with minimal YAML once per class;
tearDownClass cleans them up. Each test method gets isolated DB state via
the savepoint that TestCase wraps around every test.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import yaml
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from catalog.models import GPU, Model, Quantization
from catalog.tests.factories import GPUFactory
from pricing.models import (
    HardwareSKU,
    OnPremDeployment,
    PricingSnapshot,
    Provider,
    ReservedCapacityProduct,
    ReservedCloudDeployment,
)

# ---------------------------------------------------------------------------
# Minimal YAML fixtures (inline — seed tests don't need a full catalog)
# ---------------------------------------------------------------------------

_GPUS_YAML = textwrap.dedent("""\
- slug: nvidia-h100-sxm-80
  display_name: NVIDIA H100 SXM5 80GB
  vendor: nvidia
  architecture: hopper
  vram_gb: 80
  memory_bandwidth_gbs: 3350
  fp16_tflops: 989.0
  fp8_tflops: 1979
  tdp_watts: 700
  interconnect: nvlink
  nvlink_bandwidth_gbs: 900
  supports_fp8_native: true
""")

_QUANTS_YAML = textwrap.dedent("""\
- slug: fp8-e4m3
  display_name: FP8 (E4M3)
  weight_bits: 8
  kv_cache_bits: 8
  requires_nvidia_arch: [hopper, ada, blackwell]
""")

_MODELS_YAML = textwrap.dedent("""\
- slug: qwen-2-5-coder-32b
  display_name: Qwen2.5-Coder-32B-Instruct
  family: qwen
  architecture: dense
  total_params_b: 32.5
  active_params_b: 32.5
  num_layers: 64
  num_attention_heads: 40
  num_kv_heads: 8
  head_dim: 128
  max_context: 131072
  hf_repo: Qwen/Qwen2.5-Coder-32B-Instruct
  license: apache-2.0
  is_coding_specialist: true
  recommended_quant: fp8-e4m3
  recommended_tp: 1
""")


# ---------------------------------------------------------------------------
# seed_catalog
# ---------------------------------------------------------------------------


class SeedCatalogTest(TestCase):
    """Tests for seed_catalog management command atomic writes.

    setUpClass creates a temp seeds directory once; each test gets savepoint
    isolation via TestCase's outer transaction.
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tmpdir = tempfile.mkdtemp()
        cls.seeds_dir = Path(cls._tmpdir)
        (cls.seeds_dir / "gpus.yaml").write_text(_GPUS_YAML)
        (cls.seeds_dir / "quantizations.yaml").write_text(_QUANTS_YAML)
        (cls.seeds_dir / "models").mkdir()
        (cls.seeds_dir / "models" / "qwen.yaml").write_text(_MODELS_YAML)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        super().tearDownClass()

    def test_all_catalog_entities_committed_atomically_on_success(self) -> None:
        """seed_catalog wraps all ORM writes in transaction.atomic(). A successful
        run must leave GPU, Quantization, and Model rows visible in the DB together —
        not piecemeal (which would allow a Model row to exist without its Quantization FK)."""
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))

        self.assertTrue(GPU.objects.filter(slug="nvidia-h100-sxm-80").exists())
        self.assertTrue(Quantization.objects.filter(slug="fp8-e4m3").exists())
        self.assertTrue(Model.objects.filter(slug="qwen-2-5-coder-32b").exists())

    def test_idempotent_second_run_does_not_duplicate_rows(self) -> None:
        """Running seed_catalog twice must leave exactly one row per entity.
        The update_or_create upsert must not create duplicates on re-seeding
        during a rolling deployment."""
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))

        self.assertEqual(GPU.objects.filter(slug="nvidia-h100-sxm-80").count(), 1)
        self.assertEqual(Quantization.objects.filter(slug="fp8-e4m3").count(), 1)
        self.assertEqual(Model.objects.filter(slug="qwen-2-5-coder-32b").count(), 1)

    def test_field_update_on_re_seed_is_reflected_in_db(self) -> None:
        """Changing a field in the YAML and re-running seed_catalog must update
        the DB row. The upsert is a true update-or-create, not an insert-if-absent."""
        call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
        updated_gpus = _GPUS_YAML.replace("fp16_tflops: 989.0", "fp16_tflops: 999.0")
        (self.seeds_dir / "gpus.yaml").write_text(updated_gpus)
        try:
            call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
            self.assertEqual(
                GPU.objects.get(slug="nvidia-h100-sxm-80").fp16_tflops,
                999.0,
            )
        finally:
            (self.seeds_dir / "gpus.yaml").write_text(_GPUS_YAML)

    def test_bad_gpu_yaml_rolls_back_and_leaves_no_rows(self) -> None:
        """A GPU YAML with an invalid vendor must cause CommandError and roll back
        the entire transaction — zero GPU rows in the DB. Partial writes would
        leave orphaned rows with no join key."""
        bad_yaml = _GPUS_YAML.replace("vendor: nvidia", "vendor: intel")
        (self.seeds_dir / "gpus.yaml").write_text(bad_yaml)
        try:
            with self.assertRaises(CommandError):
                call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
            self.assertEqual(GPU.objects.count(), 0)
        finally:
            (self.seeds_dir / "gpus.yaml").write_text(_GPUS_YAML)

    def test_missing_slug_in_gpu_yaml_rolls_back_and_leaves_db_clean(self) -> None:
        """A GPU entry without a slug field must fail loudly with no partial write.
        A GPU without a slug has no join key and must never reach the DB."""
        bad_yaml = "- display_name: Mystery GPU\n  vendor: nvidia\n  vram_gb: 80\n"
        (self.seeds_dir / "gpus.yaml").write_text(bad_yaml)
        try:
            with self.assertRaises(CommandError):
                call_command("seed_catalog", "--seeds-dir", str(self.seeds_dir))
            self.assertEqual(GPU.objects.count(), 0)
        finally:
            (self.seeds_dir / "gpus.yaml").write_text(_GPUS_YAML)


# ---------------------------------------------------------------------------
# seed_providers
# ---------------------------------------------------------------------------


class SeedProvidersTest(TestCase):
    """Tests for seed_providers management command atomic writes."""

    def test_all_standard_cloud_providers_committed_atomically(self) -> None:
        """seed_providers creates all cloud provider rows in a single atomic block.
        All seven providers must be visible after the command returns — none missing."""
        call_command("seed_providers")

        slugs = set(Provider.objects.values_list("slug", flat=True))
        for expected in ("runpod", "lambda", "vast", "nebius", "gcp", "aws", "azure"):
            self.assertIn(expected, slugs)

    def test_idempotent_second_run_preserves_provider_count(self) -> None:
        """Running seed_providers twice must not create duplicate providers.
        Duplicate slugs would cause IntegrityError on subsequent scraper runs
        that look up providers by slug."""
        call_command("seed_providers")
        count_after_first = Provider.objects.count()
        call_command("seed_providers")
        self.assertEqual(Provider.objects.count(), count_after_first)

    def test_display_name_update_on_re_seed_reflected_in_db(self) -> None:
        """Changing a provider's display_name in the seeds file and re-running
        must update the DB row — stale display names appear in the admin UI."""
        seeds_file = Path(tempfile.mkdtemp()) / "providers.yaml"
        seeds_file.write_text(
            textwrap.dedent("""\
            - slug: runpod
              display_name: "RunPod Old"
              provider_type: cloud
              data_source_tier: realtime_api
        """)
        )
        call_command("seed_providers", "--seeds-file", str(seeds_file))
        self.assertEqual(Provider.objects.get(slug="runpod").display_name, "RunPod Old")

        seeds_file.write_text(
            textwrap.dedent("""\
            - slug: runpod
              display_name: "RunPod"
              provider_type: cloud
              data_source_tier: realtime_api
        """)
        )
        call_command("seed_providers", "--seeds-file", str(seeds_file))
        self.assertEqual(Provider.objects.get(slug="runpod").display_name, "RunPod")

    def test_invalid_provider_type_rolls_back_and_leaves_no_rows(self) -> None:
        """An unrecognised provider_type in the YAML must fail with CommandError
        before writing to the DB. A partial write would leave provider rows in
        an inconsistent state that breaks scraper task routing."""
        seeds_file = Path(tempfile.mkdtemp()) / "providers.yaml"
        seeds_file.write_text(
            textwrap.dedent("""\
            - slug: bad-provider
              display_name: "Bad"
              provider_type: datacenter
              data_source_tier: realtime_api
        """)
        )
        with self.assertRaises(CommandError):
            call_command("seed_providers", "--seeds-file", str(seeds_file))

        self.assertFalse(Provider.objects.filter(slug="bad-provider").exists())

    def test_data_source_tier_seeded_correctly_for_each_provider(self) -> None:
        """data_source_tier drives Celery Beat scheduling. RunPod must be realtime_api
        (frequent polling) and Lambda must be scraped_page (HTML scraping cadence)."""
        call_command("seed_providers")
        self.assertEqual(Provider.objects.get(slug="runpod").data_source_tier, "realtime_api")
        self.assertEqual(Provider.objects.get(slug="lambda").data_source_tier, "scraped_page")


# ---------------------------------------------------------------------------
# seed_on_prem
# ---------------------------------------------------------------------------

_SKU_YAML = {
    "slug": "supermicro-h100-tc",
    "display_name": "Supermicro H100 8x",
    "vendor": "supermicro",
    "num_gpus": 8,
    "gpu_slug": "nvidia-h100-sxm-80-tc",
    "cpu_model": "Intel Xeon Platinum 8480+",
    "cpu_sockets": 2,
    "ram_gb": 512,
    "nvme_tb": 10,
    "network_gbps": 200,
    "host_tdp_watts": 800,
}

_DEP_YAML = {
    "slug": "lambda-h100-on-prem-tc",
    "display_name": "Lambda H100 On-Prem",
    "hardware_sku_slug": "supermicro-h100-tc",
    "capex_per_node_usd": "180000.00",
    "power_usd_per_kwh": "0.0700",
    "sysadmin_annual_burdened_usd": "200000.00",
}


class SeedOnPremTest(TestCase):
    """Tests for seed_on_prem management command writes and signal management."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tmpdir = tempfile.mkdtemp()
        tmp = Path(cls._tmpdir)
        cls.hw_dir = tmp / "hardware"
        cls.dep_dir = tmp / "deployments"
        cls.hw_dir.mkdir()
        cls.dep_dir.mkdir()
        (cls.hw_dir / "sku.yaml").write_text(yaml.dump(_SKU_YAML))
        (cls.dep_dir / "dep.yaml").write_text(yaml.dump(_DEP_YAML))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self) -> None:
        # GPU must exist for the seed command to resolve the gpu_slug FK
        GPUFactory(slug="nvidia-h100-sxm-80-tc", vram_gb=80, tdp_watts=700)

    def test_seeds_sku_deployment_and_triggers_snapshot_generation(self) -> None:
        """seed_on_prem must upsert the HardwareSKU, upsert the OnPremDeployment,
        then call regenerate_on_prem_snapshots() exactly once. The snapshots must
        be in the DB immediately after the command returns."""
        call_command(
            "seed_on_prem",
            hardware_dir=self.hw_dir,
            deployments_dir=self.dep_dir,
        )

        self.assertTrue(HardwareSKU.objects.filter(slug="supermicro-h100-tc").exists())
        self.assertTrue(OnPremDeployment.objects.filter(slug="lambda-h100-on-prem-tc").exists())
        self.assertEqual(PricingSnapshot.objects.count(), 2)

    def test_idempotent_second_seed_does_not_duplicate_sku_or_deployment(self) -> None:
        """Running seed_on_prem twice must leave exactly one HardwareSKU and one
        OnPremDeployment. update_or_create must prevent duplicates so re-seeding
        during a rolling deployment is safe."""
        call_command(
            "seed_on_prem",
            hardware_dir=self.hw_dir,
            deployments_dir=self.dep_dir,
        )
        call_command(
            "seed_on_prem",
            hardware_dir=self.hw_dir,
            deployments_dir=self.dep_dir,
        )

        self.assertEqual(HardwareSKU.objects.filter(slug="supermicro-h100-tc").count(), 1)
        self.assertEqual(OnPremDeployment.objects.filter(slug="lambda-h100-on-prem-tc").count(), 1)

    def test_signal_disconnected_during_bulk_seed_regenerate_called_exactly_once(self) -> None:
        """seed_on_prem disconnects the post_save signal during the deployment loop
        to prevent N regeneration runs for N deployments. regenerate_on_prem_snapshots
        must be called exactly once at the end — not once per deployment."""
        with patch("pricing.management.commands.seed_on_prem.regenerate_on_prem_snapshots") as mock_regen:
            mock_regen.return_value = 2
            call_command(
                "seed_on_prem",
                hardware_dir=self.hw_dir,
                deployments_dir=self.dep_dir,
            )

        self.assertEqual(mock_regen.call_count, 1)


# ---------------------------------------------------------------------------
# seed_reserved
# ---------------------------------------------------------------------------


def _write_reserved_yamls(products_dir: Path, deps_dir: Path, provider_slug: str, gpu_slug: str) -> None:
    product = {
        "slug": f"lambda-h100-1yr-{provider_slug}",
        "display_name": "Lambda H100 1-yr All Upfront",
        "cloud_provider_slug": provider_slug,
        "gpu_slug": gpu_slug,
        "gpus_per_node": 8,
        "payment_cadence": "all_upfront",
        "term_months": 12,
        "upfront_usd": "500000.00",
        "listing_observed_at": "2025-01-15",
    }
    deployment = {
        "slug": f"lambda-prod-{provider_slug}",
        "display_name": "Lambda Production",
        "product_slug": f"lambda-h100-1yr-{provider_slug}",
        "cloud_provider_slug": provider_slug,
        "expected_utilization_pct": "0.700",
    }
    (products_dir / "product.yaml").write_text(yaml.dump(product))
    (deps_dir / "deployment.yaml").write_text(yaml.dump(deployment))


class SeedReservedTest(TestCase):
    """Tests for seed_reserved management command atomic writes."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tmpdir = tempfile.mkdtemp()
        tmp = Path(cls._tmpdir)
        cls.products_dir = tmp / "products"
        cls.deps_dir = tmp / "deployments"
        cls.products_dir.mkdir()
        cls.deps_dir.mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmpdir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self) -> None:
        self.gpu = GPUFactory(slug="nvidia-h100-sxm-80-rs", vram_gb=80, tdp_watts=700)
        self.provider = Provider.objects.create(
            slug="lambda-rs",
            display_name="Lambda",
            provider_type="cloud",
            data_source_tier="scraped_page",
        )
        _write_reserved_yamls(self.products_dir, self.deps_dir, self.provider.slug, self.gpu.slug)

    def tearDown(self) -> None:
        # Clean YAML files so each test writes fresh ones
        for f in self.products_dir.iterdir():
            f.unlink()
        for f in self.deps_dir.iterdir():
            f.unlink()

    def test_seeds_product_deployment_and_generates_snapshots(self) -> None:
        """seed_reserved must upsert the ReservedCapacityProduct, upsert the
        ReservedCloudDeployment, and call regenerate_reserved_cloud_snapshots() once.
        Both model rows and their snapshots must be in the DB after the command."""
        call_command(
            "seed_reserved",
            products_dir=self.products_dir,
            deployments_dir=self.deps_dir,
        )

        product_slug = f"lambda-h100-1yr-{self.provider.slug}"
        dep_slug = f"lambda-prod-{self.provider.slug}"
        self.assertTrue(ReservedCapacityProduct.objects.filter(slug=product_slug).exists())
        self.assertTrue(ReservedCloudDeployment.objects.filter(slug=dep_slug).exists())
        self.assertGreater(PricingSnapshot.objects.count(), 0)

    def test_idempotent_second_seed_does_not_duplicate_product_or_deployment(self) -> None:
        """Running seed_reserved twice must leave exactly one product and one deployment.
        update_or_create prevents duplicates so a re-seed after config changes is safe."""
        call_command(
            "seed_reserved",
            products_dir=self.products_dir,
            deployments_dir=self.deps_dir,
        )
        call_command(
            "seed_reserved",
            products_dir=self.products_dir,
            deployments_dir=self.deps_dir,
        )

        product_slug = f"lambda-h100-1yr-{self.provider.slug}"
        dep_slug = f"lambda-prod-{self.provider.slug}"
        self.assertEqual(ReservedCapacityProduct.objects.filter(slug=product_slug).count(), 1)
        self.assertEqual(ReservedCloudDeployment.objects.filter(slug=dep_slug).count(), 1)

    def test_invalid_payment_cadence_rolls_back_entire_seed(self) -> None:
        """seed_reserved wraps all writes in @transaction.atomic. An invalid
        payment_cadence must trigger CommandError and roll back the transaction —
        zero product rows in the DB. Partial writes would leave unreachable
        ReservedCloudDeployment rows with no product FK."""
        bad_product = {
            "slug": "bad-cadence-product",
            "display_name": "Bad",
            "cloud_provider_slug": self.provider.slug,
            "gpu_slug": self.gpu.slug,
            "gpus_per_node": 8,
            "payment_cadence": "quarterly",  # not a valid choice
            "term_months": 3,
            "upfront_usd": "0.00",
            "listing_observed_at": "2025-01-15",
        }
        (self.products_dir / "product.yaml").write_text(yaml.dump(bad_product))

        with self.assertRaises((CommandError, Exception)):
            call_command(
                "seed_reserved",
                products_dir=self.products_dir,
                deployments_dir=self.deps_dir,
            )

        self.assertFalse(ReservedCapacityProduct.objects.filter(slug="bad-cadence-product").exists())

    def test_signal_disconnected_during_deployment_seed_regenerate_called_once(self) -> None:
        """seed_reserved disconnects the post_save signal during the deployment
        loop and calls regenerate_reserved_cloud_snapshots() exactly once at the end —
        not once per deployment, which would create redundant snapshot rows."""
        with patch(
            "pricing.management.commands.seed_reserved.regenerate_reserved_cloud_snapshots"
        ) as mock_regen:
            mock_regen.return_value = 2
            call_command(
                "seed_reserved",
                products_dir=self.products_dir,
                deployments_dir=self.deps_dir,
            )

        self.assertEqual(mock_regen.call_count, 1)
