# Phase 13 Review - Catalyst and Estimate-Revision Intelligence

Date: 2026-08-02
Reviewer: Codex
Scope: CERI provider protocol and registry, manual and primary providers, source-record
ingestion, normalization, point-in-time estimate queries, catalyst taxonomy and revisions,
conflict/manual-review flows, opportunity/risk scoring, change detection, alerts, exports,
licensed-data purge, routes, schema, docs, and CERI tests.

## Objective

Validate provider controls, normalization, point-in-time queries, revisions, conflict handling,
export restrictions, and licensed-data lifecycle.

## Executive Summary

Phase 13 is not exit-ready.

CERI has a strong local-first foundation. It is disabled by default, the manual provider is the safe
fixture provider, the primary provider reports health/capabilities while live fetches remain gated,
source records carry content hashes and quarantine state, normalization covers fiscal periods,
effective sessions, verified currency conversion, estimates, guidance, earnings, surprise values,
and catalysts, and export redaction is recursive and tested against sensitive nested content. The
focused CERI suite is green.

The exit blockers are in historical semantics, correction lifecycle, and purge execution. The
low-level point-in-time estimate query implements `AS_KNOWN` and `LATEST_CORRECTED`, but the public
score-history query only validates that `mode` and `as_of` are present; it does not actually branch
on the historical mode. Source-record correction fields exist, but ingestion deduplicates only by a
content-hash idempotency key while the schema also enforces one row per provider record id; changed
content for the same provider record cannot become an auditable supersession chain through the
service. Licensed-data purge is preview-first and confirmation-bound, but execution marks an audit
as `EXECUTED` without deleting, tombstoning, redacting, quarantining, or invalidating affected
licensed records and derivatives.

## Evidence Log

| Check | Result | Notes |
| --- | --- | --- |
| Phase 13 checklist from `C:/Users/Ivica/Downloads/software_review_plan.md` | Reviewed | Objective, review activities, outputs, and exit criteria mapped. |
| Focused CERI suite | Passed | `uv run pytest tests\ceri -q` -> `135 passed, 1 warning in 13.94s`. |
| Provider protocol and registry | Mostly satisfied | Protocol covers health, capabilities, identity, estimates, earnings, guidance, and catalysts. Registry exposes priority order, health, capabilities, and dataset policy. |
| Manual provider behavior | Satisfied | Manual provider supports JSON/CSV fixtures, dataset inference, malformed-record quarantine marker, and fixture health reporting. |
| Primary provider controls | Mostly satisfied | Credentials come from `CERI_PRIMARY_PROVIDER_API_KEY`, missing credentials report unhealthy health, and live fetches raise until a licensed adapter exists. Rate/retry/licensing policies are metadata only until live implementation exists. |
| Source-record idempotency and corrections | Partial | Stable content hashes and idempotency keys exist. Supersession/correction fields exist in schema but are not set by source-record ingestion. |
| Normalization | Mostly satisfied | Fiscal periods, effective sessions, currency/scale conversion, estimates, earnings, guidance, and surprise calculations are implemented and tested. |
| Historical point-in-time semantics | Partial | Low-level estimate query supports `AS_KNOWN` and `LATEST_CORRECTED`; public score-history query does not apply the mode. |
| Conflict/manual review | Partial | Conflict detection and review-state APIs exist. Operating procedure needs stronger quarantine/review state transitions and post-review recalculation obligations. |
| Export restrictions | Mostly satisfied | Recursive redaction masks restricted fields, sensitive nested keys, bearer tokens, local paths, SQL details, source URLs, and raw payloads. |
| Licensed-data purge | Not met | Preview, token confirmation, audit, and rebuild-count manifest exist. Execution does not actually purge or invalidate data. |
| Provider outage isolation | Mostly satisfied | CERI jobs can fail/partial without blocking core SwingLens; provider health is separate and CERI is optional/disabled by default. |

## Provider Compliance and Data-Lineage Report

Provider controls:

- CERI is disabled by default in config (`config/ceri.yaml:1-4`).
- Provider priority is manual first, primary second (`config/ceri.yaml:7-9`).
- Provider protocol requires health, capabilities, identity resolution, estimates, earnings,
  guidance, and catalysts (`app/services/ceri/provider_protocol.py:19-38`).
- Registry reports capabilities and health and derives dataset export/raw-payload policy
  (`app/services/ceri/provider_registry.py:36-60`).
- Primary provider reads credentials from `CERI_PRIMARY_PROVIDER_API_KEY`, reports
  `credentials_missing` when absent, and blocks live fetches without the licensed adapter
  (`app/services/ceri/providers/primary_provider.py:52-65`,
  `app/services/ceri/providers/primary_provider.py:87-101`,
  `app/services/ceri/providers/primary_provider.py:133-140`).
