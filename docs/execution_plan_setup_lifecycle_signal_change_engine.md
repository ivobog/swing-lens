# SwingLens Execution Plan: Setup Lifecycle and Signal-Change Engine

**Document version:** 1.1
**Status:** Reviewed and updated implementation plan
**Last updated:** 2026-08-01
**Coverage review:** Reconciled requirement-by-requirement against the SLSE SRS and SDD
**Feature:** Setup Lifecycle and Signal-Change Engine (SLSE)

Source documents:

- `C:\Users\Ivica\Downloads\SwingLens_Setup_Lifecycle_and_Signal_Change_Engine_SRS.docx`
- `C:\Users\Ivica\Downloads\SwingLens_Setup_Lifecycle_and_Signal_Change_Engine_SDD.docx`

This plan implements SLSE as an additive temporal interpretation layer over existing
SwingLens run outputs. Existing fundamental scores, technical scores, combined results,
ranking profiles, market regime, sector rotation, and OWPE outputs remain independent.
SLSE captures immutable point-in-time setup snapshots, selects a canonical ticker-day
sequence, detects material changes, manages setup episodes, creates in-app alerts, and
answers: what became newly actionable, stronger, weaker, triggered, extended, failed, or
expired today?

## Current Repo Baseline

- The app is a local FastAPI/Jinja2/HTMX/PostgreSQL application.
- SQLAlchemy models currently live in `app/models/tables.py` with Alembic migrations in
  `alembic/versions/`.
- Existing source entities already cover most SLSE inputs:
  `UploadRun`, `RawCompanyRow`, `FundamentalScore`, `TechnicalScore`, `CombinedResult`,
  `RankingResult`, `MarketRegimeSnapshot`, `SectorRotationSnapshot`, `SectorRotationRow`,
  and `PriceBar`.
- Durable background jobs already support lease owner, execution token, heartbeat,
  lease expiry, stale recovery, cancellation, and fencing through
  `app/services/background_job_service.py`.
- The full pipeline currently ends with:
  `SECTOR_ROTATION_SNAPSHOT`, then `CAPTURING_WINNER_PREDICTIONS`.
- SLSE should add `CAPTURING_SETUP_SIGNALS` and `EVALUATING_SETUP_LIFECYCLES` after
  sector rotation and before winner-probability capture, so OWPE can later consume
  stable lifecycle features.
- OWPE is already present under `app/services/winner_probability/`; SLSE should not
  depend on OWPE for v1 operation.
- Existing execution plans in `docs/` use dependency-ordered phases, primary files,
  tests, exit criteria, rollout gates, and traceability. This plan follows that pattern.

## V1 Product Decisions

Use these decisions to keep implementation coherent:

1. Build v1 for US equities, daily bars, completed trading sessions, and research-only use.
2. Use forward capture as the authoritative v1 mode. Historical reconstruction is
   optional, separately labeled, lower confidence, and excluded from live alert evidence.
3. Keep lifecycle state separate from actionability. A ticker can be `READY` and still
   `BLOCKED` by earnings, market policy, stale data, liquidity, or confidence.
4. Use completed daily close as the v1 trigger authority. Store high-cross evidence in
   debug/signals, but do not treat it as the default trigger.
5. Preserve immutable snapshots and events. Corrections create canonical revisions or new
   evaluation versions; they do not rewrite history.
6. Compare only canonical snapshots for ticker/timeframe sequential changes.
7. Permit one active episode per ticker, timeframe, and setup family. Multiple families
   may coexist; primary display is deterministic.
8. Implement breakout and pullback families first. Add VCP and continuation after the
   core state machine and episode invariants are stable.
9. In-app alerts only for v1. No email, SMS, push, webhook, broker orders, or order staging.
10. Store stable reason codes and evidence JSON for every transition and material change.
11. Use YAML configuration plus normalized config hashes for thresholds, states, phases,
    signal registry, hysteresis, cooldowns, confidence, and alerts.
12. Treat missing optional metrics as null with warnings, never zero.
13. Use existing background-job lease hardening; add SLSE-specific bounded batch
    heartbeats and partial retry handling.
14. Release in stages: shadow capture, lifecycle preview, dashboard, alerts, replay, then
    OWPE bridge.


## Version 1.1 Coverage Corrections and Mandatory Additions

The original phase structure is retained. The following additions are mandatory and
supersede any earlier wording that would permit them to remain optional at final release.

### A. Locked product and authority decisions

1. Breakout and pullback remain the first adapters implemented, but final release requires
   working and tested adapters for `BREAKOUT`, `PULLBACK`, `VCP`, `CONTINUATION`, and
   `GENERIC`. VCP, continuation, or generic may be temporarily feature-disabled only during
   intermediate rollout stages.
2. Store the SRS data-quality label independently from transition confidence:
   `HIGH`, `NORMAL`, `LOW`, or `INSUFFICIENT`.
3. Completed daily close remains the authoritative v1 trigger. Persist diagnostic
   intraday-high crossing evidence separately, and never let high-cross evidence alone
   create the default `TRIGGERED` state.
4. `RECONSTRUCTED` snapshots are excluded from live alerts, live alert statistics, and OWPE
   feature exports by default. Inclusion requires an explicit audited override.
5. Persisted replay creates a parallel evaluation version. It does not replace the live
   current-version pointer unless an explicit local administrative promotion action is
   executed and audited.
6. Signal snapshots, episodes, lifecycle events, signal-change events, alert source events,
   and evaluation versions are retained indefinitely by default.
7. Any purge is local-only, disabled by default, preview-first, explicitly scoped,
   confirmation protected, count-reporting, and audited.
8. Lock the exact SRS performance targets:
   - capture plus evaluation of 1,000 tickers within 60 seconds, excluding upstream
     market-data fetching;
   - default dashboard and ticker-timeline APIs within 500 ms at p95 against a PostgreSQL
     fixture containing at least 100,000 snapshots.
9. Lock API behavior:
   - invalid state, date, threshold, cursor, sort, direction, or configuration input returns
     HTTP 400 with a stable error code;
   - missing ticker, episode, evaluation, alert, or run-scoped lifecycle resource returns
     HTTP 404;
   - accepted background evaluation returns HTTP 202 with `evaluation_id`, current status,
     and status URL;
   - unavailable metrics remain JSON `null` and are never replaced by zero or empty text.

### B. Phase 0 additions: semantic and operational guard rails

Add these tasks to Phase 0:

1. Approve retention, purge, replay-authority, and API-error semantics before migration work.
2. Document the exact 60-second and 500-ms NFR targets as constants or validated config.
3. Define a stable API error vocabulary, including at least:
   - `INVALID_STATE`
   - `INVALID_DATE`
   - `INVALID_THRESHOLD`
   - `INVALID_SORT`
   - `INVALID_CURSOR`
   - `INVALID_CONFIGURATION`
   - `TICKER_NOT_FOUND`
   - `EPISODE_NOT_FOUND`
   - `EVALUATION_NOT_FOUND`
   - `ALERT_NOT_FOUND`
   - `RUN_LIFECYCLE_NOT_FOUND`
