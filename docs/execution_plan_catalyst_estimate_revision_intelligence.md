# SwingLens Execution Plan: Catalyst and Estimate-Revision Intelligence

**Document version:** 1.1
**Status:** Reviewed and implementation-ready plan
**Last updated:** 2026-08-01
**Feature:** Catalyst and Estimate-Revision Intelligence (CERI)

Source documents:

- `C:\Users\Ivica\Downloads\SwingLens_Catalyst_and_Estimate_Revision_Intelligence_SRS.docx`
- `C:\Users\Ivica\Downloads\SwingLens_Catalyst_and_Estimate_Revision_Intelligence_SDD.docx`

## Version 1.1 Coverage Review

This revision closes the gaps found during a requirement-by-requirement comparison with the
SRS and SDD. In particular, it adds:

- an explicit effective-session resolver and daily cutoff contract,
- provider-priority conflict resolution while preserving all observations,
- estimate-observation deduplication and verified currency-conversion lineage,
- revision-confidence persistence and analyst breadth counts,
- immutable catalyst event revisions and scheduled-event outcome handling,
- persisted alert rules/events with acknowledgement and deduplication,
- processing-run lineage, complete durable job types, execution fencing, and backfill
  checkpoints,
- revision APIs, event revision/history APIs, operational quarantine/conflict/stale views,
  and explicit backfill/reprocess scopes,
- current-view and full-evidence exports with field-level licensing controls,
- run-deletion retention rules and audited provider-license purge workflows,
- score reproduction, top positive/negative contributors, alignment flags, and an immutable
  point-in-time feature bridge for Outcome Engine calibration,
- exact observability context, localhost/CSRF controls, accessibility checks, and additional
  acceptance gates.

This plan implements CERI as an additive, point-in-time evidence layer for SwingLens.
CERI ingests structured analyst estimates, estimate history, earnings actuals, guidance,
and company catalysts; preserves raw source provenance; derives revision, surprise,
catalyst, opportunity, risk, and confidence features; exposes those results through APIs,
filters, dashboards, ticker detail pages, exports, and change feeds; and remains strictly a
research and filtering feature.

## Current Repo Baseline

- SwingLens is a local FastAPI/Jinja2/HTMX/PostgreSQL application.
- SQLAlchemy models currently live in `app/models/tables.py`; Alembic imports those models
  through `alembic/env.py`.
- Services follow feature folders for larger subsystems, for example
  `app/services/winner_probability/` and `app/services/setup_lifecycle/`.
- Durable background jobs already exist through `app/services/background_job_service.py`,
  `app/services/background_worker.py`, and `app/worker.py`.
- The full pipeline currently contains the base scoring steps, optional Setup Lifecycle
  steps, and the Winner Probability capture step.
- `app/services/us_market_calendar.py` already provides the exchange-aware daily session
  basis needed for CERI effective-session work.
- Existing SwingLens source entities relevant to CERI include `UploadRun`, `RawCompanyRow`,
  `FundamentalScore`, `TechnicalScore`, `CombinedResult`, `RankingResult`, `PriceBar`,
  `MarketRegimeSnapshot`, `SectorRotationSnapshot`, `BackgroundJob`, and `PipelineRun`.
- There are no current `ceri` symbols in `app/`, `tests/`, `docs/`, `config/`, or
  `alembic/`, so the feature can be introduced cleanly behind flags.

## V1 Product Decisions

Use these decisions to keep implementation coherent:

1. Build v1 for US equities, local research, daily completed-session analysis, and
   provider-neutral structured data.
2. Keep provider ingestion outside the ordinary full pipeline by default. Provider jobs
   update the shared point-in-time evidence store; run-scoped pipeline capture reads that
   evidence by cutoff.
3. Add a CERI run-capture step after `SECTOR_ROTATION_SNAPSHOT`. When Setup Lifecycle is
   enabled, CERI capture should run before SLSE evaluation so SLSE can later consume
   catalyst/revision change context without changing its structural lifecycle state.
4. Keep CERI advisory in v1. Do not mutate `CombinedResult`, `RankingResult`, technical
   scores, fundamental scores, or broker/order behavior.
5. Implement a manual CSV/JSON provider first. Treat commercial providers as Phase 10
   productionization after the provider-neutral domain, fixture tests, licensing controls,
   and operations views are stable.
6. Persist immutable source records before normalized records, derived features, scores, or
   alerts.
7. Store `published_at`, `observed_at`, `ingested_at`, `effective_at`, `effective_session`,
   `as_of_session`, and `cutoff_at` explicitly. Do not use ingestion time as proof that
   information was public.
8. Preserve missing values as null plus warnings. Missing analyst count, high/low values,
   timestamps, stale datasets, or sparse coverage lower confidence and may produce
   `Unrated`; they never become zero or a neutral score by default.
9. Keep opportunity and risk independent. A high opportunity score can coexist with high
   event risk, and both must remain visible and independently filterable.
10. Store config version, config hash, calculation version, source IDs, feature IDs, and
    evidence hash with every score snapshot.
11. Use rules-first scoring for v1. Outcome Engine calibration may later tune thresholds or
    ranking integration, but v1 scores must be deterministic and explainable.
12. Use conservative catalyst deduplication. False duplicates are easier to review than
    incorrectly merged material events.
13. Corrections and manual reviews create superseding records or audited normalized
    revisions. They do not silently rewrite raw source history.
14. Maintain two historical modes: `AS_KNOWN` for prediction/backtest integrity and
    `LATEST_CORRECTED` for data-quality review.
15. Keep all feature flags disabled by default until acceptance fixtures, migration checks,
    and focused CERI tests pass.
16. Resolve announcement timestamps through one New-York-time effective-session service. Daily
    snapshots include only evidence eligible at their explicit cutoff; after-hours evidence becomes
    eligible on the next completed US session.
17. Use configured provider priority to select an operational normalized value when sources conflict,
    while retaining every source observation, conflict reason, and review decision.
18. Represent catalyst clusters separately from immutable event revisions. Status, direction,
    materiality, dates, and reviewed overrides are versioned rather than silently overwritten.
19. Persist alert rules and alert events as first-class audit records; alerts never replace source
    change events and may be disabled independently.
20. Retain source evidence, score snapshots, and downstream evidence after an upload-run deletion.
    Provider-license deletion is an explicit preview-first administrative purge with an audit record.

## Phase 0: Preparation, Guard Rails, and Baseline

Goal: lock semantics, add disabled settings, document rollout gates, and capture the current
project baseline before schema work.

Primary files:

- `docs/execution_plan_catalyst_estimate_revision_intelligence.md`
- `app/settings.py`
- `.env.example`
- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `tests/test_settings.py`
- `tests/test_pipeline_service.py`
- `tests/test_pipeline_executor.py`

Tasks:

1. Create a branch, for example `codex/catalyst-estimate-revision-intelligence`.
2. Capture baseline checks:
   ```powershell
   ruff check app tests
   pytest -q
   alembic heads
   alembic current
   ```
3. Resolve the actual Alembic head immediately before writing the migration.
4. Add disabled settings:
   - `CERI_ENABLED=false`
   - `CERI_PROVIDER_INGEST_ENABLED=false`
   - `CERI_RUN_CAPTURE_ENABLED=false`
   - `CERI_UI_ENABLED=false`
   - `CERI_ALERTS_ENABLED=false`
   - `CERI_ADMIN_ENABLED=false`
   - `CERI_BACKFILL_ENABLED=false`
   - `CERI_CONFIG_PATH=config/ceri.yaml`
   - `CERI_TAXONOMY_PATH=config/ceri_catalyst_taxonomy.yaml`