- Manual provider supports local JSON/CSV records and health/capability reporting
  (`app/services/ceri/providers/manual_provider.py:36-72`,
  `app/services/ceri/providers/manual_provider.py:74-85`).

Data-lineage controls:

- Ingestion runs are keyed by deterministic request key and persist provider, dataset, terms
  version, config version/hash, counts, quota, checkpoints, warnings, and errors
  (`app/services/ceri/source_record_service.py:26-71`,
  `app/services/ceri/source_record_service.py:151-233`).
- Source records persist provider, provider terms version, dataset, provider record id, timestamps,
  source URL/reference, raw or restricted JSON, content hash, idempotency key, export policy,
  retention deadline, supersession fields, correction type, and quarantine reason
  (`app/models/ceri_tables.py:173-216`).
- Estimate snapshots persist source id, company id, metric, fiscal period, consensus/high/low,
  analyst breadth, source/canonical currency and scale, conversion lineage, effective time/session,
  canonical observation key, original fields, and quality flags
  (`app/models/ceri_tables.py:220-274`).
- Catalyst event revisions store source id, prior/outcome revision ids, revision number, current
  flag, date/status/direction/materiality/confidence, operational values, conflicts, review state,
  and creation time (`app/models/ceri_tables.py:382-431`).

Lineage gap:

- Source-record supersession fields are schema-only today. `store_source_record` computes a
  content-hash idempotency key, dedupes exact content, and inserts a new row; it never finds the
  prior provider record, sets `supersedes_id`, sets `correction_type`, or increments
  `corrected_count` (`app/services/ceri/source_record_service.py:73-141`).

## Point-in-Time Correctness Test Matrix

| Scenario | Current coverage | Required before exit |
| --- | --- | --- |
| `AS_KNOWN` estimate excludes future effective records | Covered by `CeriPointInTimeQuery` tests | Keep and add DB-backed integration test. |
| `LATEST_CORRECTED` applies later correction to an as-known original | Covered at low-level estimate query | Add ingestion/service path that creates correction chain, then query it. |
| Public ticker score history requires explicit mode and cutoff | Covered | Add mode-difference assertions. Current implementation ignores mode after validation. |
| Score snapshots use corrected estimate lineage | Gap | Public history should expose as-known score and latest-corrected score separately or document score snapshots as stored-only, not corrected PIT. |
| Late-arriving provider record after cutoff | Partial | Need full source-record + normalized estimate + revision feature + score snapshot tests. |
| Superseded source record invisible in `AS_KNOWN` but corrected in `LATEST_CORRECTED` | Gap in ingestion | Requires service-created `supersedes_id` and `correction_type`. |
| Baseline revision window tolerance | Covered at low-level estimate query | Add evidence-hash and selected-provider lineage assertion in feature generation. |
| Quarantined source record excluded from normalization and exports restricted | Covered in source-record/normalization/export tests | Add route-level operations quarantine test with hostile payload. |

## Redaction Attack Test Suite

Current redaction behavior:

- Config marks `source_url` and `raw_payload` restricted (`config/ceri.yaml:138-147`).
- Export policy treats configured restricted fields and sensitive field fragments as non-exportable
  (`app/services/ceri/export_policy.py:12-24`,
  `app/services/ceri/export_policy.py:51-62`).
- Redaction recurses into mappings, lists, and tuples
  (`app/services/ceri/export_policy.py:86-105`).
- Text redaction masks bearer tokens, local filesystem paths, and SQL-like details
  (`app/services/ceri/export_policy.py:26-33`,
  `app/services/ceri/export_policy.py:118-122`).
- Full-evidence export explicitly masks source URL and raw payload and only emits permitted fields
  (`app/services/ceri/export_service.py:81-112`).

Required attack cases:

| Payload | Expected |
| --- | --- |
| Nested `authorization`, `provider_secret`, `api_key`, `raw_payload`, `source_url` keys | Field values replaced with `<restricted:...>`. |
| Bearer token embedded in warning/error text | Token replaced with `Bearer <restricted:token>`. |
| Windows/macOS/Linux local path in provider payload or error | Path replaced with `<restricted:path>`. |
| SQL text containing statement and table/where/values markers | Entire string replaced with `<restricted:sql>`. |
| Source URL hidden under nested object/list | Redacted recursively. |
| Provider raw payload under exportable wrapper | Raw payload masked by field policy. |
| Authorization header in ingestion errors/job result | Redacted before returning routes or exports. |