4. Confirm that administrative evaluate supports one run, one ticker, one date range, and all
   eligible records.
5. Confirm that replay output is non-authoritative by default and that promotion is a
   separate action.

Add tests proving the above decisions are represented in settings, validated configuration,
or stable constants.

### C. Phase 1 additions: configuration, enums, and registry

Add to `config/setup_lifecycle.yaml` and the config loader:

1. Explicit data-quality-label rules for `HIGH`, `NORMAL`, `LOW`, and `INSUFFICIENT`.
2. Diagnostic high-cross signal definitions separate from close-authoritative trigger rules.
3. Generic fallback policy and a rule preventing generic from shadowing a supported family
   when the supported family has sufficient evidence.
4. Stable API error codes and exact performance targets.
5. Reconstructed-origin exclusion defaults for alerts and OWPE export.
6. Replay-authority and promotion policy.
7. Retention and purge policy flags.

Add `DataQualityLabel` to the enum set.

Add validation tests for:

- deterministic data-quality labels;
- generic fallback precedence;
- high-cross versus close-trigger separation;
- stable error-code references;
- reconstructed-origin exclusion policy.

### D. Phase 2 additions: persistence and audit model

Extend `setup_lifecycle_evaluation_runs` with:

- `current_phase`;
- explicit read, captured, canonical, changed, transitioned, alerted, skipped, warning, and
  failed counts;
- requested configuration JSON;
- dry-run flag;
- requester;
- source snapshot minimum and maximum IDs;
- output evaluation version;
- duration and last heartbeat.

Extend `setup_signal_snapshots` with:

- explicit `data_quality_label`;
- close-authoritative trigger values;
- diagnostic high-cross evidence;
- `canonical_decision_json` containing ordered comparator values and selected reason.

Extend `setup_lifecycle_events` with a stable `source_event_key` and database uniqueness
within the evaluation version.

Add or confirm database uniqueness for:

- one canonical snapshot per ticker, timeframe, and data-as-of date;
- one active episode per ticker, timeframe, and setup family;
- lifecycle source-event key;
- signal-change source-event key;
- alert event key.

Add `setup_lifecycle_administrative_audit_events` for:

- replay promotion or retirement;
- repair;
- purge preview and execute;
- administrative configuration changes.

Repository work must include retention queries, purge preview and execute support,
administrative audit writes, and replay-version authority lookups.

### E. Phase 3 additions: point-in-time integrity and data quality

The snapshot builder must enforce:

1. Source effective dates are not later than `data_as_of_date`.
2. Source calculation or creation timestamps are not later than the capture cutoff unless
   explicitly classified as metadata.
3. Market-regime and sector-rotation snapshots are not newer than the ticker as-of date.
4. Future outcome data, later lifecycle records, and later OWPE results are never copied into
   the snapshot.
5. Price bars used for evidence preserve source, adjustment type, timeframe, and hash.
6. Missing optional values remain null with warnings.
7. Data-quality labels follow the SRS rules:
   - `HIGH`: all required features, fresh completed bar, consistent context;
   - `NORMAL`: required features with minor optional omissions;
   - `LOW`: inferred required feature, missing context, near-stale data, or disagreement;
   - `INSUFFICIENT`: hard-required evidence absent or stale beyond the hard limit.
8. Diagnostic high-cross evidence is persisted but cannot independently create the default
   v1 trigger.

Add no-lookahead, future-dated-source, data-quality-label, and high-cross diagnostic tests.

### F. Phase 4 and Phase 5 additions: canonical audit and complete change registry

Canonicalization must persist the complete ordered comparator evidence in
`canonical_decision_json`, not only a short reason string.

Canonical revision events must use stable keys and remain deduplicated under retry.

Evaluation status must update `current_phase`, detailed counts, duration, warnings, failures,
and heartbeat between every bounded phase and batch.

The default signal registry and change detector must explicitly cover:

- technical score;
- setup score;
- classification;
- stage;
- relative strength;
- sector rank;
- market regime;
- earnings risk;
- liquidity;
- data quality;
- configured derived metrics.

For numeric signals, persist the applicable raw, percentage, percentile, and rank deltas in
addition to normalized direction.

### G. Phase 6 and Phase 7 additions: complete families and gap semantics

Add:

- `app/services/setup_lifecycle/generic_adapter.py`;
- `tests/setup_lifecycle/test_generic_lifecycle.py`.

Final acceptance requires fixtures for breakout, pullback, VCP, continuation, and generic.

Observation-gap behavior must:

1. Never infer failure from absence alone.
2. Increment only for completed US trading sessions with an applicable completed
   observation/evaluation opportunity.
3. Reset to zero when a canonical snapshot returns.
4. Use family-specific gap thresholds.
5. Expire exactly once after the configured threshold.

Stale source bars remain queryable and produce `LOW_CONFIDENCE` with freshness warnings.
Only severe hard-required-data failure may become `BLOCKED`.

### H. Phase 8 additions: alert completeness

Alert queries and UI must support:

- unread;
- acknowledged;
- dismissed;
- date;
- ticker;
- lifecycle state;
- severity;
- setup family where configured.

Reconstructed-origin events do not create live alerts or contribute to live alert statistics
unless an explicit audited override is active.

Add acceptance tests for both policies.

### I. Phase 9 additions: exact routes, evaluation scopes, API contracts, and purge

Implement all SRS HTML routes:

- `GET /setup-lifecycle`
- `GET /setup-lifecycle/ticker/{ticker}`
- `GET /setup-lifecycle/episodes/{episode_id}`
- `GET /setup-lifecycle/alerts`
- `GET /runs/{run_id}/setup-lifecycle`
- `GET /setup-lifecycle/export.csv`
- `GET /setup-lifecycle/export.json`

Implement all SRS JSON routes:

- `GET /api/setup-lifecycle/changes`
- `GET /api/setup-lifecycle/tickers/{ticker}`
- `GET /api/setup-lifecycle/episodes/{episode_id}`
- `GET /api/setup-lifecycle/alerts`
- `POST /api/setup-lifecycle/alerts/{alert_id}/acknowledge`
- `POST /api/setup-lifecycle/alerts/{alert_id}/dismiss`
- `POST /api/setup-lifecycle/run/{run_id}/evaluate`
- `POST /api/setup-lifecycle/evaluate`
- `POST /api/setup-lifecycle/replay`
- `GET /api/setup-lifecycle/evaluations/{evaluation_id}`
- `GET /api/setup-lifecycle/filter-options`
- `GET /api/setup-lifecycle/operations`
- `GET /api/setup-lifecycle/diagnostics`

Administrative evaluation payloads must support:

- source run;
- ticker;
- date range;
- all eligible records;
- capture-only, evaluate, dry-run, replay, or repair mode where applicable.

Replay records must preserve:

- source snapshot range;
- requested configuration;
- dry-run flag;
- requester;
- output evaluation version.

Add local-only administrative endpoints:

- `POST /api/setup-lifecycle/purge/preview`
- `POST /api/setup-lifecycle/purge/execute`
- `POST /api/setup-lifecycle/evaluations/{evaluation_id}/promote`
- `POST /api/setup-lifecycle/evaluations/{evaluation_id}/retire`

