# Catalyst and Estimate Revision Intelligence

CERI is disabled by default and is intended for local research workflows. The manual provider is the safe fixture provider. The production path is the explicit `eodhd` adapter for estimates, earnings and news, complemented by the `sec` adapter for first-party filings and conservative guidance extraction. The `primary` adapter remains registered for compatibility with older fixtures.

## Provider Controls

- Provider priority is `manual`, `primary`, `eodhd`, then `sec`; live callers select EODHD or SEC explicitly while the first two entries preserve existing fixture behavior.
- Manual records carry `manual-fixture-1.0` terms metadata unless a caller supplies another version.
- EODHD credentials are read only from `EODHD_API_KEY`; SEC uses `SEC_USER_AGENT` and does not require an API key.
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
# CERI Full Stack Operations

The production provider path is explicitly `eodhd`; `primary` remains a
compatibility placeholder for older manual fixtures. EODHD credentials are
read from `EODHD_API_KEY` only. SEC uses a descriptive `SEC_USER_AGENT` and
does not require an API key. IBKR remains the sole CERI price source.

## Safe activation sequence

1. Leave all `CERI_*` flags false and run the offline CERI tests.
2. Configure `EODHD_API_KEY`, SEC user-agent/contact, and keep
   `CERI_ALERTS_ENABLED=false`.
3. Run a fixture validation or `CeriProviderValidationService` report for the
   50-symbol sample; inspect estimate coverage, baselines, earnings, news and
   SEC guidance exceptions.
4. Enable `CERI_ENABLED`, `CERI_PROVIDER_INGEST_ENABLED`, and
   `CERI_BACKFILL_ENABLED`; queue a small EODHD ingest/backfill and inspect CERI
   Ops for health, quarantine and lineage.
5. Enable `CERI_RUN_CAPTURE_ENABLED` and `CERI_UI_ENABLED` for shadow scoring.
6. After the validation gate passes, explicitly enable alerts. Alerts remain
   disabled by default and do not submit broker orders.

## Licensing and purge

EODHD source records are marked restricted, are never included as raw payloads
in normal exports, and carry personal-use/purge lineage. Use Ops **Purge
Preview** first; execution requires the matching manifest hash, confirmation
token and local-admin protection. SEC, IBKR, manual and native SwingLens
evidence is not selected by an EODHD purge.

## Jobs

The durable worker supports provider ingest, normalization, feature rebuild,
capture, standalone change detection, alert rebuild, bounded backfill and
licensed-data purge. Rebuild and change jobs use deterministic request keys;
re-running the same scope is coalesced or deduplicated.
