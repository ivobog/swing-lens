# CERI Release Notes

## Phase 10

- Added a credentials-gated primary provider adapter with explicit rate-limit, retry, licensing, and retention metadata.
- Centralized CERI field-level export policy and sensitive-value redaction.
- Added operational metrics and structured event logging for ingestion and provider-license purge flows.
- Added audited provider-license purge preview/execute semantics with confirmation-token enforcement and derived invalidation accounting.
- Documented provider, export, purge, observability, and localhost-admin safety controls.

## Wave 2: Scoped Processing And Resume Safety

- Scoped capture catalyst evidence by company and effective session.
- Scoped standalone change rebuilds to the requested companies, run, dates,
  and change timestamps.
- Prevented partial normalization, feature, and capture stages from advancing
  the durable CERI pipeline.
- Made alert rebuild request keys stable across job IDs.
- Preserved failed-ticker backfill checkpoints and retried them safely on resume.
- Distinguished stale warnings from unrelated data-quality warnings in change
  detection.