Purge execution requires a confirmation token derived from the preview and must report
affected snapshot, episode, lifecycle-event, signal-change, alert, and evaluation counts.

All list APIs must support limit, cursor or page, sort, direction, filters, and total counts.
All responses must preserve JSON nulls and follow the stable error contract.

### J. Phase 10 additions: exact UI behavior and accessibility

The Market Changes page must include these quick filters:

- Newly Ready
- Newly Triggered
- Improving Fast
- Failed Today
- Extended
- Gate Blocked
- Low Confidence
- No Material Change

Display:

- selected data-as-of trading date;
- comparison date;
- missing-session gap badge;
- stale-system warning.

Ticker lifecycle source links must include, when available:

- source run;
- technical score card;
- market-regime snapshot;
- sector-rotation view;
- OWPE or future outcome record.

Accessibility is a release requirement:

- semantic table headers and captions;
- keyboard-operable filters, pagination, row expansion, replay controls, and alert actions;
- visible focus;
- state, actionability, warning, and risk meaning conveyed with text or icons, never color
  alone.

### K. Phase 11 additions: replay authority and lifecycle

Add explicit replay-version promotion and retirement:

1. Preview differences before promotion.
2. Require local administrative confirmation, requester, and reason.
3. Atomically update the live current-version pointer.
4. Preserve all previous authority history.
5. Write an administrative audit event.
6. Make retirement non-destructive.

OWPE export must use immutable SLSE evidence, exclude future evidence, and exclude
reconstructed-origin records by default.

### L. Phase 12 additions: performance, observability, accessibility, and acceptance

Run performance checks against the exact SRS targets:

- 1,000-ticker capture plus evaluation within 60 seconds;
- dashboard and ticker timeline within 500 ms p95;
- PostgreSQL fixture with at least 100,000 snapshots;
- query-plan checks proving high-frequency filters use promoted/indexed columns rather than
  broad JSONB scans.

Implement and test the named operational metrics:

- `slse_snapshots_captured`
- `slse_snapshots_canonical`
- `slse_change_events_created`
- `slse_lifecycle_transitions`
- `slse_active_episodes`
- `slse_alerts_created`
- `slse_evaluation_failures`
- `slse_evaluation_duration_seconds`
- `slse_low_confidence_share`
- `slse_canonical_revisions`

Implement and test structured log events for:

- capture started and completed;
- snapshot skipped;
- canonical changed;
- transition created;
- episode closed;
- alert created or suppressed;
- ticker failed;
- replay completed.

Add explicit accessibility tests and API-contract tests.

### M. Requirement-complete acceptance overlay

Before final release, the acceptance fixture must prove:

1. All `SLSE-FR-001` through `SLSE-FR-075` Must requirements have an implementation artifact
   and a passing test or approved waiver.
2. All `SLSE-NFR-001` through `SLSE-NFR-012` have a measurable verification.
3. All SRS acceptance criteria AC-01 through AC-15 pass.
4. Every user-visible transition is reproducible from immutable snapshots, configuration
   version, engine version, source IDs, stable event key, reason codes, and metric evidence.
5. VCP, continuation, and generic are not left as indefinite optional scope.
6. Exact HTML/API routes and stable error/null contracts pass.
7. Replay cannot silently replace live authority.
8. Purge cannot execute without preview, confirmation, explicit scope, and audit.
9. UI accessibility and research-only safety checks pass.
10. No SLSE route or service can place or stage a broker order.


## Phase 0: Preparation, Baseline, and Guard Rails

Goal: make the branch, baseline, flags, and irreversible semantic choices explicit before
schema work.

Primary files:

- `docs/execution_plan_setup_lifecycle_signal_change_engine.md`
- `app/settings.py`
- `.env.example`
- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `tests/test_settings.py`
- `tests/test_pipeline_service.py`
- `tests/test_pipeline_executor.py`

Tasks:

1. Create a branch, for example `codex/setup-lifecycle-signal-change-engine`.
2. Capture baseline checks:
   ```powershell
   ruff check app tests
   pytest -q
   alembic heads
   alembic current
   ```
3. Resolve the actual Alembic head immediately before writing the migration. Do not assume
   the example revision number remains current.
4. Add feature flags/settings:
   - `SETUP_LIFECYCLE_ENABLED=false`
   - `SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED=false`
   - `SETUP_LIFECYCLE_ALERTS_ENABLED=false`
   - `SETUP_LIFECYCLE_REPLAY_ENABLED=false`
   - `SETUP_LIFECYCLE_RECONSTRUCTION_ENABLED=false`
   - `SETUP_LIFECYCLE_CONFIG_PATH=config/setup_lifecycle.yaml`
5. Add pipeline step names and statuses, but keep disabled by default:
   - `CAPTURING_SETUP_SIGNALS`
   - `EVALUATING_SETUP_LIFECYCLES`
6. Position SLSE after `SECTOR_ROTATION_SNAPSHOT` and before
   `CAPTURING_WINNER_PREDICTIONS`.
7. Decide run deletion behavior before schema work. Recommendation: use `ON DELETE SET NULL`
   for immutable SLSE source links while retaining `source_run_id_text`, ticker, dates,
   and lineage hashes so deleted upload runs cannot make lifecycle records ambiguous.
8. Confirm no SLSE package imports IB order clients or OWPE services.
9. Document v1 trigger semantics: completed daily close is authoritative.
10. Document v1 origin semantics: live forward capture only; reconstructed data is separate.

Tests:

- Settings load with default flags disabled.
- Pipeline step order includes SLSE only when the flag path is enabled.
- Pipeline stays unchanged when SLSE flags are disabled.
- Background lease heartbeat/fencing tests still pass.

Exit criteria:

- Baseline health and migration head are known.
- Feature flags are disabled by default.
- Step order, daily-close trigger rule, run deletion policy, and forward-only origin are
  documented and tested.

## Phase 1: Configuration, Enums, DTOs, and Signal Registry

Goal: define validated behavior before any database writes.

Primary files:

- `config/setup_lifecycle.yaml`
- `app/services/setup_lifecycle/__init__.py`
- `app/services/setup_lifecycle/config.py`
- `app/services/setup_lifecycle/enums.py`
- `app/services/setup_lifecycle/dtos.py`
- `app/services/setup_lifecycle/signal_registry.py`
- `tests/setup_lifecycle/test_config.py`
- `tests/setup_lifecycle/test_signal_registry.py`

Tasks:

1. Add `config/setup_lifecycle.yaml` with:
   - engine version, schema version, config version, and timeframe,
   - canonicalization precedence and required contexts,
   - supported states, phases, transition precedence, and terminal states,
   - family policies for breakout, pullback, VCP, continuation, and generic,
   - hysteresis enter/exit thresholds,
   - persistence counters and confirmation windows,
   - episode age, observation-gap, expiry, and rearm rules,
   - confidence thresholds and weights,
   - actionability gates for market, earnings, liquidity, freshness, and confidence,
   - signal registry definitions and materiality thresholds,
   - velocity windows `[1, 3, 5, 10]`,
   - alert rules, cooldowns, confidence floors, and severity,
   - API defaults, pagination limits, export limits, and performance targets.