Remaining work:

- Add route-level tests for `/api/ceri/jobs/{job_id}` because it returns job payload/result/error
  directly (`app/routers/ceri_routes.py:525-559`).
- Add CSV formula-injection escaping if CERI exports can contain user/provider strings in
  spreadsheet-opened CSV files.

## Conflict and Manual-Review Operating Procedure

Current controls:

- Estimate conflict service chooses by provider priority, quality flags, freshness, and source id;
  competing rows are preserved (`app/services/ceri/provider_conflict_service.py:17-54`).
- Estimate dedup groups by canonical observation key, effective session, and consensus tolerance
  (`app/services/ceri/estimate_deduplicator.py:13-43`).
- Catalyst dedup clusters by company/category/subtype/subject and flags conflicting event dates or
  mutually exclusive statuses (`app/services/ceri/catalyst_deduplicator.py:13-54`).
- Operations endpoints expose quarantine and conflicts
  (`app/services/ceri/query_service.py:348-374`,
  `app/routers/ceri_routes.py:483-508`).
- Manual review records prior/new values, reviewer, and reason; route conflict-detects active review
  mismatches and can update current revision review state
  (`app/services/ceri/manual_review_service.py:11-63`,
  `app/routers/ceri_routes.py:631-672`).

Procedure before release:

1. Conflicted source records or catalyst revisions enter `PENDING_REVIEW`, not just warning flags.
2. Operations page shows provider, dataset, source id, conflict type, selected value, competing
   values, confidence degradation, and recommended action.
3. Reviewer chooses accept selected, override fields, quarantine source, mark duplicate, or defer.
4. Review action creates immutable manual review row and a new catalyst/estimate revision where
   applicable.
5. Dependent revision features, score snapshots, changes, and alerts are rebuilt with lineage back to
   the review id.
6. Export includes review state and evidence hash, but not restricted raw provider payloads.

## Purge and Rebuild Verification Checklist

Current controls:

- Config disables provider-license purge by default and requires preview, confirmation, and audit
  alignment (`config/ceri.yaml:149-154`,
  `app/services/ceri/config.py:638-646`).
- Preview computes affected source records and derivatives, stores a preview manifest hash,
  confirmation-token hash, affected counts, invalidated derivative counts, actor, and reason
  (`app/services/ceri/purge_service.py:51-100`,
  `app/services/ceri/purge_service.py:147-201`).
- Execute requires prior preview, matching scope, and matching confirmation token
  (`app/services/ceri/purge_service.py:102-124`).
- Routes require local admin and explicit confirmation token for execution
  (`app/routers/ceri_routes.py:771-820`).

Required before exit:

- Decide lifecycle semantics: delete, tombstone, redact, quarantine, or audit-only attestation.
- If named "purge", execution must apply the selected lifecycle action to source records and
  affected normalized rows in one transaction.
- Persist invalidation records for derived revision features, score snapshots, changes, and alerts.
- Block or mark stale any query/export that would serve invalidated derivatives before rebuild.
- Enqueue rebuild and store rebuild job ids on the purge audit.
- Add tests proving data is unavailable/restricted after execution and rebuilt outputs exclude
  purged evidence.

## Findings Register

### PH13-001 - Corrected provider records cannot form an auditable supersession chain

Severity: High

Evidence:

- Source records include `supersedes_id` and `correction_type`
  (`app/models/ceri_tables.py:200-204`).
- The schema also enforces unique `(provider, dataset, provider_record_id)`
  (`app/models/ceri_tables.py:207-213`).
- `store_source_record` deduplicates only by idempotency key, which includes the content hash
  (`app/services/ceri/source_record_service.py:73-91`,
  `app/services/ceri/source_record_service.py:244-250`).
- When content changes for the same provider record id, the idempotency key changes, so the service
  attempts to insert a new row with the same provider/dataset/provider_record_id and does not set
  `supersedes_id` or `correction_type` (`app/services/ceri/source_record_service.py:113-133`).
- Tests cover exact idempotency-key dedupe and malformed quarantine, but not corrected same-id
  provider records (`tests/ceri/test_source_record_service.py:62-103`).

Impact: Corrections and late-arriving changes cannot be represented through the ingestion service as
append-only, auditable source-record revisions. This undermines `LATEST_CORRECTED`, conflict
review, purge lineage, and the Phase 13 requirement for supersession chains and correction types.

Recommendation:

- Look up existing provider/dataset/provider_record_id before insert.
- If content hash matches, dedupe exactly.
- If content hash differs, create a correction row with a distinct revision identity or relax the
  provider-record uniqueness into a current-pointer model; set `supersedes_id`, `correction_type`,
  and increment corrected counts.