5. Define CERI pipeline constants:
   - `CERI_CAPTURE_SNAPSHOT`
   - optionally `CERI_CHANGE_DETECTION` if change detection is split from capture.
6. Update pipeline-step construction so the effective order is:
   - base scoring through `SECTOR_ROTATION_SNAPSHOT`,
   - CERI capture when enabled,
   - optional Setup Lifecycle steps when enabled,
   - `CAPTURING_WINNER_PREDICTIONS`.
7. Ensure CERI failures can mark the pipeline `PARTIAL` without corrupting existing run outputs.
8. Define stable API error codes for invalid filters, bad date ranges, missing tickers/runs,
   unavailable provider capability, invalid configuration, review conflicts, duplicate active
   backfills, license-restricted fields, purge conflicts, and unauthorized local-admin writes.
9. Record the research-only safety boundary in docs and route copy: CERI never places, modifies,
   stages, or cancels orders.
10. Confirm the application still binds to `127.0.0.1` by default and that provider ingestion,
    review, reprocess, backfill, cancellation, and purge routes use the existing local-admin and
    CSRF protections.
11. Lock durable-job semantics before feature jobs are enabled:
    - deterministic request keys and duplicate-job coalescing,
    - lease owner, execution token, heartbeat renewal, and fencing on final writes,
    - bounded transactions and cancellation checks between pages/companies/batches,
    - checkpoint persistence for provider pages and company/session ranges.
12. Lock deletion and retention semantics:
    - run-scoped foreign keys use `ON DELETE SET NULL` or equivalent non-cascading behavior,
    - immutable provider evidence and derived snapshots remain available after run deletion,
    - licensed-data purge requires preview, explicit confirmation, reason, actor, and audit record.
13. Lock source-conflict semantics: configured provider hierarchy chooses the operational value;
    all source values, conflict details, and review decisions remain queryable.
14. Document the exact daily cutoff and effective-session policy in New York time before any
    point-in-time fixtures or scoring code are written.

Tests:

- Settings load with defaults disabled.
- Pipeline step names are deterministic across CERI, SLSE, and OWPE flag combinations.
- Existing pipeline behavior is unchanged with CERI disabled.
- CERI partial failure does not delete or mutate existing SwingLens outputs.
- Safety tests prove no CERI code imports or calls an order-placement client.
- Local bind, CSRF/local-admin boundaries, job fencing, and non-cascading retention decisions are tested.

Exit criteria:

- CERI can be toggled at the settings and pipeline-shell level with no visible behavior
  change while disabled.

## Phase 1: Configuration, Taxonomy, Enums, and DTOs

Goal: define validated CERI behavior before database writes.

Primary files:

- `config/ceri.yaml`
- `config/ceri_catalyst_taxonomy.yaml`
- `app/services/ceri/config.py`
- `app/services/ceri/dtos.py`
- `app/services/ceri/enums.py`
- `tests/ceri/test_config.py`
- `tests/ceri/test_taxonomy.py`

Tasks:

1. Add default CERI configuration with:
   - calculation version, config version, provider priority, dataset policies,
   - revision windows `[7, 30, 90]`,
   - metrics for EPS diluted and revenue, with optional EBITDA, margin, FCF,
   - supported period types for current quarter, next quarter, current fiscal year, and
     next fiscal year,
   - near-zero threshold,
   - minimum analyst count,
   - minimum component coverage percentage,
   - freshness limits by dataset,
   - opportunity weights,
   - event-risk penalties,
   - confidence weights and labels,
   - change thresholds,
   - export licensing controls,
   - provider terms/version and retention metadata,
   - provider priority and conflict-resolution hierarchy,
   - daily cutoff and effective-session rules in `America/New_York`,
   - verified currency-conversion policy and conversion-source requirements,
   - backfill/reprocess batch sizes, checkpoint policy, and concurrency limits,
   - alert rule definitions, cooldowns, severities, acknowledgement behavior, and dedup scope,
   - score posture and alignment-flag rules,
   - field-level exportability/restriction tags and purge policy.
2. Add the initial catalyst taxonomy:
   - `EARNINGS`
   - `GUIDANCE`
   - `PRODUCT`
   - `CONTRACT`
   - `REGULATORY`
   - `LEGAL`
   - `CAPITAL_ALLOCATION`
   - `FINANCING`
   - `INSIDER`
   - `INDEX`
   - `ANALYST_ACTION`
   - `CORPORATE_ACTION` / corporate transaction
3. Define enums/constants for metrics, period types, datasets, providers, confidence
   labels, guidance actions, event categories, event statuses, event directions, date
   confidence, historical view mode, processing status, and change type.
4. Implement strict config loading and normalized hash calculation.
5. Validate:
   - weight totals and score ranges,
   - taxonomy references,
   - provider capability references,
   - threshold ordering,
   - freshness limits,
   - disabled datasets/categories,
   - exportable versus restricted field policies,
   - provider hierarchy cycles and missing default source policy,
   - trading-session cutoff rules and timezone validity,
   - event-status transitions and alert references,
   - retention/purge policy and provider terms metadata.
6. Define DTOs for provider requests/responses, normalized records, revision features,
   score components, warnings, filters, export rows, and API payloads.

Tests:

- Valid default config and taxonomy load.
- Config hash is stable for semantically identical input.
- Invalid weights, unknown taxonomy categories, impossible thresholds, and unknown provider
  capabilities fail with actionable errors.
- Missing-value policies reject zero-as-null ambiguity.

Exit criteria:

- CERI behavior is configuration-driven, versioned, hashable, and test-covered without
  database migrations.

## Phase 2: Persistence Model and Migration

Goal: create append-only storage for identity, raw evidence, normalized facts, derived
features, snapshots, changes, and audited reviews.

Primary files:

- `app/models/ceri_tables.py`
- `app/models/__init__.py`
- `alembic/env.py`
- `alembic/versions/<next_revision>_add_ceri_tables.py`
- `tests/ceri/test_schema.py`

Tasks:

1. Add a separate `app/models/ceri_tables.py` model module and import it from Alembic so
   the metadata is registered. If the team prefers the existing monolithic model style,
   keep CERI models in `app/models/tables.py` for the first schema pass and split later.
2. Add tables from the SDD:
   - `ceri_companies`
   - `ceri_company_aliases`
   - `ceri_ingestion_runs`
   - `ceri_source_records`
   - `ceri_estimate_snapshots`
   - `ceri_earnings_actuals`
   - `ceri_guidance_events`
   - `ceri_catalyst_events`
   - `ceri_catalyst_sources`
   - `ceri_revision_features`
   - `ceri_score_snapshots`
   - `ceri_change_events`
   - `ceri_manual_reviews`
   - `ceri_processing_runs`
   - `ceri_catalyst_event_revisions`
   - `ceri_alert_rules`
   - `ceri_alert_events`
   - `ceri_purge_audits`
3. Define identity and operational audit fields:
   - `ceri_company_aliases` stores provider ID/CIK/ticker alias, exchange, valid-from, valid-to,
     source, and confidence,
   - `ceri_processing_runs` stores job type, deterministic request key, scope, config version,
     cutoff, status, counts, retries, duration, checkpoint, actor, and errors,
   - `ceri_alert_rules` stores enabled state, severity, thresholds, scope, cooldown, config version,
     and source event types,
   - `ceri_alert_events` stores source change/event revision, stable event key, severity, status,
     created/acknowledged/dismissed timestamps, and evidence,
   - `ceri_purge_audits` stores preview manifest hash, provider/license scope, actor, reason,
     confirmation token hash, counts, invalidated derivatives, and completion state.