2. Implement frozen config dataclasses and strict YAML loader.
3. Implement stable enums/constants:
   - `LifecycleState`
   - `SetupFamily`
   - `Actionability`
   - `EventSeverity`
   - `ConfidenceLabel`
   - `SnapshotOrigin`
   - `EpisodeStatus`
   - `EvaluationMode`
   - `EvaluationStatus`
   - `SignalValueType`
   - `SignalCategory`
   - `AlertStatus`
4. Implement DTOs for snapshot source records, normalized snapshots, signal values,
   change decisions, family evidence, lifecycle decisions, actionability decisions,
   episode apply results, alert results, query filters, and exports.
5. Implement `SignalDefinitionRegistry`:
   - value type,
   - source field,
   - favorable direction,
   - unit,
   - materiality threshold,
   - threshold crossings,
   - missing-value policy,
   - display label,
   - velocity windows.
6. Validate:
   - all referenced states and phases exist,
   - transition precedence covers all supported states,
   - terminal-state rules are complete,
   - enter/exit hysteresis thresholds are logically ordered,
   - alert rules reference known events and severities,
   - confidence thresholds are ordered and within 0 to 100,
   - signal definitions reference known promoted fields or JSON paths,
   - config hash is stable after default application and sorted serialization.

Tests:

- Default config loads.
- Invalid states, phases, thresholds, signals, alert rules, or confidence ranges fail fast.
- Config hash is stable for semantically identical YAML.
- Signal normalizers preserve null and quality states.
- Direction handling is correct for higher-is-better, lower-is-better, rank, boolean,
  enum, set, date, and nullability signals.

Exit criteria:

- SLSE behavior can be loaded, validated, and hashed without touching persistence.
- Pure code can use DTOs and registry definitions without ORM dependencies.

## Phase 2: Persistence Model and Migration

Goal: create append-only storage for snapshots, evaluation runs, episodes, lifecycle
events, signal changes, alert rules, and alert events.

Primary files:

- `app/models/tables.py` or a new `app/models/setup_lifecycle_tables.py`
- `app/models/__init__.py`
- `alembic/versions/<next_revision>_create_setup_lifecycle_tables.py`
- `app/services/setup_lifecycle/repository.py`
- `tests/setup_lifecycle/test_schema.py`
- `tests/setup_lifecycle/test_repository.py`

Tasks:

1. Add `setup_lifecycle_evaluation_runs`:
   - source run scope,
   - mode `LIVE`, `DRY_RUN`, `REPLAY`, `REPAIR`,
   - status `PENDING`, `RUNNING`, `COMPLETED`, `PARTIAL`, `FAILED`, `CANCELLED`,
   - engine/config versions and hash,
   - date range and ticker scope,
   - counts, errors, heartbeat, started/completed timestamps, and audit metadata.
2. Add `setup_signal_snapshots`:
   - run/source identity, ticker, timeframe, data-as-of date, calculated timestamp,
   - origin type, engine version, config version, config hash, source data hash,
   - raw/fundamental/technical/combined/ranking/market/sector source links,
   - promoted scalar fields for scores, classification, price/trigger, trend,
     contraction/volume, leadership, risk/context, and quality,
   - `signals_json`, `feature_flags_json`, `warning_flags_json`, `missing_data_json`,
     `source_lineage_json`, and `debug_json`,
   - canonical metadata: `is_canonical`, `canonical_reason`,
     `superseded_by_snapshot_id`, `canonicalized_at`,
   - created timestamp only for immutable fields.
3. Add `setup_lifecycle_episodes`:
   - ticker, timeframe, setup family, status,
   - opened/current/closed dates,
   - last observed date and missing-observation sessions,
   - current state, phase, actionability, confidence, state age,
   - opening/current/closing snapshot and evaluation links,
   - terminal reason fields,
   - primary display flags and summary,
   - engine/config versions.
4. Add a PostgreSQL partial unique index:
   `(ticker, timeframe, setup_family) WHERE status = 'ACTIVE'`.
5. Add `setup_lifecycle_events`:
   - episode and evaluation links,
   - effective date and event type,
   - from/to state and phase,
   - state age before transition,
   - actionability before/after,
   - confidence,
   - reason/evidence/warning JSON,
   - snapshot ID,
   - version/supersession fields.
6. Add `signal_change_events`:
   - evaluation ID, ticker, timeframe, date, category, signal key,
   - previous/current snapshot IDs,
   - optional episode ID,
   - value type, old/new values, numeric delta, normalized delta, direction,
   - threshold name/direction, severity, reasons,
   - signal definition version, config hash, and stable `source_event_key`.
7. Add `signal_alert_rules` seeded with built-in v1 rules.
8. Add `signal_alert_events`:
   - source lifecycle/change event links,
   - rule ID,
   - stable event key,
   - status `UNREAD`, `ACKNOWLEDGED`, `DISMISSED`,
   - severity, created/acknowledged/dismissed timestamps,
   - reason/evidence JSON.
9. Add indexes for:
   - ticker/date,
   - data-as-of and canonical,
   - current episode state/actionability/family,
   - events by effective date and episode,
   - changes by date/category/severity/signal,
   - alerts by status/severity/date,
   - source data hash and config hash.
10. Implement repository methods for idempotent writes, canonical locks, active episode
    locks, alert deduplication, timeline queries, dashboard queries, exports, and replay
    version lookups.

Tests:

- SQLAlchemy metadata includes all tables, relationships, constraints, indexes, and JSONB fields.
- Alembic upgrade/downgrade/upgrade works against the project PostgreSQL test path.
- Duplicate snapshot natural keys are rejected or resolved idempotently.
- Only one active episode per ticker/timeframe/family is possible.
- Duplicate lifecycle events and duplicate alert event keys are rejected.
- Canonical metadata can change without mutating immutable snapshot evidence.
- Run deletion behavior preserves unambiguous lifecycle lineage.

Exit criteria:

- Persistence can represent immutable snapshots, canonical revisions, episodes, lifecycle
  events, signal changes, alerts, evaluation versions, and audit lineage.

## Phase 3: Source Loader and Snapshot Builder

Goal: capture one immutable normalized snapshot per eligible ticker after a completed run.

Primary files:

- `app/services/setup_lifecycle/source_loader.py`
- `app/services/setup_lifecycle/snapshot_builder.py`
- `app/services/setup_lifecycle/repository.py`
- `tests/setup_lifecycle/test_source_loader.py`
- `tests/setup_lifecycle/test_snapshot_builder.py`

Tasks:

1. Build a ticker-indexed source context for a run:
   - raw row,
   - fundamental score,
   - technical score,
   - combined result,
   - ranking results by profile,
   - latest compatible market-regime snapshot,
   - latest compatible sector-rotation snapshot and row,
   - relevant price bars.
2. Resolve `data_as_of_date` from completed daily bars and technical evidence.
3. Reject or warn on future-dated source context relative to the snapshot as-of date.
4. Normalize fields into promoted columns plus `signals_json`.
5. Copy source IDs and source lineage into every snapshot.
6. Compute:
   - required-feature coverage,
   - freshness status,
   - technical confidence,
   - source data hash,
   - config hash,
   - source lineage hash inputs.
