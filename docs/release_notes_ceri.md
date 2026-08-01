# CERI Release Notes

## Phase 10

- Added a credentials-gated primary provider adapter with explicit rate-limit, retry, licensing, and retention metadata.
- Centralized CERI field-level export policy and sensitive-value redaction.
- Added operational metrics and structured event logging for ingestion and provider-license purge flows.
- Added audited provider-license purge preview/execute semantics with confirmation-token enforcement and derived invalidation accounting.
- Documented provider, export, purge, observability, and localhost-admin safety controls.