4. Use `BigInteger` primary keys, timezone-aware timestamps, `JSONB` for raw payloads and
   structured evidence, explicit check constraints, and clear foreign keys to existing
   `upload_runs` where run-scoped snapshots are needed.
5. Store source-record fields:
   - provider, provider terms/version, dataset, provider record ID, company hints,
   - `published_at`, `observed_at`, `ingested_at`,
   - source URL/reference when permitted,
   - raw JSON or restricted normalized payload,
   - content hash and deterministic idempotency key,
   - export-policy classification and provider retention deadline when applicable,
   - supersession/correction relationship.
6. Store estimate snapshot fields:
   - metric, fiscal period end, period type, fiscal year/quarter,
   - consensus, high, low, analyst count,
   - provider-supplied upward/downward revision counts when available,
   - source currency/scale and optional verified canonical currency/scale,
   - conversion rate, conversion source record, and conversion effective timestamp when used,
   - `effective_at`, `effective_session`,
   - canonical-observation key, quality flags, and original provider fields.
7. Store earnings-actual fields including report timestamp/session, actual value, metric and
   period, `consensus_snapshot_id` selected immediately before the report, surprise values,
   source ID, and quality warnings.
8. Store guidance fields including action, metric, period, low/high/range, comparison basis,
   source record, confidence, effective timestamp/session, and revision/supersession links.
9. Store catalyst clusters separately from append-only catalyst event revisions. Revisions hold
   dates, status (`SCHEDULED`, `ANNOUNCED`, `COMPLETED`, `DELAYED`, `CANCELLED`,
   `OUTCOME_KNOWN`), direction, materiality, date/source confidence, policy-selected operational
   values, conflict flags, and optional prior/outcome revision links.
10. Store revision-feature fields including baseline/current snapshot IDs, actual elapsed days,
   absolute and percentage revision, upward/downward counts, net breadth, dispersion, acceleration,
   revision-confidence score/label, warnings, and config/calculation lineage.
11. Store score snapshot fields:
   - run ID, company ID, ticker, as-of session, cutoff,
   - opportunity score, event risk score, data confidence, coverage percentage, posture,
   - earnings proximity risk separately from revision/surprise quality,
   - alignment flags for fundamentals, technicals, sector, regime, lifecycle, and earnings clearance,
   - top positive and negative contributors,
   - component JSON, reasons, warnings,
   - config version/hash, calculation version, evidence hash.
12. Add uniqueness and indexes for:
   - provider/dataset/provider record identity,
   - source content hash,
   - company alias validity,
   - estimate key plus source/effective timestamp,
   - catalyst event cluster and source links,
   - revision feature identity by company/metric/period/window/as-of session/config,
   - one score snapshot per run/company/config/calculation version,
   - change-event and alert-event dedup keys,
   - active event-revision and manual-review supersession chains,
   - processing request key/status/checkpoint queries,
   - quarantine/conflict/stale operations filters,
   - purge preview manifest and provider/license scope.
13. Add deletion/retention rules:
   - source evidence, normalized facts, event revisions, revision features, and score snapshots never cascade-delete with `UploadRun`,
   - retain original run/ticker/date lineage when the FK becomes null,
   - purge only through audited provider-license operations; never through ordinary run deletion.
14. Add migration sequence:
   - M1 identity, ingestion-run, and source-record tables,
   - M2 estimate, earnings, guidance, and catalyst tables,
   - M3 revision-feature, score-snapshot, change-event, and manual-review tables,
   - M4 UploadRun relationships and pipeline status fields if needed,
   - M5 canonical identity backfill from existing tickers/raw rows,
   - M6 manual fixture seed path for golden tests.

Tests:

- SQLAlchemy metadata includes all tables, constraints, relationships, and indexes.
- Alembic upgrade, downgrade, and upgrade works against the project database path.
- Uniqueness constraints prevent duplicate source records, duplicate score snapshots, and
  duplicate change events under retry.
- Supersession preserves original records.
- Nullable provider fields remain nullable and are not coerced to zero.
- Upload-run deletion does not erase CERI evidence or make lineage ambiguous.
- Event revisions, alert events, processing runs, and purge audits preserve append-only history.

Exit criteria:

- The database can represent immutable evidence, normalized facts, derived features,
  point-in-time snapshots, audited corrections, and operational lineage.

## Phase 3: Provider Protocol, Manual Provider, and Ingestion Audit

Goal: create the provider-neutral ingestion surface and a first-class manual CSV/JSON
provider for development, fixtures, recovery, and historical import.

Primary files:

- `app/services/ceri/provider_protocol.py`
- `app/services/ceri/provider_registry.py`
- `app/services/ceri/providers/manual_provider.py`
- `app/services/ceri/source_record_service.py`
- `app/services/ceri/orchestration.py`
- `app/services/ceri/job_handlers.py`
- `tests/ceri/test_provider_protocol.py`
- `tests/ceri/test_manual_provider.py`
- `tests/ceri/test_source_record_service.py`

Tasks:

1. Define `CeriProvider` with:
   - `capabilities()`
   - `health()`
   - `resolve_company()`
   - `fetch_estimate_snapshots()`
   - `fetch_earnings_actuals()`
   - `fetch_guidance()`
   - `fetch_catalysts()`
2. Implement provider capability discovery and dataset-specific freshness, payload-storage,
   retention, export, and correction policies.
3. Implement manual CSV/JSON import for:
   - company identity aliases and validity dates,
   - estimate snapshots including optional upward/downward counts,
   - earnings actuals and consensus references,
   - guidance,
   - catalyst events and later status/outcome revisions.
4. Create `ceri_ingestion_runs` with provider, provider terms/version, dataset, scope,
   deterministic request key, status, requested/fetched/inserted/deduplicated/corrected/
   quarantined/failed counts, warnings, retries, quota metadata, started/completed timestamps,
   duration, checkpoint, and terminal status.
5. Hash and store immutable source records with deterministic idempotency keys and permitted
   payload fields only.
6. Redact provider secrets and restricted raw content in logs and errors.
7. Support partial provider capability. Missing datasets lower confidence later; they do not
   disable all CERI processing.
8. Queue downstream normalization/rebuild jobs only for affected companies and sessions.
9. Register `CERI_PROVIDER_INGEST` and `CERI_NORMALIZE` handlers with durable-job request-key
   coalescing, heartbeat renewal, execution-token fencing, page/company checkpoints, bounded
   commits, cancellation checks, and partial completion.
10. Persist a domain-level `ceri_processing_run` for normalization and downstream rebuild
    lineage; `BackgroundJob` remains the executor rather than the sole audit record.
11. Enforce provider retention and payload-storage policy before persistence. An adapter may
    store normalized permitted fields without the full payload when licensing requires it.
12. Reject or quarantine malformed records without losing safe failure metadata, provider page,
    source identity, or retry context.

Tests:

- Manual fixtures ingest deterministically.
- Duplicate imports are idempotent.
- Provider health and capability APIs report partial support.
- Malformed rows are retained as safe failure metadata and excluded from scoring.
- Provider failure preserves latest valid evidence.
- A stale worker cannot commit after lease ownership changes, and checkpointed retry resumes without duplication.
- Ingestion-run counts, retries, duration, quota, and terminal status are reproducible.