7. Preserve missing optional values as null and add explicit warnings.
8. Treat per-ticker source failures as nonfatal. The evaluation run becomes `PARTIAL` only
   when ticker failures occur; systemic failures become `FAILED`.
9. Implement idempotent capture for `(run_id, ticker, timeframe, engine_version, config_hash)`.
10. Keep all same-day revisions, even when not canonical.

Tests:

- A completed run creates one snapshot for each eligible ticker.
- Retry does not create duplicates.
- Missing optional data remains null and appears in warnings.
- Source IDs are copied for all available upstream records.
- Stale source bars produce low-confidence/freshness warnings.
- One bad ticker does not prevent other snapshots.
- Snapshot source hash changes when relevant source evidence changes.

Exit criteria:

- Shadow capture can persist immutable snapshots without lifecycle state changes or alerts.

## Phase 4: Canonicalization and Evaluation Orchestration

Goal: choose canonical ticker-day snapshots and orchestrate the evaluate-run workflow.

Primary files:

- `app/services/setup_lifecycle/canonicalization.py`
- `app/services/setup_lifecycle/evaluation_service.py`
- `app/services/setup_lifecycle/repository.py`
- `app/services/setup_lifecycle/job_handlers.py`
- `app/services/background_worker.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `tests/setup_lifecycle/test_canonicalization.py`
- `tests/setup_lifecycle/test_evaluation_service.py`
- `tests/setup_lifecycle/test_job_handlers.py`
- `tests/test_pipeline_service.py`
- `tests/test_pipeline_executor.py`

Tasks:

1. Implement canonical precedence:
   - completed latest daily bar,
   - successful source pipeline and no fatal technical error,
   - highest required-feature coverage,
   - market-regime and sector-rotation context present,
   - latest calculation timestamp,
   - highest snapshot ID as final deterministic tie-break.
2. Lock affected ticker-day canonical sets during updates.
3. Ensure exactly one canonical snapshot per ticker/timeframe/data-as-of date.
4. Create canonical revision audit change events when canonical selection changes.
5. Load previous canonical snapshots for each ticker/timeframe in batched queries.
6. Add `SETUP_LIFECYCLE_EVALUATE_RUN` background job handling:
   - create evaluation run,
   - load config,
   - capture snapshots in batches,
   - canonicalize affected dates,
   - evaluate changes and lifecycle per ticker/family,
   - evaluate alerts,
   - finalize counts and status.
7. Add pipeline integration behind flags:
   - execute after sector rotation,
   - record lifecycle snapshot count, transition count, alert count, active episode count,
     low-confidence count, and error count in pipeline result JSON,
   - mark SLSE failure as nonfatal `PARTIAL` when upstream results are usable.
8. Use heartbeat and cancellation checks between batches.
9. Use per-ticker savepoints or bounded transactions so a data-specific ticker failure is
   independently retryable.

Tests:

- Multiple same-day snapshots select exactly one canonical record.
- Canonical revision keeps old snapshots.
- Previous comparison uses prior canonical ticker/timeframe only.
- Session gaps use completed US trading sessions.
- Pipeline step is skipped when disabled.
- Enabled step writes counts and can mark the pipeline `PARTIAL`.
- Cancellation and lease loss stop before final writes.

Exit criteria:

- A full run can capture and canonicalize snapshots through a durable job with observable,
  idempotent status.

## Phase 5: Signal-Change Detection Engine

Goal: emit material change events from consecutive canonical snapshots without alert noise.

Primary files:

- `app/services/setup_lifecycle/change_detector.py`
- `app/services/setup_lifecycle/signal_registry.py`
- `app/services/setup_lifecycle/repository.py`
- `tests/setup_lifecycle/test_change_detector.py`
- `tests/setup_lifecycle/test_signal_velocity.py`

Tasks:

1. Implement change types:
   - float,
   - percentage,
   - integer/rank,
   - boolean,
   - enum,
   - set membership,
   - date,
   - nullability.
2. Implement direction-aware normalized delta:
   - higher-is-better,
   - lower-is-better,
   - lower-is-better-until-trigger,
   - lower-rank-is-better,
   - risk-increase/risk-decrease semantics.
3. Detect threshold crossings separately from raw delta magnitude.
4. Implement velocity over 1, 3, 5, and 10 completed sessions when sufficient history exists.
5. Categorize changes into:
   - `SETUP`
   - `SCORE`
   - `TREND`
   - `VOLATILITY_VOLUME`
   - `LEADERSHIP`
   - `MARKET`
   - `RISK`
   - `DATA_QUALITY`
6. Classify severity as `INFO`, `NOTABLE`, `ACTIONABLE`, or `RISK`.
7. Emit missing-to-present, present-to-missing, and stale-to-fresh data-quality events.
8. Use stable source event keys for deduplication after retry.
9. Record old value, new value, raw delta, normalized delta, direction, threshold name,
   source snapshot IDs, and reasons.
10. Suppress sub-threshold changes, while allowing lifecycle transitions to produce their
    own events later.

Tests:

- Threshold entry and exit produce one event each.
- Repeated beyond-threshold values do not produce duplicate events.
- Numeric, rank, enum, boolean, set, date, and nullability changes behave correctly.
- Velocity windows produce expected values and skip insufficient history.
- Data quality events are explicit.
- Source event keys are stable across retry.

Exit criteria:

- Material change events are deterministic, deduplicated, direction-aware, and fully
  explainable.

## Phase 6: Lifecycle Family Adapters and Pure State Machine

Goal: evaluate setup family, phase, lifecycle state, confidence, and reasons from
normalized snapshots and prior episode state.

Primary files:

- `app/services/setup_lifecycle/family_adapters.py`
- `app/services/setup_lifecycle/breakout_adapter.py`
- `app/services/setup_lifecycle/pullback_adapter.py`
- `app/services/setup_lifecycle/vcp_adapter.py`
- `app/services/setup_lifecycle/continuation_adapter.py`
- `app/services/setup_lifecycle/lifecycle_engine.py`
- `app/services/setup_lifecycle/confidence_service.py`
- `tests/setup_lifecycle/test_breakout_lifecycle.py`
- `tests/setup_lifecycle/test_pullback_lifecycle.py`
- `tests/setup_lifecycle/test_vcp_lifecycle.py`
- `tests/setup_lifecycle/test_continuation_lifecycle.py`
- `tests/setup_lifecycle/test_lifecycle_engine.py`

Tasks:

1. Keep pure state-machine functions free of SQLAlchemy models.
2. Implement family-adapter selection:
   - map technical classification and feature flags,
   - compute family evidence,
   - retain all families above tracking threshold,
   - choose primary candidate deterministically.
3. Implement breakout rules:
   - base forming,
   - range contraction,
   - volume dry-up,
   - pivot ready,
   - breakout trigger,
   - follow-through confirmation,
   - extension,
   - failed breakout,
   - expiry.
4. Implement pullback rules:
   - prior uptrend,
   - constructive retreat,
   - selling pressure declining,
   - support approach/test,
   - reversal ready,
   - reversal trigger,
   - follow-through,
   - extension,
   - support break/failure,
   - expiry.
5. Add VCP and continuation adapters after breakout/pullback fixtures are stable.
6. Apply transition precedence:
   `FAILED > EXTENDED > CONFIRMED > TRIGGERED > READY > TIGHTENING > DEVELOPING >
   DISCOVERED > EXPIRED`.
7. Implement hysteresis:
   - separate enter/exit thresholds,
   - minimum duration/persistence,
   - confirmation counters,
   - grace periods,
   - confidence/evidence margins.
8. Support immediate failure and configured persistence for normal progression.
9. Compute confidence from coverage, signal agreement, persistence, freshness/lineage, and
   context completeness.
10. Require at least one reason code and evidence value for every state change.
11. Prefer no state change with reduced confidence when evidence is weak.

Tests:

- Every lifecycle state can be reached through deterministic fixture sequences.
- Ready/tightening oscillation does not flap under hysteresis.
- Failed breakout transitions immediately to `FAILED`.
- Triggered then follow-through reaches `CONFIRMED` after configured sessions.
- Extended state occurs only after ready/triggered/confirmed where configured.
- Missing required evidence keeps or lowers confidence instead of inventing a transition.
- Terminal states never reopen.

Exit criteria:

- Breakout and pullback sequence fixtures pass.
- VCP and continuation either pass or are explicitly disabled as later sub-scope.

## Phase 7: Episode Service, Actionability, and Primary Selection

Goal: open, update, close, rearm, and display setup episodes while keeping actionability
orthogonal to lifecycle state.

Primary files:

- `app/services/setup_lifecycle/episode_service.py`
- `app/services/setup_lifecycle/actionability_policy.py`
- `app/services/setup_lifecycle/confidence_service.py`
- `tests/setup_lifecycle/test_episode_service.py`
- `tests/setup_lifecycle/test_actionability_policy.py`
- `tests/setup_lifecycle/test_primary_episode_selection.py`

Tasks:

1. Lock active episode keys with `SELECT FOR UPDATE` or transaction advisory locks.
2. Open an episode when trackable evidence reaches `DISCOVERED` or stronger and no active
   episode exists for the key.
3. If the first observed state is already `READY` or stronger, open at that state and
   record skipped progression in reasons instead of manufacturing earlier events.
4. On no state change, update current snapshot, age, observation gap, confidence, and
   primary status without creating a transition event.
5. On state or phase change, create an immutable lifecycle event and update episode current
   fields.
6. Close on `FAILED` or `EXPIRED`, preserving terminal reason.
7. Enforce rearm after failed/expired episodes using cooldown or fresh setup evidence.
8. Implement observation-gap aging for filtered universes:
   - absence from one run does not mean failure,
   - use completed trading sessions,
   - expire once the configured gap threshold is exceeded.
9. Select primary active episode by state priority, confidence, setup score, recency, and
   family precedence.
10. Implement actionability policy:
    - `FAILED` is `BLOCKED`,
    - `EXTENDED` is `WATCH_ONLY`,
    - stale/insufficient required features become `LOW_CONFIDENCE` or `BLOCKED`,
    - imminent earnings block without changing lifecycle state,
    - market policy blocks/reduces without changing lifecycle state,
    - ready/triggered/confirmed plus gates pass becomes `ACTIONABLE`,
    - discovered/developing/tightening/expired are `WATCH_ONLY`.

Tests:

- One active episode per family invariant holds under concurrent evaluation.
- State ages reset or retain according to transition type.
- Absence gap closes only after configured threshold.
- Rearm cooldown prevents immediate duplicate episodes.
- Ready with imminent earnings remains `READY` but actionability is `BLOCKED`.
- Primary selection is deterministic when multiple families are active.
- Superseding evaluation marks prior events without deletion.

Exit criteria:

- Lifecycle episode history is immutable, current state is denormalized for queries, and
  actionability gates are explainable.

## Phase 8: Built-In Alerts

Goal: create useful in-app alert events from lifecycle and signal-change sources with
deduplication and cooldowns.

Primary files:

- `app/services/setup_lifecycle/alert_service.py`
- `app/services/setup_lifecycle/repository.py`
- `tests/setup_lifecycle/test_alert_service.py`

Tasks:

1. Seed built-in rules:
   - `NEW_READY`
   - `NEW_TRIGGER`
   - `NEW_CONFIRMATION`
   - `NEW_FAILURE`
   - `NEW_EXTENSION`
   - `SCORE_ACCELERATION`
   - `SECTOR_ACCELERATION`
   - `GATE_BLOCKED`
   - `DATA_DEGRADED`
2. Implement enabled status, severity, scope, cooldown, minimum confidence, setup-family
   restriction, and market restriction.
3. Generate stable alert event keys from rule ID, ticker, episode, source event,
   effective date, and evaluation version.
4. Enforce uniqueness at the database level.
5. Apply cooldown to semantically repeated alerts while allowing opposite crossings and
   new episodes.
6. Implement acknowledge and dismiss actions that update only alert user state.
7. Keep external delivery table out of v1 except as a future migration note.

Tests:

- New ready creates one actionable alert.
- Unchanged ready does not repeat an alert after retry or next day.
- New failure creates risk alert.
- Gate blocked creates risk alert without lifecycle state mutation.
- Cooldown suppresses repeated score acceleration but not a distinct opposite crossing.
- Acknowledge/dismiss does not mutate source events.

Exit criteria:

- Alert events are useful, deduplicated, auditable, and independent from source lifecycle
  history.

## Phase 9: Query APIs, Exports, and Operations Backend

Goal: expose dashboard, timeline, alerts, replay, export, and diagnostics through stable
backend surfaces.

Primary files:

- `app/services/setup_lifecycle/query_service.py`
- `app/services/setup_lifecycle/export_service.py`
- `app/services/setup_lifecycle/replay_service.py`
- `app/routers/setup_lifecycle_routes.py`
- `app/main.py`
- `tests/setup_lifecycle/test_query_service.py`
- `tests/setup_lifecycle/test_export_service.py`
- `tests/setup_lifecycle/test_replay.py`
- `tests/setup_lifecycle/test_routes.py`

Tasks:

1. Add JSON APIs:
   - `GET /api/setup-lifecycle/changes`
   - `GET /api/setup-lifecycle/tickers/{ticker}/timeline`
   - `GET /api/setup-lifecycle/episodes/{episode_id}`
   - `GET /api/setup-lifecycle/alerts`
   - `POST /api/setup-lifecycle/alerts/{alert_id}/acknowledge`
   - `POST /api/setup-lifecycle/alerts/{alert_id}/dismiss`
   - `POST /api/setup-lifecycle/evaluate-run`
   - `POST /api/setup-lifecycle/replay`
   - `GET /api/setup-lifecycle/operations`
   - `GET /api/setup-lifecycle/diagnostics`
2. Add filters:
   - ticker,
   - sector,
   - setup family,
   - lifecycle state,
   - transition,
   - actionability,
   - confidence,
   - state age,
   - score range,
   - score velocity,
   - sector-rank change,
   - market regime,
   - warning flags,
   - alert status/severity.
3. Add sorting:
   - transition priority,
   - confidence,
   - current score,
   - score velocity,
   - state age,
   - trigger distance,
   - sector rank,
   - latest event time.
4. Implement cursor pagination and total counts.
5. Implement exports:
   - filtered changes CSV/JSON,
   - episode CSV/JSON,
   - alert CSV/JSON,
   - operations summary JSON.
6. Implement dry-run replay:
   - returns proposed transitions and alerts without writes.
7. Implement persisted replay:
   - creates a new evaluation version,
   - reuses immutable snapshots,
   - keeps prior event versions.
8. Implement diagnostics:
   - latest canonical date,
   - latest successful evaluation,
   - active episode count,
   - pending jobs,
   - stale lease count,
   - low-confidence share,
   - stale-system warning.
9. Register router in `app/main.py`.

Tests:

- API returns daily transition counts and candidate lists.
- Compound filters and sorting are deterministic.
- Timeline returns snapshots, states, changes, blockers, and source links.
- Exports contain stable schemas and honor filters.
- Dry-run replay writes nothing.
- Persisted replay creates a parallel event version.
- Diagnostics expose stale lifecycle evaluation state.

Exit criteria:

- Backend/API surfaces support the daily cockpit, ticker timeline, alert center, operations,
  exports, and replay.

## Phase 10: UI, Navigation, and HTMX Interactions

Goal: make SLSE usable as an operational daily change cockpit.

Primary files:

- `app/templates/setup_lifecycle.html`
- `app/templates/setup_lifecycle_ticker.html`
- `app/templates/setup_lifecycle_episode.html`
- `app/templates/setup_lifecycle_alerts.html`
- `app/templates/setup_lifecycle_operations.html`
- `app/templates/partials/_nav.html`
- `app/templates/run_detail.html`
- `app/static/setup_lifecycle.js`
- `app/static/app.css`
- `tests/setup_lifecycle/test_routes.py`

Tasks:

1. Add navigation to Market Changes and Alert Center.
2. Add run-detail links/status for SLSE evaluation when available.
3. Build Market Changes page:
   - selected date,
   - transition counts,
   - quick filters,
   - candidate table,
   - state/actionability/confidence badges,
   - score and sector velocity columns,
   - stale-system warning,
   - export links.
4. Build ticker lifecycle page:
   - primary episode summary,
   - secondary active episodes,
   - state ribbon,
   - chronological timeline,
   - previous/current metric comparison,
   - blockers and warning flags,
   - source links.
5. Build episode detail page:
   - all snapshots and lifecycle events for one family episode,
   - terminal reason,
   - superseded event labeling.
6. Build alert center:
   - unread/actionable/risk filters,
   - acknowledge/dismiss HTMX actions,
   - source links.
7. Build evaluation operations page:
   - run status,
   - replay form,
   - counts,
   - warnings,
   - errors,
   - config and engine versions.
8. Use server-side lifecycle computation only. HTMX updates filters, row expansion, alert
   state, and pagination.
9. Clearly label reconstructed, stale, low-coverage, noncanonical, and superseded records.
10. Keep copy research-oriented and avoid any trade/order language.

Tests:

- Templates render with full data, missing context, empty state, and stale data.
- Filters update candidate table and counts.
- Row expansion loads evidence lazily.
- Alert acknowledgement updates only the alert row.
- Timeline pagination works.
- Reconstructed and superseded labels render.
- Navigation links are present.

Exit criteria:

- A user can identify newly ready/triggered/failed/extended candidates, inspect why they
  changed, and manage in-app alerts without page reload churn.

## Phase 11: Replay, Maintenance, Repair, and OWPE Bridge

Goal: harden long-running workflows and provide stable lifecycle features for future
probability work.

Primary files:

- `app/services/setup_lifecycle/replay_service.py`
- `app/services/setup_lifecycle/maintenance_service.py`
- `app/services/setup_lifecycle/export_service.py`
- `app/services/setup_lifecycle/job_handlers.py`
- `app/services/winner_probability/feature_extractor.py`
- `tests/setup_lifecycle/test_replay.py`
- `tests/setup_lifecycle/test_maintenance.py`
- `tests/setup_lifecycle/test_outcome_bridge.py`

Tasks:

1. Add job types:
   - `SETUP_LIFECYCLE_REPLAY`
   - `SETUP_LIFECYCLE_REPAIR_TICKER`
   - `SETUP_LIFECYCLE_DAILY_MAINTENANCE`
   - `SETUP_ALERT_REBUILD`
2. Daily maintenance:
   - age missing observations,
   - expire stale episodes,
   - refresh summary counts,
   - skip non-completed market sessions.
3. Repair ticker:
   - retry one ticker/family/date after data correction,
   - preserve prior versions,
   - expose repair counts.
4. Alert rebuild:
   - re-evaluate alert rules from source events,
   - do not change lifecycle history.
5. Replay version comparison:
   - changed state dates,
   - added/removed events,
   - alert differences,
   - changed primary episode.
6. OWPE bridge:
   - export lifecycle state, phase, transition type, state age, signal velocity,
     actionability, confidence, episode ID, and source event links as point-in-time
     features,
   - do not require OWPE to be enabled,
   - do not join mutable source state during feature export.

Tests:

- Maintenance expires absent episodes exactly once.
- Repair is idempotent and scoped.
- Alert rebuild deduplicates.
- Replay comparison is deterministic.
- OWPE feature export works from immutable SLSE records and excludes future evidence.

Exit criteria:

- SLSE can be maintained, repaired, replayed, and consumed as a stable feature source.

## Phase 12: Rollout, Documentation, and Performance

Goal: enable the subsystem safely and prove it meets acceptance criteria.

Primary files:

- `README.md`
- `docs/setup_lifecycle_signal_change_engine.md`
- `docs/release_notes_setup_lifecycle_signal_change_engine.md`
- `tests/setup_lifecycle/test_acceptance_fixture.py`
- `tests/setup_lifecycle/test_performance.py`

Tasks:

1. Document configuration fields, default thresholds, state semantics, and actionability.
2. Document pipeline behavior:
   - disabled flags,
   - shadow capture,
   - lifecycle preview,
   - dashboard,
   - alerts,
   - replay.
3. Document route/API/export endpoints.
4. Document v1 limitations:
   - daily close trigger,
   - no intraday,
   - no external alert delivery,
   - reconstructed history is separate,
   - research-only and no order placement.
5. Add golden fixtures for:
   - clean breakout,
   - failed breakout,
   - clean bull pullback,
   - deteriorating pullback,
   - VCP,
   - extended momentum,
   - choppy score oscillation,
   - missing-data sequence,
   - market-gate block,
   - filtered-universe observation gap.
6. Run performance checks:
   - capture 1,000 tickers with batch inserts,
   - canonicalize affected dates with indexed queries,
   - evaluate episodes in deterministic batches,
   - dashboard query under the configured p95 target.
7. Stage rollout:
   - shadow capture,
   - lifecycle preview,
   - dashboard release,
   - alert release,
   - replay release,
   - OWPE bridge.

Recommended verification:

```powershell
ruff check app tests
pytest tests/setup_lifecycle -q
pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q
pytest tests/test_background_job_service.py tests/test_background_worker.py -q
pytest -q
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

