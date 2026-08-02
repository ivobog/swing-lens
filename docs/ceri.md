# Catalyst and Estimate Revision Intelligence

CERI is disabled by default and is intended for local research workflows. The manual provider is the safe fixture provider. The `primary` provider adapter is registered for capability and health reporting, but live fetches are gated behind `CERI_PRIMARY_PROVIDER_API_KEY` and a licensed adapter implementation.

## Provider Controls

- Provider priority is `manual`, then `primary`, matching `config/ceri.yaml`.
- Manual records carry `manual-fixture-1.0` terms metadata unless a caller supplies another version.
- The primary provider stores credentials only in environment variables and reports `credentials_missing` health when no key is configured.
- Primary provider policy defaults to restricted export, no raw-payload storage, no redistribution, 365-day retention, 60 requests/minute, and a three-attempt retry policy.
- Provider outages should degrade CERI status and confidence without blocking the core SwingLens views.

## Export And Redaction

All CERI exports use `CeriExportPolicyRegistry`. Configured restricted fields, source URLs, raw payloads, authorization headers, tokens, API keys, provider secrets, SQL details, and local filesystem paths are masked before they leave service boundaries. Full-evidence exports expose permitted normalized fields and stable evidence identifiers, not vendor source payloads.

## Purge Workflow

Provider-license purge is preview-first and audited.

1. Preview with provider, license scope, actor, and reason.
2. Review affected source-record and downstream dependency counts.
3. Execute only with the confirmation token derived from the preview hash.
4. Preserve the non-sensitive `ceri_purge_audits` record.
5. Mark derived revision and score snapshots for rebuild/invalidation accounting.

Ordinary upload-run deletion must never cascade into CERI source evidence. Licensed-data purge is an explicit administrative operation and is not a rollback feature.

## Observability

Metrics use these required families: `ceri_ingestion_*`, `ceri_freshness_*`, `ceri_coverage_*`, `ceri_scores_*`, `ceri_conflicts_*`, `ceri_jobs_*`, `ceri_processing_*`, `ceri_alerts_*`, and `ceri_purge_*`.

Structured CERI log events include job/run identifiers, provider, dataset, ticker/company when known, calculation version, config hash, request key, and execution token fields where available. Licensed payloads and secrets are redacted.

## Security Defaults

The app binds to `127.0.0.1` by default. CERI admin writes remain disabled unless `ceri_admin_enabled` is set and require the existing local-admin and CSRF checks.
