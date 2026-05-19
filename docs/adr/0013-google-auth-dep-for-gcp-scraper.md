# ADR-0013 — Add `google-auth` dependency for GCP Cloud Billing scraper

**Status:** Accepted  
**Date:** 2026-05-18  
**Milestone:** M05.5a

## Context

The GCP scraper (M05.5a.T03) must authenticate against the Google Cloud Billing Catalog API
(`cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus`) to list GPU pricing SKUs.
The API requires an OAuth 2.0 Bearer token, not an API key.

Two options were considered for obtaining and refreshing the token:

### Option A — `google-auth` (accepted)

Add `google-auth>=2.30` to `pyproject.toml`. Use
`google.auth.default(scopes=[...])` which reads `GOOGLE_APPLICATION_CREDENTIALS`
(a service-account JSON path) automatically, plus falls back to application default
credentials from `gcloud auth application-default login` for local dev.

A thin `_HttpxTransport` wrapper class (10 lines) implements the google-auth transport
interface using the already-pinned `httpx` dep for HTTP, so **no `requests` dependency
is introduced**. `google-auth` alone has a small, stable dep footprint (cachetools,
pyasn1-modules, rsa).

### Option B — `google-cloud-billing` SDK

The official `google-cloud-billing` library would handle auth, pagination, and SKU
deserialization automatically, but it pulls in ~10 transitive dependencies (protobuf,
gapic-generator-python, google-cloud-core, grpcio, googleapis-common-protos, etc.) and
requires generated client stubs. This is disproportionate for what is a paginated HTTP
call to a JSON REST endpoint.

## Decision

Use **Option A**. One new dependency (`google-auth`) is warranted; ten is not.

## Consequences

- `GOOGLE_APPLICATION_CREDENTIALS` must be set in any environment that runs
  `dump_pricing --provider gcp` (service-account JSON path) or `gcloud auth
  application-default login` must have been run locally.
- The GitHub Actions `scrape-pricing.yml` workflow writes the service-account JSON from
  a `GCP_SA_KEY` repo secret into `$RUNNER_TEMP/gcp-sa.json` and exports
  `GOOGLE_APPLICATION_CREDENTIALS` before calling `dump_pricing`.
- Required IAM permission on the billing account: `roles/billing.viewer`
  (or just `cloudbilling.skus.list`). Read-only access to the SKU catalog.
- Mypy: add `google.*` to `ignore_missing_imports` since google-auth ships partial stubs.