Manual verification:

1. Start the app locally.
2. Run a full pipeline with SLSE disabled and confirm existing behavior is unchanged.
3. Enable shadow capture and confirm snapshots are created without visible UI changes.
4. Enable lifecycle preview and inspect ticker timelines.
5. Enable dashboard and filter newly `READY` and `TRIGGERED` candidates.
6. Enable alerts and verify no-repeat behavior across repeated evaluations.
7. Run dry-run replay and confirm no persisted events change.
8. Run persisted replay and confirm a new evaluation version appears.
9. Export filtered changes and timeline evidence.
10. Confirm no route or UI can place, stage, or imply broker orders.

Exit criteria:

- All acceptance criteria are satisfied or explicitly waived in release notes.
- Feature flags support rollback at each rollout stage.
- Full regression and migration checks pass.

## Test Matrix

Core unit tests:

- Config validation and hash stability.
- Signal normalizers for every value type and direction.
- Threshold entry, exit, and no-repeat behavior.
- Velocity windows and insufficient-history behavior.
- Breakout, pullback, VCP, continuation, and generic evidence.
- Every lifecycle state and allowed transition.
- Hysteresis boundaries and persistence counters.
- Confidence and actionability invariants.
- Observation gaps, expiry, failure, rearm, and primary selection.