Exit criteria:

- Structured evidence can enter the system through the same provider protocol that later
  commercial adapters will use.

## Phase 4: Identity Resolution, Provenance, and Normalization

Goal: map provider records to canonical securities, preserve provenance, and normalize
domain facts without scoring yet.

Primary files:

- `app/services/ceri/identity_resolver.py`
- `app/services/ceri/fiscal_period_normalizer.py`
- `app/services/ceri/estimate_normalizer.py`
- `app/services/ceri/earnings_normalizer.py`
- `app/services/ceri/guidance_normalizer.py`
- `app/services/ceri/catalyst_taxonomy.py`
- `app/services/ceri/catalyst_deduplicator.py`
- `app/services/ceri/effective_session_service.py`
- `app/services/ceri/estimate_deduplicator.py`
- `app/services/ceri/provider_conflict_service.py`
- `app/services/ceri/currency_conversion_service.py`
- `tests/ceri/test_identity_resolver.py`
- `tests/ceri/test_fiscal_period_normalizer.py`
- `tests/ceri/test_estimate_normalizer.py`
- `tests/ceri/test_catalyst_taxonomy.py`
- `tests/ceri/test_catalyst_deduplicator.py`
- `tests/ceri/test_effective_session_service.py`
- `tests/ceri/test_estimate_deduplicator.py`
- `tests/ceri/test_provider_conflict_service.py`

Tasks:

1. Resolve every incoming record by ticker, exchange, provider identifier, CIK, and configured
   aliases. Alias records include validity dates so historical tickers do not map to the wrong
   security.
2. Quarantine ambiguous or unresolved records and expose them to operations. They must not
   affect scores.
3. Normalize fiscal periods while retaining original provider labels.
4. Implement one effective-session resolver using `America/New_York` and the US market calendar:
   - pre-market announcements become eligible in the same session,
   - regular-session announcements are timestamp-aware and must precede the score cutoff,
   - after-hours announcements become eligible on the next trading session,
   - weekend/holiday announcements become eligible on the next trading session,
   - missing timestamps use a source date with low date confidence and an explicit warning.
5. Normalize metric, units, source currency/scale, consensus, high/low, analyst count,
   upward/downward counts, effective timestamps, and effective sessions.
6. Convert currency/scale only when a verified conversion basis with source and effective
   timestamp exists. Otherwise preserve the source value, mark canonical conversion unavailable,
   and prevent cross-currency comparison.
7. Deduplicate exact and near-identical estimate observations using provider record keys,
   canonical estimate key, eligible timestamp bucket, content hash, and configured tolerances.
   Preserve all source links and identify one canonical operational observation.
8. Resolve source conflicts using configured provider priority plus freshness/quality rules.
   Persist the selected operational value, all competing observations, conflict type, and
   resolution reason.
9. Normalize guidance actions as raised, initiated, maintained, narrowed, widened, lowered,
   withdrawn, or unknown. Retain metric, period, range, comparison basis, source, confidence,
   effective timestamp/session, and supersession lineage.
10. Normalize catalyst category, subtype, subject key, announced timestamp, expected date,
    effective session, status, direction, materiality, confidence, and date confidence.
11. Deduplicate catalyst records conservatively using canonical company, category, subtype,
    normalized subject, event-date bucket, compatible timestamps, source similarity, and
    mutually exclusive outcome checks.
12. Preserve all source-specific fields and source links behind each canonical catalyst cluster.
13. Implement manual reviewed overrides as new audited review records that create a new normalized
    event revision, not raw evidence edits. Store reviewer, reason, old/new value, effective time,
    and supersession link.
14. Validate catalyst status transitions and store scheduled-event outcomes as later event
    revisions or linked outcome revisions; never overwrite the scheduled record.

Tests:

- Identity ambiguity quarantines records.
- Non-calendar fiscal years normalize correctly.
- Currency/scale uncertainty prevents comparison and raises a warning.
- Provider zero is distinguishable from missing.
- Two equivalent contract-award records become one event with two source references.
- Conflicting event dates remain visible and lower confidence.
- Manual overrides retain old value, new value, reviewer, timestamp, reason, and revision lineage.
- Pre-market, regular-session, after-hours, weekend, holiday, and missing-time events receive the documented effective session and confidence.
- Provider-priority selection is deterministic while all conflicting values remain visible.
- Historical aliases respect validity dates and verified currency conversion is fully traceable.

Exit criteria:

- CERI has source-linked normalized estimates, earnings, guidance, and catalysts with no
  derived scoring side effects.

## Phase 5: Point-in-Time Queries and Revision Features

Goal: calculate estimate-revision evidence with explicit baselines, cutoffs, confidence,
and leakage protection.

Primary files:

- `app/services/ceri/point_in_time_query.py`
- `app/services/ceri/revision_feature_service.py`
- `tests/ceri/test_point_in_time_query.py`
- `tests/ceri/test_revision_feature_service.py`
- `tests/ceri/test_leakage.py`

Tasks:

1. Implement `AS_KNOWN` and `LATEST_CORRECTED` query modes.
2. Return only evidence with `effective_at <= cutoff_at` in `AS_KNOWN` mode.
3. Apply later corrections only in `LATEST_CORRECTED` mode.
4. Define canonical estimate key:
   - company ID,
   - metric,
   - period type,
   - fiscal period end,
   - verified currency basis,
   - scale.
5. Select baselines:
   - current snapshot at or before cutoff,
   - target baseline date equal to cutoff date minus window,
   - latest eligible snapshot at or before the target baseline date,
   - optional configured tolerance with actual elapsed days recorded,
   - reject different canonical estimate keys.
6. Calculate 7-, 30-, and 90-day absolute and percentage revisions using the safe near-zero
   and sign-change rule.
7. Calculate analyst breadth from stored upward/downward counts and expose both counts plus the
   normalized breadth value.
8. Calculate acceleration using actual elapsed days by comparing the recent revision rate with
   the longer-window revision rate, recording every baseline and elapsed-day input.
9. Calculate normalized dispersion only when consensus is safely away from zero; flag unusually
   high dispersion and preserve the unavailable reason otherwise.
10. Calculate and persist revision-confidence score/label from analyst sample, freshness,
    provider quality, metric/period coverage, timestamp quality, and unresolved conflicts.
11. Persist baseline/current snapshot IDs, source observation IDs, provider-selection reason,
    actual elapsed days, config hash, and calculation version for every revision value.
12. Preserve unavailable revisions with reasons instead of zero.
13. Aggregate current-quarter, next-quarter, current-year, and next-year revision strength with
    configurable weights and coverage rules.
14. Build an immutable evidence hash for each derived revision feature and provide a reproduction
    method that recomputes it from the stored lineage.

Tests:

- Comparable EPS snapshots calculate expected absolute and percentage revision.
- Missing 30-day baseline returns unavailable plus reason, not zero.
- Near-zero or sign-changing baseline uses absolute change plus warning.
- Baseline selection works across weekends, holidays, and missing observations.
- Later source records and later corrections cannot leak into earlier `AS_KNOWN` queries.
- Dispersion is unavailable for near-zero consensus.
- Breadth shows the original upward/downward counts and normalized value.
- Acceleration uses actual elapsed days and stored source snapshots.
- Revision confidence and evidence reproduction are deterministic.

Exit criteria:

- Revision features are reproducible from stored source IDs and cutoff semantics.

## Phase 6: Earnings, Guidance, Catalyst Features, and Scoring

Goal: calculate surprise, guidance, catalyst, opportunity, risk, and confidence outputs.