- Add DB-backed tests for correction insert, query visibility, and export lineage.

### PH13-002 - Public score-history mode is validated but not applied

Severity: High

Evidence:

- Low-level `CeriPointInTimeQuery` branches on `AS_KNOWN` and `LATEST_CORRECTED`
  (`app/services/ceri/point_in_time_query.py:37-63`).
- Tests prove `LATEST_CORRECTED` can apply a later correction while `AS_KNOWN` stays on the original
  estimate (`tests/ceri/test_point_in_time_query.py:24-53`).
- Public ticker history validates that `mode` is one of the historical modes and that `as_of` exists
  (`app/services/ceri/query_service.py:154-159`,
  `app/services/ceri/query_service.py:467-475`).
- After validation, ticker history only filters stored score snapshots by `snapshot.cutoff_at <=
  filters.as_of`; it does not branch on `filters.mode`
  (`app/services/ceri/query_service.py:169-172`).
- Route tests cover missing mode/as-of, not different results between modes
  (`tests/ceri/test_ceri_query_service.py:60-78`,
  `tests/ceri/test_ceri_routes_api.py:63-77`).

Impact: API consumers can request `AS_KNOWN` or `LATEST_CORRECTED` score history and receive the same
stored-snapshot semantics. That fails the Phase 13 exit criterion that historical queries have proven
temporal semantics.

Recommendation:

- Either implement score-level reconstruction for both modes or rename/document the endpoint as
  stored score snapshots only.
- Add tests with a corrected estimate affecting a score snapshot and assert divergent
  `AS_KNOWN`/`LATEST_CORRECTED` responses.
- Include mode, cutoff, source correction policy, and evidence hash in the response metadata.

### PH13-003 - Licensed-data purge execution is audit-only

Severity: High

Evidence:

- Preview computes affected counts and derivative invalidation counts including
  `requires_rebuild` (`app/services/ceri/purge_service.py:147-201`).
- Execute validates preview scope and confirmation token, then sets `audit.status = "EXECUTED"` and
  `executed_at` (`app/services/ceri/purge_service.py:102-129`).
- Execute does not delete, tombstone, redact, quarantine, or invalidate source records, normalized
  rows, revision features, score snapshots, change events, or alerts.
- This was also flagged in Phase 4 as an unresolved lifecycle issue
  (`docs/review/phase4_database_migrations_transactions.md:185-205`).

Impact: Operators can believe licensed provider data was purged when only the audit row changed.
Restricted information can remain queryable/exportable and derivatives can remain stale, so the
Phase 13 licensed-data lifecycle criterion is not met.

Recommendation:

- Implement the chosen purge lifecycle action transactionally.
- Record invalidated derivative ids and rebuild job ids.
- Block affected exports/queries until rebuild is complete or mark outputs as invalidated.
- Add tests that prove executed purge changes data availability and rebuild state.

### PH13-004 - CERI job status exposes raw job payload/result/error without export-policy redaction

Severity: Medium

Evidence:

- Export redaction is robust in `CeriExportPolicyRegistry` and `CeriExportService`
  (`app/services/ceri/export_policy.py:51-122`,
  `app/services/ceri/export_service.py:81-112`).
- `/api/ceri/jobs/{job_id}` returns `job.payload_json`, `job.result_json`, and
  `job.error_message` directly (`app/routers/ceri_routes.py:525-559`).
- Job payloads can include `manual_path`, provider scope, purge confirmation tokens, and other
  operational metadata.

Impact: Restricted values that are safe in formal exports can still leave approved boundaries via a
status endpoint. This narrows but does not fully close the redaction attack surface.

Recommendation:

- Redact job payload/result/error through `redact_sensitive` before returning API responses.
- Avoid storing raw confirmation tokens in job payloads; store hashes or one-time server-side
  references.
- Add route-level tests for bearer tokens, paths, SQL fragments, source URLs, raw payloads, and
  confirmation tokens in job status output.

## Exit Criteria Assessment

| Exit criterion | Status | Notes |
| --- | --- | --- |
| Historical queries have proven temporal semantics | Not met | Low-level estimate PIT works, but public score-history mode is not applied. |
| Restricted information cannot leave approved service boundaries | Partial | Export policy is strong; job status output needs redaction. |
| Corrections, conflicts, and purges remain auditable | Partial | Conflicts/manual review are auditable; source-record corrections are not service-created, and purge execution is audit-only. |

## Verification

- `uv run pytest tests\ceri -q` -> `135 passed, 1 warning in 13.94s`.