Sequence and property tests:

- Developing to tightening to ready to triggered to confirmed creates ordered events.
- Ready distance oscillation does not flap.
- Triggered followed by high-volume reversal fails immediately.
- Missing one filtered run keeps an episode active.
- Missing beyond configured sessions expires once.
- Earnings gate blocks actionability without changing lifecycle.
- Same-day source revision creates snapshot and canonical audit, not destructive rewrite.
- Repeated job execution produces stable counts.
- Random generated sequences preserve terminal immutability and active-episode uniqueness.

Integration tests:

- Alembic migration up/down/up with PostgreSQL JSONB, constraints, partial indexes, and locks.
- Full source-run fixture through capture, canonicalization, changes, episodes, alerts, API,
  templates, and exports.
- Multiple runs per trading date and market-holiday boundaries.
- Background worker retry, heartbeat, cancellation, stale recovery, and partial failure.
- Pipeline `PARTIAL` behavior when SLSE fails after upstream scoring succeeds.
- Export round-trip and stable JSON schema.

Performance tests:

- 1,000-ticker capture and evaluation meets configured target or records a documented exception.
- Dashboard query returns within configured p95 target.
- Timeline query uses cursor pagination.
- JSONB-heavy evidence stays out of high-frequency filters unless promoted to columns.

