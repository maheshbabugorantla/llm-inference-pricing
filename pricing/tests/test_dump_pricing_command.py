from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from pricing.models import PricingSnapshot
from pricing.scrapers import ScraperEntry
from pricing.scrapers.base import ScrapedPrice


def _fake_price(slug: str = "runpod") -> ScrapedPrice:
    return ScrapedPrice(
        provider_slug=slug,
        gpu_slug_hint="H100 SXM",
        tier="on_demand",
        region="",
        hourly_usd=Decimal("2.50"),
        available=True,
        raw={"gpu": "H100 SXM", "price": "2.50"},
    )


def _entry(slug: str, prices: list[ScrapedPrice] | None = None) -> ScraperEntry:
    p = prices if prices is not None else [_fake_price(slug)]
    return ScraperEntry(
        scrape_fn=lambda: p,
        gpu_slug_resolver=lambda x: x,
        source_url=f"https://{slug}.example.com",
    )


class DumpPricingCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.tmp_path = Path(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_dump_pricing_writes_per_provider_json(self) -> None:
        from django.core.management import call_command

        with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": _entry("runpod")}):
            call_command("dump_pricing", "--provider", "runpod", "--out", str(self.tmp_path))

        out_file = self.tmp_path / "runpod.json"
        self.assertTrue(out_file.exists())
        data = json.loads(out_file.read_text())
        self.assertEqual(data["provider_slug"], "runpod")
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(len(data["prices"]), 1)
        self.assertEqual(data["prices"][0]["hourly_usd"], "2.50")

    def test_dump_pricing_all_writes_all_providers(self) -> None:
        from django.core.management import call_command

        fake_scrapers = {
            "runpod": _entry("runpod"),
            "lambda": _entry("lambda"),
            "vast": _entry("vast"),
            "nebius": _entry("nebius"),
        }
        with patch.dict("pricing.scrapers.SCRAPERS", fake_scrapers, clear=True):
            call_command("dump_pricing", "--provider", "all", "--out", str(self.tmp_path))

        for slug in ("runpod", "lambda", "vast", "nebius"):
            self.assertTrue((self.tmp_path / f"{slug}.json").exists())

    def test_dump_pricing_does_not_overwrite_on_scraper_exception(self) -> None:
        from django.core.management import call_command

        prior_content = '{"schema_version": 1, "provider_slug": "runpod", "prices": []}\n'
        (self.tmp_path / "runpod.json").write_text(prior_content)

        broken = ScraperEntry(
            scrape_fn=lambda: (_ for _ in ()).throw(RuntimeError("network down")),
            gpu_slug_resolver=lambda x: x,
            source_url="https://runpod.example.com",
        )
        with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": broken}):
            with self.assertRaises(SystemExit):
                call_command("dump_pricing", "--provider", "runpod", "--out", str(self.tmp_path))

        self.assertEqual((self.tmp_path / "runpod.json").read_text(), prior_content)

    def test_dump_pricing_returns_nonzero_on_partial_failure(self) -> None:
        from django.core.management import call_command

        broken = ScraperEntry(
            scrape_fn=lambda: (_ for _ in ()).throw(RuntimeError("bang")),
            gpu_slug_resolver=lambda x: x,
            source_url="https://runpod.example.com",
        )
        with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": broken}):
            with self.assertRaises(SystemExit) as ctx:
                call_command("dump_pricing", "--provider", "runpod", "--out", str(self.tmp_path))

        self.assertNotEqual(ctx.exception.code, 0)


class DumpPricingDatabaseTest(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.tmp_path = Path(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_dump_pricing_does_not_touch_database(self) -> None:
        from django.core.management import call_command

        with patch.dict("pricing.scrapers.SCRAPERS", {"runpod": _entry("runpod")}):
            call_command("dump_pricing", "--provider", "runpod", "--out", str(self.tmp_path))

        self.assertEqual(PricingSnapshot.objects.count(), 0)
