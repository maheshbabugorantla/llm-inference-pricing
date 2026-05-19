# Pricing Scrapers

Each scraper fetches GPU pricing from one provider and returns `list[ScrapedPrice]`.
All scrapers are registered in `pricing/scrapers/__init__.py` (`SCRAPERS` dict) and
are picked up automatically by the `dump_pricing` / `load_pricing` management commands.

## Providers

| Slug | Source | Auth |
|---|---|---|
| `runpod` | RunPod GraphQL API | None (public) |
| `lambda` | Lambda Labs pricing page | None (public) |
| `vast` | Vast.ai bundles API | None (public) |
| `nebius` | Nebius pricing page | None (public) |
| `gcp` | GCP Cloud Billing Catalog API | Service-account JSON (see below) |

---

## GCP — Google Cloud Platform

### How it works

`gcp.py` calls:
```
GET https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus
```
This is the GCP Compute Engine billing catalog. The scraper pre-filters to
`category.resourceGroup == "GPU"`, then keeps only `us-central1` on-demand and
Committed-Use Discount (CUD) SKUs. Prices are parsed as `Decimal` from the
`units`/`nanos` fields; no `float` arithmetic is used.

### Required IAM permission

The service account needs exactly one permission on the billing account:

```
roles/billing.viewer
```

(This grants `cloudbilling.skus.list` — read-only access to the public SKU
catalog. No billing data or spend information is exposed.)

### Local setup

1. **Create a service account** in the GCP project that owns your billing account:

   ```bash
   gcloud iam service-accounts create pricing-scraper \
     --display-name "LLM Pricing Scraper" \
     --project YOUR_PROJECT_ID
   ```

2. **Grant `roles/billing.viewer`** on the billing account (not the project):

   ```bash
   gcloud billing accounts add-iam-policy-binding YOUR_BILLING_ACCOUNT_ID \
     --member="serviceAccount:pricing-scraper@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/billing.viewer"
   ```

   Find your billing account ID:
   ```bash
   gcloud billing accounts list
   ```

3. **Download the JSON key**:

   ```bash
   gcloud iam service-accounts keys create ~/secrets/pricing-scraper-key.json \
     --iam-account pricing-scraper@YOUR_PROJECT_ID.iam.gserviceaccount.com
   ```

4. **Set the environment variable** before running scrapers locally:

   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS=~/secrets/pricing-scraper-key.json
   python manage.py dump_pricing --provider gcp
   ```

### GitHub Actions setup

The `scrape-pricing.yml` workflow reads the key from a repo secret `GCP_SA_KEY`:

```bash
# Store the key contents as a repo secret (one-time setup)
gh secret set GCP_SA_KEY < ~/secrets/pricing-scraper-key.json
```

The workflow writes the secret to `${RUNNER_TEMP}/gcp-sa.json` and sets
`GOOGLE_APPLICATION_CREDENTIALS` before calling `dump_pricing`. The step is
guarded with `if: env.GCP_SA_KEY != ''` so forks and PRs without the secret
skip it gracefully instead of failing.

See `.github/workflows/scrape-pricing.yml` for the exact step definition.

### Scraper output

`dump_pricing --provider gcp` writes `data/pricing/gcp.json` with rows covering:

| GPU | Tiers |
|---|---|
| NVIDIA H100 80GB | `on_demand`, `cud-1yr`, `cud-3yr` |
| NVIDIA A100 80GB | `on_demand`, `cud-1yr`, `cud-3yr` |
| NVIDIA A100 40GB | `on_demand`, `cud-1yr`, `cud-3yr` |
| NVIDIA L4 | `on_demand`, `cud-1yr`, `cud-3yr` |
| NVIDIA T4 | `on_demand`, `cud-1yr`, `cud-3yr` |
| NVIDIA V100 | `on_demand` |
| NVIDIA P100 | `on_demand` |
| NVIDIA P4 | `on_demand` |

All rows use region `us-central1`. Multi-region capture is deferred to M07.