Primary files:

- `app/services/ceri/surprise_feature_service.py`
- `app/services/ceri/catalyst_feature_service.py`
- `app/services/ceri/opportunity_score_service.py`
- `app/services/ceri/event_risk_service.py`
- `app/services/ceri/confidence_service.py`
- `app/services/ceri/snapshot_service.py`
- `tests/ceri/test_surprise_feature_service.py`
- `tests/ceri/test_catalyst_feature_service.py`
- `tests/ceri/test_scoring.py`
- `tests/ceri/test_confidence_service.py`

Tasks:

1. Ingest and normalize earnings actuals with the exact point-in-time consensus snapshot selected
   immediately before the report timestamp. Persist `consensus_snapshot_id`, report timestamp,
   effective session, source ID, and selection reason.
2. Calculate EPS and revenue surprise magnitude for at least the last four reported periods when
   available.
3. Summarize surprise consistency, direction, and post-report price response when OHLCV data is
   available.
4. Calculate guidance direction and confidence by metric and period while retaining action, range,
   comparison basis, source, effective session, and revision lineage.
5. Calculate catalyst materiality, direction, source confidence, date confidence, scheduled binary
   risk, status/outcome state, and conflict penalties from the selected event revision.
6. Implement opportunity score from configured components:
   - revision magnitude,
   - revision breadth,
   - revision acceleration,
   - earnings surprise,
   - guidance direction,
   - catalyst materiality,
   - price-response quality,
   - conflict penalties.
7. Implement event risk independently from opportunity:
   - earnings proximity,
   - regulatory binary risk,
   - legal binary risk,
   - financing gap risk,
   - corporate action risk,
   - date uncertainty,
   - conflict/staleness penalties.
8. Implement confidence from source quality, freshness, estimate coverage, analyst sample,
   timestamp quality, and conflict-free score.
9. Keep earnings proximity as a separately exposed risk window (`days_until_earnings`, risk level,
   blocked/high/medium/clear/unknown) and never present it as revision or surprise quality.
10. Return `High`, `Normal`, `Low`, or `Insufficient` labels based on configured thresholds.
11. Derive the configured posture (`Positive`, `Improving`, `Mixed`, `Deteriorating`,
    `Binary Risk`, or `Unrated`) without hiding numeric opportunity/risk diagnostics.
12. Derive additive alignment flags against fundamental quality, technical setup, sector state,
    market regime, lifecycle actionability, and earnings clearance. These flags never mutate ranks.
13. Persist immutable score snapshots with components, weights, penalties, caps, reasons, warnings,
    coverage, posture, alignment flags, top three positive and negative contributors, config hash,
    calculation version, cutoff, source IDs, and evidence hash.
14. Implement score reproduction from stored feature values, event/revision IDs, configuration hash,
    calculation version, and evidence hash, returning stored-versus-reproduced differences.

Tests:

- Earnings surprise uses consensus available immediately before the report.
- High staleness prevents `High` confidence.
- Sparse analyst coverage lowers confidence or produces `Unrated`.
- Positive opportunity and high risk are simultaneously visible.
- Changing weights creates a new config hash and new snapshots without changing old ones.
- Score reproduction succeeds from stored features, source IDs, config hash, and calculation
  version.

Exit criteria:

- The core CERI outputs can be calculated deterministically and explained per ticker.

## Phase 7: Change Detection, Alerts, Pipeline Capture, and Exports

Goal: compare snapshots, emit meaningful changes, integrate with run scope, and export
evidence without creating noisy duplicates.

Primary files:

- `app/services/ceri/change_detection_service.py`
- `app/services/ceri/alert_service.py`
- `app/services/ceri/backfill_service.py`
- `app/services/ceri/processing_run_service.py`
- `app/services/ceri/job_handlers.py`
- `app/services/ceri/outcome_feature_export.py`
- `app/services/ceri/export_service.py`
- `app/services/ceri/orchestration.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `tests/ceri/test_change_detection_service.py`
- `tests/ceri/test_export_service.py`
- `tests/ceri/test_orchestration.py`
- `tests/test_pipeline_service.py`
- `tests/test_pipeline_executor.py`

Tasks:

1. Capture one CERI score snapshot per run/ticker/config/calculation version using the run cutoff
   and `AS_KNOWN` evidence only.
2. Compare the latest completed CERI snapshot and event revision with the prior completed snapshot
   for the ticker and emit stable material changes:
   - `REVISION_UP`, `REVISION_DOWN`, `REVISION_ACCELERATED`, `REVISION_DECELERATED`,
   - `GUIDANCE_RAISED`, `GUIDANCE_LOWERED`, `GUIDANCE_WITHDRAWN`,
   - `NEW_CATALYST`, `CATALYST_UPDATED`, `CATALYST_CONFIRMED`, `CATALYST_DELAYED`,
     `CATALYST_CANCELLED`, `CATALYST_RESOLVED`, `NEW_BINARY_EVENT`,
   - `OPPORTUNITY_UPGRADED`, `OPPORTUNITY_DOWNGRADED`,
   - `RISK_ESCALATED`, `RISK_DEESCALATED`,
   - `DATA_STALE`, `DATA_REFRESHED`, `CONFLICT_OPENED`, `CONFLICT_RESOLVED`.
3. Generate stable change dedup keys from company, source snapshots/event revisions, change type,
   effective session, config/calculation version, and evaluation scope.
4. Persist configurable in-app alert rules and generated alert events when
   `CERI_ALERTS_ENABLED` is true. Store source change/event revision, severity, stable event key,
   cooldown decision, status (`UNREAD`, `ACKNOWLEDGED`, `DISMISSED`), and evidence.
5. Prevent duplicate alerts for the same event revision under idempotent reruns; acknowledgement
   or dismissal must not mutate the source change/event.
6. Implement export modes:
   - `CURRENT_VIEW`: filtered run/ticker rows exactly matching the current query,
   - `FULL_EVIDENCE`: normalized estimate revisions, guidance, catalysts/event revisions,
     source IDs, score components, cutoff, versions, warnings, and provenance.
7. Ensure exports honor field-level licensing configuration by omitting/replacing restricted raw
   content, provider URLs, and non-redistributable fields.
8. Register complete durable job types:
   - `CERI_PROVIDER_INGEST`, `CERI_NORMALIZE`, `CERI_REBUILD_FEATURES`,
   - `CERI_CAPTURE_RUN`, `CERI_CHANGE_DETECTION`, `CERI_BACKFILL`,
   - `CERI_ALERT_REBUILD`, `CERI_PURGE_LICENSED_DATA`.
9. Create `ceri_processing_runs` for normalization, rebuild, capture, change detection, backfill,
   alert rebuild, and purge. Store deterministic request key, scope, config version, status, counts,
   warnings, failures, retries, duration, checkpoints, actor, and source cutoffs.
10. Implement idempotent backfill/recalculation by ticker, date range, provider, dataset,
    historical view mode, and configuration version. Resume from provider-page or
    company/session checkpoints and never change earlier `AS_KNOWN` evidence.
11. Expose an immutable CERI point-in-time feature export for Outcome Engine calibration containing
    revision, surprise, catalyst, risk, confidence, posture, alignment, source cutoff, and source
    IDs. It must not join mutable latest-state data or require ranking mutation.
12. Integrate CERI output counters into pipeline result JSON:
    - snapshots captured,
    - rated/unrated,
    - changes,
    - alerts,
    - quarantined/stale/conflicted counts,
    - failures/skips.

Tests:

- Idempotent reruns create no duplicate score snapshots, change events, or alerts.
- Daily change feed emits one alert for a catalyst revision.
- Partial provider failure preserves latest eligible data.
- CSV and JSON exports include required audit fields and omit restricted fields.
- Pipeline remains `COMPLETED` or `PARTIAL` according to existing status rules.
- Alert events are persisted, deduplicated, and user-state changes do not alter source evidence.
- Backfill/recalculation resumes from checkpoints and is idempotent for every supported scope.
- Outcome Engine feature export is point-in-time safe and reproducible.

Exit criteria:

- CERI can run inside the full pipeline as a deterministic, nonfatal research step.

## Phase 8: Query APIs and Operations Backend

Goal: expose stable JSON APIs for dashboards, ticker pages, provider health, operations,
recalculation, review, and exports.

Primary files:

- `app/services/ceri/query_service.py`
- `app/routers/ceri_routes.py`
- `app/routers/ceri_provider_routes.py`
- `app/main.py`
- `tests/ceri/test_query_service.py`
- `tests/ceri/test_routes_api.py`
- `tests/ceri/test_routes_admin.py`

Tasks:

1. Implement query routes:
   - `GET /api/ceri/latest`
   - `GET /api/ceri/run/{run_id}`
   - `GET /api/ceri/ticker/{ticker}`
   - `GET /api/ceri/ticker/{ticker}/history`
   - `GET /api/ceri/changes`
   - `GET /api/ceri/events`
   - `GET /api/ceri/events/{event_id}`
   - `GET /api/ceri/events/{event_id}/revisions`
   - `GET /api/ceri/revisions`
   - `GET /api/ceri/revisions/{revision_id}`
   - `GET /api/ceri/alerts`
   - `GET /api/ceri/providers/health`
   - `GET /api/ceri/operations/quarantine`
   - `GET /api/ceri/operations/conflicts`
   - `GET /api/ceri/operations/stale`
   - `GET /ceri/export.csv`
   - `GET /ceri/export.json`
2. Implement write/admin routes:
   - `POST /api/ceri/ingestion-runs`
   - `POST /api/ceri/recalculate`
   - `POST /api/ceri/events/{id}/review`
   - `POST /api/ceri/jobs/{id}/cancel`
   - `POST /api/ceri/backfills`
   - `POST /api/ceri/reprocess`
   - `POST /api/ceri/alerts/{id}/acknowledge`
   - `POST /api/ceri/alerts/{id}/dismiss`
   - `POST /api/ceri/purge/preview`
   - `POST /api/ceri/purge/execute`
3. Add filters:
   - opportunity minimum,
   - risk maximum,
   - confidence,
   - EPS/revenue revision windows,
   - revision breadth,
   - surprise trend,
   - guidance direction,
   - catalyst category,
   - event date,
   - changed since,
   - provider freshness,
   - technical score/classification,
   - fundamental score,
   - sector state/rank,
   - market regime,
   - setup lifecycle actionability when present,
   - next binary event trading-session distance,
   - score posture and alignment flags,
   - data coverage and warning/conflict flags,
   - historical view mode and explicit `as_of`/cutoff.
4. Add deterministic sorting, pagination, total counts, and stable ticker tie-breaks.
5. Preserve JSON nulls for unavailable metrics and expose raw counts beside normalized breadth.
6. Return structured errors with stable codes, including `INVALID_FILTER`, `INVALID_DATE_RANGE`,
   `TICKER_NOT_FOUND`, `RUN_NOT_FOUND`, `PROVIDER_CAPABILITY_UNAVAILABLE`,
   `CONFIG_VERSION_NOT_FOUND`, `REVIEW_CONFLICT`, `BACKFILL_ALREADY_ACTIVE`,
   `LICENSE_RESTRICTED`, `PURGE_CONFIRMATION_REQUIRED`, and `ADMIN_FORBIDDEN`.
7. Require `mode=AS_KNOWN|LATEST_CORRECTED` and explicit `as_of`/cutoff on historical or
   reproduction-sensitive endpoints.
8. Expose operations data:
   - dataset freshness,
   - ingestion status,
   - quota state,
   - errors,
   - quarantined records,
   - stale/conflicted records,
   - reprocess controls,
   - processing/backfill checkpoints, retries, durations, and cancellation state,
   - alert delivery/dedup state,
   - provider terms/version, retention deadline, export restrictions, and purge preview.
9. Implement score/revision reproduction endpoints or detail payloads that expose source IDs,
   baseline/current IDs, selected provider hierarchy, config/calculation versions, evidence hash,
   and stored-versus-reproduced values.
10. Make every write route local-admin-only, CSRF-protected where applicable, idempotent, and audited.

Tests:

- APIs return correct filters, sorting, pagination, and null semantics.
- History supports `AS_KNOWN` and `LATEST_CORRECTED`.
- Invalid filters return stable 400 codes.
- Missing ticker/run/event resources return 404.
- Admin writes are local-only, CSRF-protected where applicable, idempotent, and audited.
- Revision/event-revision APIs expose exact lineage and historical view semantics.
- Backfill, reprocess, alert-state, quarantine/conflict, and purge routes enforce the documented contracts.

Exit criteria:

- Backend surfaces support the CERI dashboard, ticker detail, change feed, provider
  operations, exports, and review workflow.

## Phase 9: UI and Navigation

Goal: make CERI usable inside the existing local SwingLens cockpit.

Primary files:

- `app/templates/ceri_dashboard.html`
- `app/templates/ceri_ticker.html`
- `app/templates/ceri_changes.html`
- `app/templates/ceri_operations.html`
- `app/templates/partials/_nav.html`
- `app/templates/run_detail.html`
- `app/static/ceri.js`
- `app/static/app.css`
- `tests/ceri/test_routes_ui.py`

Tasks:

1. Add navigation for CERI dashboard and provider operations when `CERI_UI_ENABLED` is true.
2. Add run-detail CERI status, count, and link when a run has CERI snapshots.
3. Build dashboard:
   - headline cards for high opportunity/low risk, upward revision leaders, guidance
     raises, binary risks, stale/unrated counts, and meaningful changes,
   - filter bar combining CERI with ranking, technical classification, sector state, market
     regime, lifecycle status, and earnings risk,
   - candidate table with ticker, opportunity, risk, confidence, EPS/revenue revisions,
     breadth, guidance, next event, latest meaningful change, and source freshness,
   - change-feed panel grouped by upward revisions, negative revisions, new catalysts, risk
     escalations, and resolved events,
   - provider freshness strip.
4. Build ticker detail:
   - opportunity and risk cards,
   - component waterfall plus the top three positive and top three negative contributors for each score,
   - estimate-history chart and table by fiscal period with 7/30/90-day changes, source snapshots,
     actual elapsed days, and revision confidence,
   - analyst breadth with upward/downward counts, normalized value, analyst sample, and dispersion,
   - trailing earnings surprise and post-report reaction,
   - chronological catalyst timeline with source badges and review markers,
   - point-in-time cutoff, provider coverage, config version, and evidence hash.
5. Build change/alert views:
   - daily groups for upward revisions, downward revisions, guidance changes, new/updated/resolved
     catalysts, opportunity changes, and risk escalations/de-escalations,
   - alert filters and acknowledge/dismiss actions that never mutate source events.
6. Build operations page:
   - provider health,
   - ingestion run status,
   - quota/freshness,
   - quarantined/conflicted/stale records,
   - reprocess/backfill actions and checkpoint progress,
   - provider hierarchy/conflict detail and audited review controls,
   - provider terms/retention/export policy,
   - preview-first licensed-data purge controls.
7. Use text and icons for status meanings; never rely on color alone.
8. Keep copy research-oriented, for example "structured evidence suggests" rather than
   guaranteed outcome language.

Tests:

- Templates render full data, empty state, stale data, conflicted data, and low-confidence
  data.
- Compound filters update candidate rows.
- Ticker detail renders source provenance and warnings.
- Alert/review actions do not mutate raw evidence.
- Top three positive/negative contributors, breadth counts, event revisions, and point-in-time cutoff are visible.
- Purge execution cannot occur without a successful preview and explicit confirmation.
- Accessibility checks cover semantic table headers, keyboard navigation, focus, and
  non-color-only status communication.

Exit criteria:

- A user can find revision leaders, inspect catalysts, identify binary risk, review source
  confidence, and export evidence from the UI.

## Phase 10: Commercial Provider, Licensing, Performance, and Hardening

Goal: productionize provider integration and prove CERI meets nonfunctional, licensing,
observability, and acceptance requirements.

Primary files:

- `app/services/ceri/providers/<provider>_provider.py`
- `app/services/ceri/observability.py`
- `docs/ceri.md`
- `docs/release_notes_ceri.md`
- `tests/ceri/test_provider_contracts.py`
- `tests/ceri/test_acceptance_fixture.py`
- `tests/ceri/test_performance.py`

Tasks:

1. Add the first commercial provider adapter only after the manual provider contract passes.
2. Implement provider-specific rate limits, retry policy, capability matrix, quota status,
   licensing restrictions, terms/version metadata, retention policy, and provider-priority rules.
3. Store provider credentials through environment variables or secure local secret storage.
4. Redact secrets, auth headers, provider tokens, restricted source content, SQL details, and
   local filesystem paths from logs/errors.
5. Implement a field-level export-policy registry used by every API/export serializer.
6. Implement audited provider-license purge:
   - preview affected records and downstream dependencies,
   - require actor, reason, provider/license scope, and confirmation token,
   - preserve a non-sensitive purge audit and invalidate/rebuild affected derived snapshots,
   - prohibit ordinary cascade deletion.
7. Verify the application binds to localhost by default and all administrative writes enforce
   the existing local security and CSRF boundary.
8. Implement metrics:
   - `ceri_ingestion_*`
   - `ceri_freshness_*`
   - `ceri_coverage_*`
   - `ceri_scores_*`
   - `ceri_conflicts_*`
   - `ceri_jobs_*`
   - `ceri_processing_*` for counts, retries, duration, checkpoints, and partial status,
   - `ceri_alerts_*` for emitted, suppressed, acknowledged, dismissed, and duplicate prevention,
   - `ceri_purge_*` for previews, executions, blocked requests, and affected records.
9. Implement structured log events. Every event includes `job_id`, `processing_run_id`,
   `ingestion_run_id`, provider, dataset, company/ticker when known, calculation version,
   config hash, request key, and execution token; licensed payloads and secrets are excluded:
   - ingestion started/completed,
   - source record inserted/deduplicated/quarantined,
   - normalization failed,
   - revision rebuilt,
   - score snapshot captured,
   - change event emitted,
   - alert emitted/suppressed,
   - provider quota degraded,
   - ticker scoring failed,
   - purge preview/executed/blocked.
10. Run performance targets:
    - run-scoped CERI table for 500 tickers loads within 2 seconds after data is persisted,
    - normalization and scoring for 2,000 tickers completes within 10 minutes excluding
      provider network latency,
    - indexes support five years of daily estimate snapshots for 5,000 securities.
11. Run release verification:
    ```powershell
    ruff check app tests
    pytest tests/ceri -q
    pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q
    pytest tests/test_background_job_service.py tests/test_background_worker.py -q
    pytest -q
    alembic upgrade head
    alembic downgrade -1
    alembic upgrade head
    ```

Tests:

- Provider contract fixtures contain no live secrets.
- Quota or provider outage degrades confidence without blocking existing SwingLens views.
- Exports obey licensing field controls.
- Performance fixtures meet or document target exceptions.
- Full acceptance fixture passes AC-01 through AC-18.
- Run deletion preserves evidence, licensed-data purge is preview-first/audited, and restricted fields never leak.
- Job fencing, checkpoint resume, event revisions, alert persistence, and score/revision reproduction pass.
- Localhost binding and CSRF/local-admin protections pass.

Exit criteria:

- CERI is safe to enable for local research with provider controls, monitoring, acceptance
  coverage, and rollback gates.

## Acceptance Coverage

The release fixture must prove:

1. Comparable EPS snapshots calculate absolute and percentage revisions with stored source
   IDs.
2. Missing baselines produce unavailable revisions with reasons.
3. After-hours catalysts map to the next US trading session.
4. Equivalent provider catalyst records deduplicate into one canonical event with multiple
   source references.
5. Conflicting event dates remain visible and lower confidence.
6. Stale estimates cannot produce high confidence.
7. Opportunity and event risk remain independently visible and filterable.
8. Weight changes create new config hashes and new snapshots without changing old ones.
9. Later corrections do not leak into earlier `AS_KNOWN` views.
10. Provider outage completes with partial CERI status and preserves core SwingLens results.
11. Ticker detail identifies top positive and negative score contributors.
12. CSV and JSON exports include cutoff, confidence, versions, warnings, and evidence
    references.
13. Daily change feed does not duplicate alerts under retry.
14. Compound filtering can combine technical score, sector leadership, positive revisions,
    and bounded risk.
15. Quarantined ambiguous records never contribute to ticker scores.
16. Earnings surprise uses consensus available immediately before the report.
17. Zero or sign-changing baselines use the safe rule and explanation warning.
18. Manual classification overrides retain old value, new value, reviewer, and reason.

Additional SDD/release gates:

- Alias validity dates prevent historical ticker misidentification.
- Provider conflicts select a deterministic operational value while preserving all observations.
- Revision confidence, analyst counts, event revisions, posture, alignment flags, and top contributors
  are reproducible.
- Every durable job uses request-key coalescing, heartbeat, execution fencing, cancellation checks,
  and checkpointed retry.
- Backfill and recalculation support ticker/date/provider/configuration scope without look-ahead.
- Source evidence survives upload-run deletion; licensed-data purge is explicit, preview-first, and audited.
- Revision APIs, full-evidence export, alert persistence, and Outcome Engine point-in-time feature export
  satisfy their documented contracts.

## Test Matrix

Core unit tests:

- Config validation and hash stability.
- Provider protocol and manual provider parsing.
- Identity resolution, alias validity, CIK/provider IDs, and quarantine.
- Fiscal-period normalization for non-calendar fiscal years.
- Effective-session resolver for pre-market, regular-session, after-hours, weekend, holiday,
  missing-timestamp, and explicit daily-cutoff cases.
- Estimate normalization, exact/near deduplication, provider-priority conflicts, missing/null
  semantics, alias validity, and verified currency/scale conversion lineage.
- Baseline selection and revision formulas.
- Breadth counts/value, dispersion, elapsed-day acceleration, revision confidence, coverage, and
  overall confidence.
- Earnings surprise with pre-report consensus snapshot lineage and complete guidance normalization.
- Catalyst taxonomy, materiality, deduplication, date confidence, status/outcome revisions,
  manual reviews, provider conflicts, and mutually exclusive event handling.
- Opportunity, risk, score caps, penalties, missing components, posture, alignment flags, top
  contributors, and reasons/warnings.

Integration tests:

- Alembic upgrade/downgrade/up with PostgreSQL constraints and indexes.
- Ingestion/processing job request-key idempotency, retries, cancellation, lease heartbeats,
  execution fencing, bounded batches, checkpoint resume, and stale-worker rejection.
- Concurrent duplicate source insert attempts.
- Point-in-time queries with corrections and supersession chains.
- Run-scoped snapshot capture joining existing SwingLens entities.
- Change detection, event revision handling, persisted alert rules/events, alert-state mutation,
  and deduplication.
- API filters, revision/event-history endpoints, pagination, current/full-evidence exports,
  reproduction, backfill/reprocess, quarantine/conflict/stale operations, and admin boundaries.
- UI templates for normal, empty, stale, conflicted, low-confidence, revised-event, alert,
  checkpoint/backfill, and purge-preview states.

Leakage and quantitative tests:

- Future source records cannot affect historical scores.
- Later corrections affect only `LATEST_CORRECTED`, not `AS_KNOWN`.
- Earnings surprise uses pre-report consensus.
- Exported feature fixtures and Outcome Engine point-in-time features match independent calculations.
- Outcome Engine walk-forward calibration is required before any ranking integration.

Performance tests:

- 500-ticker run table loads within 2 seconds.
- 2,000-ticker normalization/scoring completes within 10 minutes excluding provider latency.
- Five years of daily snapshots for 5,000 securities retain acceptable query plans.
- High-frequency filters use indexed columns rather than broad JSONB scans.

Security and safety tests:

- Provider credentials are never stored in raw payloads, logs, templates, or exports.
- The app binds to localhost by default; admin writes are local-only and CSRF-protected where applicable.
- Restricted provider fields are omitted from licensed exports.
- Admin review/reprocess routes follow existing local write protections.
- No CERI route, UI, service, or job can place, stage, modify, or cancel orders.
- Upload-run deletion preserves CERI evidence, and licensed-data purge requires preview, confirmation,
  restricted-field handling, downstream invalidation, and an audit record.

## Requirement Traceability

- Configuration and provider management: Phases 0, 1, 3, 8, and 10 cover
  `FR-CERI-001` through `FR-CERI-006`.
- Company identity and source provenance: Phases 2, 3, and 4 cover `FR-CERI-007`
  through `FR-CERI-010`.
- Estimate snapshots and revision analytics: Phases 4 and 5 cover `FR-CERI-011`
  through `FR-CERI-025`.
- Earnings, surprise, and guidance intelligence: Phase 6 covers `FR-CERI-026` through
  `FR-CERI-031`.
- Catalyst-event intelligence: Phases 4, 6, 7, 8, and 9 cover `FR-CERI-032` through
  `FR-CERI-038`.
- Scores and policy outputs: Phase 6 covers `FR-CERI-039` through `FR-CERI-045`.
- Filtering, ranking context, and change detection: Phases 7, 8, and 9 cover
  `FR-CERI-046` through `FR-CERI-052`.
- APIs, exports, and operations: Phases 7, 8, 9, and 10 cover `FR-CERI-053` through
  `FR-CERI-058`.
- Business rules, nonfunctional requirements, security, observability, and acceptance
  criteria are cross-cutting and must map to concrete tests before release.
- Effective-session, source hierarchy, event revisions, alert persistence, job fencing/checkpoints,
  retention/purge, revision APIs, score reproduction, and Outcome Engine feature export are explicit
  SDD contracts and may not be treated as optional implementation detail.

Release rule:

- Every `Must` requirement must map to at least one implementation artifact and one passing
  test, or to an explicit approved waiver in release notes.
- Every user-visible score must be reproducible from immutable source identifiers, feature
  values, config hash, calculation version, and evidence hash.
- No provider ingestion, run capture, UI, or alert feature flag may be enabled until the
  corresponding tests and rollback gate pass.

## Recommended Pull Request Order

1. PR 1: Phase 0 settings, pipeline placeholders, safety decisions, and baseline.
2. PR 2: Phase 1 config, taxonomy, enums, DTOs, and validation.
3. PR 3: Phase 2 database model, Alembic migration, relationships, and schema tests.
4. PR 4: Phase 3 provider protocol, manual provider, source records, and ingestion audit.
5. PR 5: Phase 4 identity resolution, normalization, catalyst taxonomy, and deduplication.
6. PR 6: Phase 5 point-in-time query service and revision features.
7. PR 7: Phase 6 surprise, guidance, catalyst features, scoring, confidence, and snapshots.
8. PR 8: Phase 7 change detection, persisted alerts, processing runs, durable job handlers,
   backfill/checkpoints, exports, Outcome Engine bridge, and pipeline capture.
9. PR 9: Phase 8 query/revision/event APIs, reproduction, admin/backfill routes, provider health,
   quarantine/conflict/stale operations, and purge preview/execute contracts.
10. PR 10: Phase 9 dashboard, ticker detail, contributor/revision views, change/alert feed,
    operations UI, and navigation.
11. PR 11: Phase 10 commercial provider, licensing/retention/purge controls, observability,
    local security, performance, acceptance fixtures, and release notes.

## Rollback Strategy

- Keep provider ingestion, run capture, UI, alerts, admin routes, and backfill behind
  independent flags.
- If provider ingestion fails, disable `CERI_PROVIDER_INGEST_ENABLED` and keep existing
  evidence available for point-in-time reads.
- If run capture fails, disable `CERI_RUN_CAPTURE_ENABLED`; existing SwingLens pipeline
  outputs remain unaffected.
- If alerts are noisy, disable `CERI_ALERTS_ENABLED` without deleting change events.
- If UI has defects, disable `CERI_UI_ENABLED` while APIs and snapshots remain inspectable.
- Migration downgrade removes only CERI tables and seeded config/taxonomy artifacts in non-production
  test environments. Production evidence is not removed through ordinary rollback; feature flags disable
  behavior while preserving audit history.
- Provider-license purge is not a rollback mechanism and cannot be undone; it requires preview and audit.
- Existing combined results, ranking results, market-regime snapshots, sector-rotation
  snapshots, SLSE tables, OWPE tables, and price bars remain unchanged.

## Deferred or Optional Scope

Defer unless v1 is stable:

- Automatic ranking-score mutation or CERI-blended rankings.
- Full natural-language news sentiment.
- Generative-AI-only catalyst classification.
- Intraday event-arbitrage workflows.
- Unverified social-media or message-board rumor scoring.
- External alert delivery such as email, SMS, push, webhook, or broker integration.
- Non-US equities and non-equity asset classes.
- Provider redistribution beyond licensed/exportable fields.

## Definition of Done

CERI is done when SwingLens can ingest structured estimate/catalyst evidence through the
manual provider, preserve immutable source provenance, normalize identities and fiscal
periods, compute point-in-time revisions, calculate earnings/guidance/catalyst features,
produce independent opportunity and event-risk scores with confidence and reasons, capture
run-scoped immutable score snapshots, persist event revisions and deduplicated alerts, detect
material changes without duplicates, expose dashboard/ticker/change/provider/revision views and
current/full-evidence JSON/CSV exports, support checkpointed backfill and audited reprocessing,
reproduce scores and revisions, preserve evidence across run deletion, enforce provider-license
retention/purge rules, export point-in-time features to Outcome Engine calibration, pass leakage and
acceptance fixtures, meet the stated performance targets, and remain strictly research-only.