Security and safety tests:

- No SLSE path imports or calls an order-placement client.
- Replay accepts structured validated configuration only.
- Error responses redact credentials, SQL, stack traces, and filesystem paths.
- UI uses research-only labels and no trading commands.

## Requirement Traceability

- Snapshot capture and canonicalization: Phases 1 through 4 cover `SLSE-FR-001` through
  `SLSE-FR-015`.
- Signal-change detection: Phase 5 covers `SLSE-FR-020` through `SLSE-FR-028`.
- Lifecycle episodes and transitions: Phases 6 and 7 cover `SLSE-FR-030` through
  `SLSE-FR-042`.
- In-app alerts: Phase 8 covers `SLSE-FR-050` through `SLSE-FR-055`.
- Dashboard, filters, timeline, and export: Phases 9 and 10 cover `SLSE-FR-060` through
  `SLSE-FR-068`.
- Administration and replay: Phases 4, 9, and 11 cover `SLSE-FR-070` through
  `SLSE-FR-075`.
- Business rules, nonfunctional requirements, security, observability, and performance are
  cross-cutting across all phases and must be mapped to concrete tests before release.

Release rule:

- Every `Must` requirement must map to at least one implementation artifact and one passing
  test, or to an explicit approved waiver in the release notes.
- Every material transition shown to the user must be reproducible from immutable snapshots,
  configuration version, and source evidence.
- No pipeline capture should be enabled while schema, config, snapshot capture,
  canonicalization, and idempotency tests are incomplete.

## Recommended Pull Request Order

1. PR 1: Phase 0 feature flags, pipeline placeholders, semantic decisions, and baseline.
2. PR 2: Phase 1 config, enums, DTOs, and signal registry.
3. PR 3: Phase 2 persistence model, migration, and repository.
4. PR 4: Phase 3 source loader and immutable snapshot capture.
5. PR 5: Phase 4 canonicalization, evaluation runs, job handlers, and pipeline wiring.
6. PR 6: Phase 5 change detector, velocity, and material event persistence.
7. PR 7: Phase 6 family adapters, confidence, and pure lifecycle engine.
8. PR 8: Phase 7 episode service, actionability policy, observation gaps, and primary selection.
9. PR 9: Phase 8 built-in alerts and alert state APIs.
10. PR 10: Phase 9 query APIs, exports, operations, and replay backend.
11. PR 11: Phase 10 UI, navigation, timelines, alert center, and operations page.
12. PR 12: Phase 11 maintenance, repair, replay comparison, and OWPE bridge.
13. PR 13: Phase 12 documentation, performance tests, acceptance fixtures, and staged rollout.

## Rollback Strategy

- Keep all SLSE routes, jobs, alerts, and pipeline steps behind feature flags.
- If capture has a defect, disable `SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED` and leave existing
  SwingLens run outputs unaffected.
- If lifecycle evaluation has a defect, keep shadow snapshots but disable visible states,
  alerts, and replay.
- If alerts are noisy, disable `SETUP_LIFECYCLE_ALERTS_ENABLED` without deleting source
  lifecycle or change events.
- Migration downgrade removes only SLSE tables and seeded alert rules.
- Existing combined results, ranking results, market-regime snapshots, sector-rotation
  snapshots, and OWPE tables remain unchanged.

## Deferred or Optional Scope

Defer unless the core daily lifecycle is stable:

- Intraday lifecycle evaluation.
- External notifications.
- User-defined rule builder.
- Catalyst/news/event extraction.
- Empirically optimized transition thresholds.
- Automatic historical reconstruction.
- Portfolio-aware watchlists or exposure limits.
- Any broker-order, position-management, or auto-trading capability.

## Definition of Done

SLSE is done when SwingLens can capture immutable setup snapshots for completed runs,
canonicalize ticker-day records, detect material changes, evaluate setup lifecycle episodes,
separate lifecycle from actionability, create deduplicated in-app alerts, expose daily
changes and ticker timelines, export evidence, replay with versioning, survive retries and
partial ticker failures, feed stable lifecycle features to OWPE, pass focused and full
regression tests, and remain strictly research-only.

Version 1.1 completion additionally requires explicit data-quality labels, all five setup
families, exact SRS HTML/API contracts, stable error and null semantics, replay authority
controls, retention and purge safeguards, named operational metrics, the 60-second and
500-ms performance targets, accessibility verification, and complete AC-01 through AC-15
coverage.
