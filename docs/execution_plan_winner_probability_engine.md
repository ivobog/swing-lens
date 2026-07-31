# SwingLens Execution Plan: Outcome-Calibrated Winner Probability Engine

**Document version:** 1.1  
**Status:** Updated implementation plan  
**Last updated:** 2026-07-31  
**Feature:** Outcome-Calibrated Winner Probability Engine (OWPE)

Source documents:

- `SwingLens_Outcome_Calibrated_Winner_Probability_Engine_SRS.docx`
- `SwingLens_Outcome_Calibrated_Winner_Probability_Engine_SDD.docx`

This plan implements the Outcome-Calibrated Winner Probability Engine (OWPE) as an additive evidence layer. Existing scores, ranking profiles, market regime, sector rotation, and run pages remain authoritative; OWPE captures their point-in-time state, matures future outcomes, and displays transparent probability evidence without mutating existing ranks or enabling trading.

Version 1.1 closes the gaps found during the SRS/SDD coverage review. In particular, it makes decision-time estimation an explicit capture-time workflow, hardens background-job leases before long-running OWPE work, materializes pending outcome rows, persists exact evidence membership, expands filters and as-of APIs, and adds the Outcome Explorer, drift monitoring, reproduction, model retirement, and retention rules.

## Current Repo Baseline

- The app is a local FastAPI/Jinja2/HTMX/PostgreSQL application with SQLAlchemy models in `app/models/tables.py`.
- Existing source entities already cover most OWPE inputs: `UploadRun`, `RawCompanyRow`, `PriceBar`, `FundamentalScore`, `TechnicalScore`, `CombinedResult`, `RankingResult`, `MarketRegimeSnapshot`, and `SectorRotationSnapshot`.
- Durable background jobs already exist through `BackgroundJob`, `app/services/background_job_service.py`, `app/services/background_worker.py`, and `app/worker.py`.
- The full pipeline is already persisted through `PipelineRun`, `PipelineStep`, `app/services/pipeline_service.py`, and `app/services/pipeline_executor.py`.
- `PIPELINE_STEP_NAMES` currently ends with `SECTOR_ROTATION_SNAPSHOT`; OWPE adds `CAPTURING_WINNER_PREDICTIONS` after that step.
- `app/services/us_market_calendar.py` provides latest-completed-session logic, but OWPE needs additional next-session and Nth-session helpers.
- The current Alembic head must be resolved from the repository immediately before implementation. The OWPE migration must use the actual current head as `down_revision`; example filenames in this plan are illustrative only.
- Existing execution plans favor dependency-ordered phases with clear primary files, tests, and exit criteria. This plan follows that pattern.

## V1 Product Decisions

Use these decisions to keep the first implementation coherent:

1. Use the hierarchical Beta-Binomial cohort estimator as the production v1 probability source.
2. Persist a decision-time estimate for every captured prediction and configured production outcome definition. Never replace it retroactively with a later re-score.
3. Distinguish estimate kinds explicitly: `DECISION_TIME` and `LATEST_RESCORE`. A latest re-score is additive and can never mutate the original decision-time estimate.
4. Default label is `+2.5% target before -2.0% stop within 5 sessions`, using next regular-session open as entry.
5. Count the entry session as session 1. A five-session horizon therefore covers entry day plus the following four completed US sessions. This convention is locked in configuration and fixture tests.
6. Treat same-bar target/stop conflicts as explicit ambiguity and default the production label to conservative stop-first.
7. Materialize one pending forward-outcome row per configured entry model and horizon at prediction capture. Materialize configured target/stop evaluation rows or definitions at the same time.
8. Capture all eligible predictions after sector rotation as a nonfatal final pipeline step, but do not enable production pipeline capture until the decision-time estimator and lease hardening are complete.
9. Persist exact evidence membership for every probability estimate through immutable membership rows or a content-addressed evidence manifest. A query definition alone is not sufficient for audit reproduction.
10. Keep similarity neighbors and ML models as supporting or shadow functionality until the cohort baseline is stable.
11. Display/filter OWPE evidence beside existing ranks; do not modify `CombinedResult` or `RankingResult` scores.
12. Show `Insufficient` when evidence rules fail; do not fake neutral probabilities. Persist the `Insufficient` decision-time record and its reasons.
13. Store universe and screener provenance and make results conditional on the historical SwingLens screening universe.
14. Keep `NEXT_OPEN` and `SIGNAL_CLOSE_DIAGNOSTIC` outcomes completely separate in storage, statistics, APIs, and UI labels.
15. Use dedicated OWPE processing, training, and lifecycle audit tables. `BackgroundJob` remains the execution mechanism but is not the complete domain audit record.
16. Retain immutable predictions, outcome revisions, decision-time estimates, evidence manifests, models, and lifecycle events indefinitely unless a future documented retention policy explicitly supersedes this rule.
17. Prohibit any order-placement integration or trade button from this feature.

## Phase 0: Preparation, Decisions, and Guard Rails

Goal: settle irreversible semantics, harden the worker, and make OWPE easy to verify, stage, reproduce, and roll back.

Implementation record:

- Branch: `codex/winner-probability-engine`.
- Baseline checks captured on 2026-07-31: `pytest -q` passed with 526 tests and 1 warning; `ruff check app tests` passed.
- Actual Alembic head before Phase 0 changes: `0014_sector_metadata`.
- Phase 0 lease-hardening migration uses `0014_sector_metadata` as `down_revision`.
- Phase 0 keeps all OWPE feature flags disabled by default.
- Initial OWPE persistence decision: keep OWPE database models in `app/models/tables.py` through the first schema pass, then split to `app/models/winner_probability_tables.py` only if the table set starts to crowd unrelated model ownership.

Primary files:

- `docs/execution_plan_winner_probability_engine.md`
- `app/settings.py`
- `.env.example`
- `app/services/background_job_service.py`
- `app/services/background_worker.py`
- `app/worker.py`
- `tests/test_settings.py`
- `tests/test_background_job_service.py`
- `tests/test_background_worker.py`

Tasks:

1. Create a branch, for example `codex/winner-probability-engine`.
2. Capture baseline checks:
   ```powershell
   pytest -q
   ruff check app tests
   ```
3. Resolve and record the actual current Alembic head. Do not assume the example revision number in this document is still current.
4. Add feature flags/settings:
   - `WINNER_PROBABILITY_ENABLED=false`
   - `WINNER_PROBABILITY_CAPTURE_IN_PIPELINE=false`
   - `WINNER_PROBABILITY_CONFIG_PATH=config/winner_probability.yaml`
   - `WINNER_PROBABILITY_ADMIN_ENABLED=false`
5. Lock the production semantics before schema implementation:
   - entry session counts as horizon session 1,
   - `NEXT_OPEN` is the production entry model,
   - `SIGNAL_CLOSE_DIAGNOSTIC` is separate diagnostic evidence,
   - same-bar target/stop production result is conservative stop-first,
   - pending outcomes are materialized at capture,
   - decision-time estimates are created at capture using a strict earlier training cutoff,
   - exact evidence membership is persisted,
   - reconstructed history is excluded from production training by default.
6. Define estimate views and as-of semantics:
   - `DECISION_TIME`: evidence available at the prediction cutoff,
   - `LATEST_RESCORE`: newest eligible evidence,
   - `AS_OF`: records and revisions visible at an explicit historical cutoff,
   - `CURRENT`: latest active revisions.
7. Define the evidence-membership persistence approach. Preferred v1 design: `winner_estimate_evidence_members` rows containing estimate ID, prediction ID, outcome ID, outcome revision, episode ID, inclusion weight, and inclusion cutoff. For very large samples, also support a compressed content-addressed manifest and hash.
8. Define retention classes:
   - permanent: prediction snapshots, outcome revisions, decision-time estimates, evidence membership/manifests, model/cohort versions, training runs, lifecycle events;
   - rebuildable/configurable: neighbor caches, temporary aggregates, export files, operational logs after the configured retention window.
9. Keep administrative mutation paths local-only, following the existing app security boundary.
10. Decide whether OWPE models live in `app/models/tables.py` initially or a new `app/models/winner_probability_tables.py` exposed through `app.models`.

### Phase 0A: Background-Job Lease Hardening

Long-running OWPE jobs must not rely on a lease that is acquired once and never renewed. Harden the existing worker before enabling outcome maturation, backfill, cohort refresh, model training, or similarity caching.

Tasks:

1. Add or formalize lease fields:
   - `lease_owner`,
   - `execution_token` or monotonically increasing fencing token,
   - `locked_at`,
   - `heartbeat_at`,
   - `lease_expires_at`.
2. Renew heartbeats during long-running handlers and before/after each bounded batch.
3. Use compare-and-set updates that include the current execution/fencing token.
4. Prevent an old worker from committing completion or results after another worker has recovered the job.
5. Recover jobs only after actual lease expiry, not merely after elapsed wall-clock time from initial lock.
6. Ensure cancellation is observed at every heartbeat/batch boundary.
7. Persist lease-loss and recovery events in operational metadata.

Tests:

- A live heartbeat prevents stale recovery.
- An expired lease can be recovered exactly once.
- A recovered job receives a new execution/fencing token.
- An old worker cannot commit after its lease is replaced.
- Duplicate workers cannot both mark the same job complete.
- Cancellation stops the handler before the next bounded batch.

Exit criteria:

- Baseline test status is known.
- New settings are loadable and covered by tests.
- Horizon, entry, estimate-kind, as-of, evidence-membership, and retention semantics are locked and documented.
- Background-job leases use heartbeats and fencing and are safe for OWPE long-running jobs.
- No existing run, ranking, market-regime, or sector-rotation behavior changes yet.

## Phase 1: Configuration and Feature Schema

Goal: define validated OWPE behavior before adding database writes.

Primary files:

- `config/winner_probability.yaml`
- `app/services/winner_probability/config.py`
- `app/services/winner_probability/feature_schema.py`
- `app/services/winner_probability/dtos.py`
- `tests/winner_probability/test_config.py`
- `tests/winner_probability/test_feature_schema.py`

Tasks:

1. Add `config/winner_probability.yaml` with:
   - engine enablement and feature schema version,
   - calculation version,
   - production entry model `NEXT_OPEN`,
   - optional `SIGNAL_CLOSE_DIAGNOSTIC`,
   - explicit entry-day-inclusive horizon convention,
   - horizons `[1, 3, 5, 10, 20]`,
   - pending-outcome materialization policy,
   - estimate kinds `DECISION_TIME` and `LATEST_RESCORE`,
   - primary outcome definition `T2_5_S2_0_H5_NEXT_OPEN`,
   - conservative same-bar conflict policy,
   - episode cooldown rules,
   - cohort hierarchy and minimum sample thresholds,
   - prior strength, evidence-grade thresholds, and rolling-window defaults,
   - exact evidence-manifest policy,
   - cold-start raw-evidence display rules,
   - drift windows and alert thresholds,
   - retention classes,
   - API as-of defaults and maximum query windows.
2. Implement frozen config dataclasses and a strict loader.
3. Implement `FeatureSchemaRegistry` for `owpe-features-1.0.0`.
4. Include feature metadata:
   - name,
   - type,
   - source path,
   - point-in-time availability rule,
   - missingness policy,
   - normalization rule,
   - categorical vocabulary,
   - indexed-core-column flag.
5. Validate percentages, horizons, horizon-counting convention, unique primary outcome definition, estimate kinds, entry models, model/cohort status values, drift thresholds, and feature-schema compatibility at startup.
6. Validate that production and diagnostic entry models cannot share an outcome definition identifier.
7. Compute a normalized config hash that can be stored with snapshots, pending outcomes, cohorts, estimates, evidence manifests, processing runs, and models.
8. Define typed filter DTOs for probability, lower bound, interval width, expected/median return, MFE, MAE, target-first rate, evidence grade, effective sample size, earnings risk, and data quality.

Tests:

- Valid default YAML loads.
- Invalid percentages, duplicate primary definitions, unordered evidence thresholds, and unknown feature names fail with actionable errors.
- Config hash is stable for semantically identical input.
- Feature schema rejects unavailable or future-dated feature sources.
- Entry-day-inclusive horizon fixtures are unambiguous.
- Production and diagnostic entry-model definitions cannot collide.
- Filter and drift configuration rejects invalid or contradictory thresholds.

Exit criteria:

- OWPE behavior can be loaded and validated without database migrations.
- The default primary label and horizon convention are explicit and tested.

## Phase 2: Persistence Model and Migration

Goal: create the append-only storage required for snapshots, pending and matured outcomes, exact evidence membership, cohorts, estimates, models, calibration, similarity, drift, and processing lineage.

Primary files:

- `app/models/tables.py` or `app/models/winner_probability_tables.py`
- `alembic/versions/<next_revision>_add_winner_probability_engine.py`
- `tests/winner_probability/test_schema.py`

Tasks:

1. Add prediction tables:
   - `winner_prediction_snapshots`,
   - `winner_prediction_episodes`.
2. Add outcome tables:
   - `winner_forward_outcomes`,
   - `winner_target_stop_outcomes`,
   - `winner_outcome_definitions`.
3. Add probability, cohort, and evidence tables:
   - `winner_cohort_definitions`,
   - `winner_cohort_statistics`,
   - `winner_probability_estimates`,
   - `winner_estimate_evidence_members`,
   - optional `winner_evidence_manifests` for compressed/content-addressed large manifests.
4. Add model, calibration, and drift tables:
   - `winner_model_versions`,
   - `winner_calibration_bins`,
   - `winner_drift_metrics`.
5. Add operational and governance tables:
   - `winner_processing_runs`,
   - `winner_model_training_runs`,
   - `winner_model_lifecycle_events`,
   - `winner_similarity_links`.
6. Use `BigInteger` primary keys, `JSONB` for structured payloads, explicit indexes, and SQLAlchemy 2 typed conventions.
7. Store core prediction columns needed for filtering and audit, including:
   - `prediction_as_of_date`,
   - `source_data_cutoff_at`,
   - `captured_at`,
   - `planned_entry_session`,
   - entry schedule/data status,
   - run/ticker/source IDs,
   - setup family/classification/profile,
   - fundamental/technical/combined scores,
   - market regime/risk state,
   - sector state/rank,
   - suggested target/stop and reward-risk,
   - earnings date/risk/days,
   - technical data quality and fundamental coverage,
   - universe and screener provenance,
   - feature/config/schema versions and hashes.
8. Add prediction indexes:
   - `(run_id, ticker)`,
   - `(ticker, prediction_as_of_date DESC)`,
   - `eligibility_status`,
   - classification/profile/regime/sector-state filters,
   - earnings-risk and data-quality filters.
9. Materialized pending outcome rows must include due-session metadata and status. Add outcome indexes:
   - `(status, due_session)`,
   - `(prediction_id, entry_model, horizon_sessions)`,
   - current-revision partial indexes,
   - `(source_bar_lineage_hash, is_current_revision)` where useful.
10. Add estimate indexes:
   - `(prediction_id, outcome_definition_id, estimate_kind)`,
   - `(model_version_id, created_at)`,
   - probability/lower-bound/evidence-grade/effective-n filter indexes where query plans justify them.
11. Add exact evidence-membership indexes:
   - `(estimate_id, outcome_id)`,
   - `(estimate_id, episode_id)`,
   - `(prediction_id, included_as_of)`.
12. Add uniqueness rules:
   - prediction natural identity: `(run_id, ticker, prediction_as_of_date, feature_schema_version, revision)`,
   - only one active non-superseded prediction revision,
   - outcome identity: `(prediction_id, entry_model, horizon_sessions, revision)`,
   - target/stop identity: `(prediction_id, outcome_definition_id, revision)`,
   - one active current outcome revision per logical outcome,
   - one estimate per `(prediction_id, outcome_definition_id, estimate_kind, source_version, training_cutoff)`,
   - no duplicate evidence member per `(estimate_id, outcome_id, outcome_revision)`.
13. Add string enums/constants unless the repository already standardizes database enums:
   - `PredictionEligibility`,
   - `EntryScheduleStatus`,
   - `EntryDataStatus`,
   - `OutcomeStatus`,
   - `EntryModel`,
   - `FirstEvent`,
   - `EstimateKind`,
   - `EstimateSource`,
   - `EvidenceGrade`,
   - `ModelStatus`,
   - `ProcessingStatus`,
   - `LifecycleEventType`.
14. Define model artifact storage:
   - JSONB for regularized logistic-regression coefficients, intercept, preprocessing, vocabularies, calibration parameters, and dependency versions,
   - file/artifact path plus hash, format, and size for larger approved models,
   - never rely on an unversioned local pickle.
15. Encode retention class or retention-policy metadata for operational/rebuildable data while treating immutable research evidence as permanent.

Tests:

- SQLAlchemy metadata includes all tables, constraints, partial indexes, and relationships.
- Alembic upgrade/downgrade/up succeeds in the project’s real test-database pattern.
- Append-only revision constraints prevent accidental destructive overwrite.
- Duplicate decision-time estimates and duplicate evidence memberships are rejected.
- Pending outcome natural identities are idempotent.
- Model artifacts require schema, dependency, and hash metadata.

Exit criteria:

- The schema can represent immutable captures, materialized pending outcomes, multiple horizons, target/stop scenarios, decision-time and latest estimates, exact evidence membership, model lifecycle, calibration and drift metrics, processing/training lineage, similarity, and revisions.

## Phase 3: Prediction Capture, Episodes, and Pending Outcomes

Goal: persist immutable point-in-time snapshots, assign dependency episodes, materialize pending outcomes, and define the capture-time decision-estimate contract. Production pipeline capture remains disabled until Phase 6 is complete.

Primary files:

- `app/services/winner_probability/feature_extractor.py`
- `app/services/winner_probability/capture_service.py`
- `app/services/winner_probability/episode_service.py`
- `app/services/winner_probability/pending_outcome_service.py`
- `app/services/winner_probability/decision_time_estimate_service.py`
- `app/services/winner_probability/repository.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_executor.py`
- `tests/winner_probability/test_capture_service.py`
- `tests/winner_probability/test_episode_service.py`
- `tests/winner_probability/test_pending_outcome_service.py`
- `tests/winner_probability/test_decision_time_estimate_contract.py`
- `tests/test_pipeline_service.py`
- `tests/test_pipeline_executor.py`

Tasks:

1. Extend `PIPELINE_STEP_NAMES` with `CAPTURING_WINNER_PREDICTIONS` after `SECTOR_ROTATION_SNAPSHOT`.
2. Add `PipelineStatus.CAPTURING_WINNER_PREDICTIONS`.
3. Keep `WINNER_PROBABILITY_CAPTURE_IN_PIPELINE=false` until the Phase 6 estimator can persist a decision-time estimate or explicit `Insufficient` record during the same logical capture workflow.
4. Build a ticker-indexed context map for each completed run:
   - raw/upload row,
   - fundamental score,
   - technical score,
   - combined result,
   - ranking result(s),
   - market regime snapshot,
   - sector rotation snapshot/row.
5. Validate point-in-time readiness:
   - signal source bar is a completed session,
   - source timestamps are not later than the prediction cutoff,
   - optional context is captured as null plus warning when missing,
   - no future-dated source value can enter the feature payload.
6. Distinguish entry scheduling from entry data availability:
   - `entry_schedule_status = RESOLVED | UNRESOLVED`,
   - `entry_data_status = NOT_DUE | PENDING | AVAILABLE | MISSING | INVALID`.
   A future next-open bar is normally `NOT_DUE` or `PENDING`, not an exclusion.
7. Capture source IDs and all mandatory denormalized fields:
   - prediction date, source cutoff, capture time, planned entry session,
   - scores/ranks/classification/profile,
   - regime and sector context,
   - suggested target/stop and reward-risk,
   - earnings risk/date/days,
   - technical data quality and fundamental coverage,
   - universe/screener provenance and source run metadata,
   - adjustment basis and data lineage.
8. Build canonical feature JSON:
   - ordered keys,
   - normalized decimals/dates,
   - explicit missing values,
   - config/schema/calculation versions.
9. Compute deterministic `feature_vector_hash`.
10. Assign episode IDs using ticker, setup family, trigger state, and cooldown sessions. Mark dependent predictions explicitly; do not silently delete them.
11. Classify each snapshot as `ELIGIBLE` or `EXCLUDED`, with explicit reasons such as insufficient completed bars, stale signal data, invalid signal price, unresolved trading calendar, prohibited reconstructed source, or unrecoverable source conflict. Do not use a not-yet-available future entry bar as a capture exclusion.
12. Materialize pending outcome rows immediately after an eligible capture:
   - one `winner_forward_outcome` per configured entry model and horizon,
   - `status=PENDING`,
   - planned entry session and due horizon session,
   - idempotent identity by prediction, entry model, and horizon,
   - one linked target/stop evaluation row or configured definition reference per enabled target/stop scenario.
13. Define the decision-time estimate contract:
   - estimate cutoff equals the prediction source cutoff,
   - all evidence outcomes must be fully mature strictly before that cutoff,
   - current prediction and future predictions are prohibited,
   - dependent episodes follow the configured one-representative rule,
   - create `DECISION_TIME` estimate or persisted `Insufficient` result in the same capture transaction/workflow,
   - exact evidence membership is persisted,
   - no later job may update this record.
14. During incremental development before Phase 6, use a test stub for the decision-time-estimate interface and keep production capture disabled. Do not create live predictions without an estimate contract.
15. Implement idempotent capture:
   - repeated identical capture returns the existing active snapshot, pending outcomes, and decision-time estimate,
   - non-identical active capture fails with a conflict unless an explicit correction path creates a new revision.
16. Keep capture nonfatal per ticker; return inserted, duplicate, excluded, failed, warning, pending-outcome, and estimate-status counts.

Tests:

- Completed run creates one snapshot per eligible ticker.
- Repeated pipeline execution creates no duplicate active snapshot, pending outcome, target/stop row, or decision-time estimate.
- Historical source mutation does not change an already captured feature JSON or hash.
- Missing optional regime/sector context is represented by null values and warnings.
- Consecutive same-ticker/setup signals share an episode or are marked dependent.
- A future next-open bar is `NOT_DUE/PENDING`, not excluded.
- Each eligible snapshot creates all configured pending horizon rows with correct due sessions.
- The decision-time contract rejects evidence maturing at or after the prediction cutoff.
- Pipeline step appears in new pipeline runs and the executor marks it completed/partial correctly.

Exit criteria:

- A completed run can produce immutable, auditable OWPE snapshots with deterministic hashes, episode assignments, and materialized pending outcomes.
- The capture service has a mandatory decision-time-estimate interface.
- Production pipeline capture remains disabled until the real Phase 6 implementation satisfies that interface.

## Phase 4: Durable Jobs and Administrative Triggers

Goal: connect OWPE work to the hardened durable worker without creating a second orchestration system or exposing handlers before their services exist.

Primary files:

- `app/services/background_worker.py`
- `app/services/winner_probability/job_handlers.py`
- `app/routers/winner_probability_routes.py`
- `app/main.py`
- `tests/winner_probability/test_job_handlers.py`
- `tests/winner_probability/test_routes_admin.py`

Tasks:

1. Add job types incrementally when their implementation phase is complete:
   - `WINNER_PREDICTION_CAPTURE`,
   - `WINNER_OUTCOME_MATURATION`,
   - `WINNER_OUTCOME_REVISION_CHECK`,
   - `WINNER_COHORT_REFRESH`,
   - `WINNER_MODEL_TRAINING`,
   - `WINNER_SIMILARITY_CACHE`.
2. Do not register a callable production handler for an unimplemented service. A disabled placeholder must fail closed with `FEATURE_NOT_ENABLED`; it must not report success.
3. Register completed handlers in `default_job_handlers()`.
4. Reuse hardened `BackgroundJob` leases, heartbeats, fencing, cancellation, retries, stale recovery, and result JSON.
5. Persist a corresponding `winner_processing_runs` domain record for every OWPE job, including operation type, configuration hash, source cutoff, checkpoints, counts, duration, status, and error summary.
6. Add local-only administrative endpoints as services become available:
   - `POST /api/winner-probability/runs/{run_id}/capture`,
   - `POST /api/winner-probability/outcomes/process`,
   - `POST /api/winner-probability/outcomes/revisions/check`,
   - `POST /api/winner-probability/cohorts/refresh`,
   - `POST /api/winner-probability/models/train`,
   - `POST /api/winner-probability/models/{id}/promote`,
   - `POST /api/winner-probability/models/{id}/retire`,
   - `POST /api/winner-probability/similarity/cache`.
7. Ensure job handlers are idempotent and use bounded commits with cancellation/heartbeat checks.
8. Record processing counts, checkpoints, durations, errors, config hash, source cutoffs, execution token, and evidence/model version where applicable.
9. Make model promotion and retirement explicit local administrative actions with actor and reason.

Tests:

- Each administrative endpoint queues the correct implemented job type and payload.
- Unimplemented/disabled handlers fail closed rather than silently succeeding.
- Unknown or invalid outcome definitions return structured 400/422 errors.
- Cancellation requests stop before the next bounded batch.
- Per-ticker failures produce `PARTIAL` work, not whole-job failure.
- Every OWPE background job creates and updates a domain processing-run record.
- Promotion and retirement require actor/reason and are auditable.

Exit criteria:

- Implemented OWPE operations can be triggered by pipeline or manually through durable, heartbeat-protected jobs.
- No OWPE long-running task depends on in-memory process state.
- No endpoint advertises a service that is not implemented or enabled.

## Phase 5: Outcome Maturation

Goal: calculate executable forward outcomes and target/stop labels idempotently.

Primary files:

- `app/services/winner_probability/trading_session_service.py`
- `app/services/winner_probability/outcome_service.py`
- `app/services/winner_probability/target_stop_service.py`
- `app/services/winner_probability/outcome_revision_service.py`
- `app/services/price_bar_repository.py`
- `tests/winner_probability/test_trading_session_service.py`
- `tests/winner_probability/test_outcome_service.py`
- `tests/winner_probability/test_target_stop_service.py`

Tasks:

1. Extend session utilities:
   - next US trading session,
   - Nth session after entry,
   - completed-horizon check,
   - incomplete current-day guard.
2. Select due materialized `PENDING` outcome rows by due session and status rather than rediscovering logical outcomes dynamically.
3. Resolve default entry:
   - prediction date from latest completed signal session,
   - planned entry session from the stored capture-time schedule,
   - entry price from the ticker's next regular-session open.
4. Update entry data status from `NOT_DUE/PENDING` to `AVAILABLE`, `MISSING`, or `INVALID` as appropriate.
5. Resolve horizons for `1, 3, 5, 10, 20` sessions using the locked entry-day-inclusive convention.
6. Load ticker, SPY, and sector proxy bars in batches.
7. Validate:
   - entry bar exists,
   - all required bars exist or are recoverably pending,
   - OHLC relationships are valid,
   - adjustment basis is consistent,
   - bar lineage/revision hashes are available.
8. Calculate:
   - close return,
   - SPY return and excess SPY return,
   - sector return and excess sector return when available,
   - MFE and MAE,
   - sessions to MFE/MAE,
   - positive return,
   - beat SPY,
   - beat sector.
9. Evaluate target/stop scenarios:
   - fixed `+2.5%/-2.0%`,
   - optional snapshot suggested target/stop,
   - first event,
   - event session,
   - target hit,
   - stop hit,
   - optimistic/conservative/primary winner.
10. Mark same-bar conflicts explicitly and use conservative stop-first for the primary label.
11. Keep incomplete/not-yet-due market data as `PENDING`; mark malformed or adjustment-mismatched data as `EXCLUDED` only after documented retry/completeness rules are exhausted.
12. Hash source bar lineage for audit and revision detection.
13. If source bar lineage changes, create a new outcome revision and supersede the old current revision without deleting it.
14. When `SIGNAL_CLOSE_DIAGNOSTIC` is enabled, calculate separate diagnostic outcome rows from the signal-session close. Never merge diagnostic rows with `NEXT_OPEN` cohorts, estimates, exports, or UI labels.
15. Update materialized target/stop rows and forward outcomes in the same bounded unit of work, preserving partial status when benchmark or sector data is unavailable.

Tests:

- Weekend and holiday fixtures resolve the correct entry and horizon sessions.
- Entry-day-inclusive 5-session fixture matches the expected target-first/MFE/MAE result.
- Materialized pending rows mature exactly once when due.
- `SIGNAL_CLOSE_DIAGNOSTIC` results remain isolated from `NEXT_OPEN` evidence.
- Target-first, stop-first, neither, and same-bar conflict cases pass.
- Missing entry, missing intermediate, invalid OHLC, sector-proxy absence, SPY absence, and adjustment mismatch produce the documented statuses.
- Re-running unchanged outcomes creates no new revision.
- Revised bar lineage creates a new current revision while preserving the old one.

Exit criteria:

- Snapshots can mature into reproducible horizon and target/stop outcomes with full data-quality states and revision lineage.

## Phase 6: Cohort Baseline, Decision-Time Estimates, and Reproduction

Goal: produce transparent calibrated probability estimates, persist exact evidence membership, satisfy the capture-time estimator contract, and support historical reproduction before any ML model is active.

Primary files:

- `app/services/winner_probability/cohort_definition.py`
- `app/services/winner_probability/cohort_statistics.py`
- `app/services/winner_probability/probability_estimator.py`
- `app/services/winner_probability/decision_time_estimate_service.py`
- `app/services/winner_probability/evidence_manifest_service.py`
- `app/services/winner_probability/evidence_service.py`
- `app/services/winner_probability/reproduction_service.py`
- `tests/winner_probability/test_cohort_statistics.py`
- `tests/winner_probability/test_probability_estimator.py`
- `tests/winner_probability/test_decision_time_estimate_service.py`
- `tests/winner_probability/test_evidence_manifest_service.py`
- `tests/winner_probability/test_reproduction_service.py`

Tasks:

1. Implement cohort hierarchy:
   - L0: setup family + dual-score band + market risk state + sector state + ranking profile,
   - L1: setup family + score band + market risk state + sector leadership bucket,
   - L2: setup family + score band + market regime family,
   - L3: setup family + score band,
   - L4: setup family,
   - L5: global.
2. Enforce training cutoff for every estimate:
   - only outcomes fully matured strictly before the estimate cutoff,
   - only the outcome revision visible as of that cutoff,
   - no future prediction and no current prediction,
   - no prohibited dependent episode,
   - no reconstructed history unless explicitly approved for that model/cohort version.
3. Compute independent and effective sample sizes.
4. Compute cohort statistics:
   - wins,
   - raw rate,
   - posterior probability,
   - credible interval,
   - interval width,
   - median/mean return as configured,
   - median MFE,
   - median MAE,
   - target-first rate,
   - recency and coverage fields.
5. Use Beta-Binomial smoothing with configurable prior strength, default `20`.
6. Select the most specific eligible cohort meeting sample, recency, coverage, and interval-width gates.
7. Assign evidence grades:
   - High: effective n >= 100 and narrow interval,
   - Medium: effective n >= 40,
   - Low: effective n >= 15,
   - Insufficient: below threshold or critical quality failure.
8. Implement separate estimate workflows:
   - `create_decision_time_estimate(prediction_id)`: cutoff equals prediction source cutoff and record is immutable,
   - `create_latest_rescore(prediction_id, as_of)`: additive current-view estimate,
   - `reproduce_estimate(estimate_id)`: rebuilds from exact stored inputs and evidence membership without substituting current revisions.
9. Persist every decision-time result, including `Insufficient`, with:
   - outcome definition,
   - `estimate_kind=DECISION_TIME`,
   - source type,
   - cohort/model version,
   - point probability when available,
   - lower/upper bound and interval width,
   - n/effective n,
   - evidence grade,
   - raw counts and raw rate,
   - training cutoff,
   - selected cohort/backoff level,
   - explicit no-evidence reasons when withheld.
10. Persist exact evidence membership:
   - one membership row per included outcome revision, or
   - a content-addressed immutable manifest for large samples plus manifest hash and count,
   - store inclusion weight and episode representative details,
   - never rely only on a query definition that can resolve differently later.
11. Make the Phase 3 capture workflow call `create_decision_time_estimate()` after snapshot and pending-outcome creation. Capture must not be considered complete until an estimate or `Insufficient` record is stored.
12. For development snapshots created before this phase, allow a controlled historical decision-time reconstruction using the original prediction cutoff. Mark these records `reconstruction_method=AS_OF_REPLAY` and verify no later evidence entered. They are not evidence of what the UI displayed unless the UI actually existed at that time.
13. Expose raw mature counts in cold-start/insufficient states so the UI can show useful evidence without false calibration.
14. Persist reproducibility metadata:
   - canonical feature hash,
   - config hash,
   - outcome-definition version,
   - cohort/model version,
   - prior parameters,
   - evidence manifest hash,
   - source cutoff,
   - code/calculation version.
15. Return `Insufficient` with structured reasons when no cohort qualifies.

Tests:

- Small-sample raw rates shrink toward the prior and intervals widen.
- Exact cohort backs off to broader levels when sample gates fail.
- Future outcomes, current predictions, and dependent episodes are excluded.
- Evidence grades are reproducible from config.
- Decision-time estimates are created with cutoff equal to the prediction cutoff and remain immutable after cohort refresh.
- Latest re-scores are stored separately and cannot replace decision-time records.
- Every included outcome has exact membership or appears in the immutable manifest.
- Mutating current cohort definitions or outcome revisions does not change estimate reproduction.
- The same input payload and manifest produce the same probability estimate.
- An insufficient cohort persists raw counts and explicit reasons rather than a fake 50% estimate.

Exit criteria:

- Current predictions can show transparent cohort-based decision-time estimates or explicit no-evidence reasons.
- Exact evidence membership and reproduction are operational.
- The real estimator satisfies the Phase 3 capture-time contract, allowing controlled production capture in Phase 11.

## Phase 7: API, Filtering, Reproduction, and Export Backend

Goal: expose all OWPE data needed by the UI, historical audits, operations, and exports through bounded, documented, as-of-aware endpoints.

Primary files:

- `app/routers/winner_probability_routes.py`
- `app/services/winner_probability/evidence_service.py`
- `app/services/winner_probability/reproduction_service.py`
- `app/services/winner_probability/outcome_explorer_service.py`
- `app/services/winner_probability/operations_service.py`
- `app/services/winner_probability/exports.py`
- `app/services/winner_probability/repository.py`
- `app/main.py`
- `tests/winner_probability/test_routes_api.py`
- `tests/winner_probability/test_exports.py`

Tasks:

1. Add JSON APIs:
   - `GET /api/winner-probability/run/{run_id}`,
   - `GET /api/winner-probability/predictions/{prediction_id}`,
   - `GET /api/winner-probability/predictions/{prediction_id}/neighbors`,
   - `GET /api/winner-probability/tickers/{ticker}/history`,
   - `GET /api/winner-probability/estimates/{estimate_id}/reproduction`,
   - `GET /api/winner-probability/outcomes/explorer`,
   - `GET /api/winner-probability/operations/status`,
   - `GET /api/winner-probability/models`,
   - `GET /api/winner-probability/models/{id}/calibration`,
   - `GET /api/winner-probability/models/{id}/drift`.
2. Add explicit historical-view parameters where applicable:
   - `as_of_date`,
   - `training_cutoff`,
   - `estimate_view=DECISION_TIME|LATEST`,
   - `outcome_revision_view=AS_OF|CURRENT`,
   - `entry_model`,
   - `outcome_definition_id`.
3. Implement filters:
   - label and horizon,
   - minimum probability,
   - minimum lower bound,
   - maximum confidence/credible interval width,
   - evidence grade,
   - minimum raw and effective sample size,
   - minimum expected/median return,
   - minimum median MFE,
   - maximum adverse excursion threshold,
   - minimum target-before-stop rate,
   - setup/classification,
   - ranking profile,
   - regime/risk state,
   - sector state/rank,
   - earnings risk,
   - technical/fundamental data quality,
   - eligibility/exclusion state,
   - cursor/page.
4. Implement sorting:
   - probability,
   - lower bound,
   - interval width,
   - expected/median return,
   - median MFE,
   - median MAE,
   - target-first rate,
   - raw/effective sample size,
   - evidence grade,
   - deterministic ticker tie-break.
5. Use keyset pagination for history and large run/evidence lists.
6. Include schema version, config hash, model/cohort version, data cutoff, estimate kind, evidence-manifest hash, entry model, horizon convention, and outcome definition in payloads.
7. Add CSV and JSON exports for:
   - filtered current evidence,
   - historical prediction/outcome rows,
   - Outcome Explorer segment tables,
   - calibration/drift summaries,
   - estimate reproduction manifests where size permits.
8. Add/complete administrative APIs:
   - `POST /api/winner-probability/cohorts/refresh`,
   - `POST /api/winner-probability/models/{id}/retire`.
9. Add structured error model:
   - `INVALID_OUTCOME_DEFINITION`,
   - `PREDICTION_NOT_FOUND`,
   - `ESTIMATE_NOT_FOUND`,
   - `CAPTURE_CONFLICT`,
   - `INSUFFICIENT_POINT_IN_TIME_DATA`,
   - `REPRODUCTION_MISMATCH`,
   - `MODEL_PROMOTION_BLOCKED`,
   - `MODEL_RETIREMENT_BLOCKED`,
   - `MARKET_DATA_INCOMPLETE`,
   - `INVALID_AS_OF_CUTOFF`.
10. Bound API date windows, segment cardinality, export size, and neighbor counts to protect local resources.

Tests:

- Run API returns one row per ticker with selected outcome definition and estimate view.
- All required filters combine without dropping regime, sector, earnings, or data-quality context.
- Sorting is stable and deterministic.
- `as_of_date` cannot expose later outcomes, revisions, cohorts, or estimates.
- Prediction detail exposes feature snapshot, lineage, pending/mature outcomes, exact evidence manifest, decision-time estimate, latest re-score, warnings, and exclusions.
- Reproduction endpoint returns stored-versus-recalculated comparison and detects tampering/mismatch.
- Outcome Explorer returns segmented raw and calibrated statistics with low-sample suppression.
- Export headers include estimate kind, entry model, model/cohort version, cutoff, n/effective n, interval, grade, and label definition.
- Model retirement is auditable and cannot retire the only active model without an allowed fallback policy.

Exit criteria:

- All OWPE data required by the run table, ticker page, Outcome Explorer, operations view, calibration/drift dashboard, exports, and audit/reproduction flows is available through bounded service/API calls.

## Phase 8: Product UI, Outcome Explorer, and Operations Views

Goal: make OWPE useful without crowding or misleading the existing research workflow, while clearly separating calibrated estimates, raw evidence, diagnostics, and operational health.

Primary files:

- `app/templates/run_detail.html`
- `app/templates/winner_probability_run.html`
- `app/templates/winner_probability_ticker.html`
- `app/templates/winner_probability_models.html`
- `app/templates/winner_probability_outcomes.html`
- `app/templates/winner_probability_operations.html`
- `app/templates/partials/_nav.html`
- `app/static/app.css`
- `app/static/winner_probability.js`
- `tests/winner_probability/test_routes_ui.py`
- `tests/test_run_detail_view_models.py`

Tasks:

1. Add run-level evidence columns or an expandable evidence panel:
   - winner probability,
   - lower/upper bound and interval width,
   - evidence grade,
   - raw and effective n,
   - median five-session return,
   - median MFE,
   - median MAE,
   - target-first rate,
   - estimate kind and training cutoff,
   - warnings/no-evidence reason.
2. Keep filters server-side and encoded in query parameters. Include all Phase 7 probability, interval, return, MFE/MAE, target-first, earnings-risk, regime, sector, and data-quality filters.
3. Add evidence sort mode using lower confidence bound, then expected/median return, then existing rank. Do not mutate stored rank.
4. Add a Ticker Winner Evidence page:
   - selected winner definition and entry model,
   - entry-day-inclusive horizon assumption,
   - probability interval,
   - sample and independent-episode counts,
   - cohort dimensions and backoff level,
   - model/cohort version,
   - training cutoff,
   - original decision-time estimate versus latest re-score,
   - exact evidence manifest summary,
   - feature snapshot and source lineage,
   - forward and target/stop outcomes,
   - warnings/exclusions.
5. Add a complete Outcome Explorer:
   - segment by technical classification/setup family, ranking profile, market regime/risk state, sector state/rank bucket, score band, entry model, horizon, outcome definition, and date period,
   - show observed win rate, posterior probability, interval, median return, median MFE/MAE, target-first rate, raw n, effective n, and independent episodes,
   - compare periods and segments,
   - suppress or mark insufficient low-sample cells,
   - export the current segmented view.
6. Add calibration/model-health dashboard:
   - reliability bins,
   - Brier score,
   - log loss,
   - ECE,
   - calibration slope/intercept,
   - coverage,
   - sample growth,
   - segment drilldowns,
   - current drift indicators and threshold breaches.
7. Add an operations/outcome-state view:
   - snapshot counts,
   - pending/due/mature/excluded outcome counts,
   - oldest overdue pending row,
   - latest processing runs and failures,
   - cohort refresh age,
   - active model/cohort version,
   - worker lease/heartbeat health.
8. Add cold-start and insufficient-evidence states that remain informative:
   - no active model/cohort,
   - no matured outcomes,
   - insufficient sample,
   - missing benchmark/sector,
   - raw mature wins/losses, raw rate, median return/MFE/MAE, and the exact reason calibrated probability is withheld.
9. Use restrained display precision:
   - whole percentage points by default,
   - intervals always visible,
   - explicit entry model and outcome definition,
   - `Insufficient` rather than false precision.
10. Add estimate reproduction/audit action on the detail page that shows stored versus reproduced values and manifest hash.
11. Add research-only labeling and no direct trade/order action.

Tests:

- Run page renders all required evidence fields when present.
- Run page renders raw evidence and clear no-evidence reasons when calibrated probability is withheld.
- Ticker evidence page cannot confuse target-before-stop probability with generic positive-return probability.
- `NEXT_OPEN` and `SIGNAL_CLOSE_DIAGNOSTIC` are visibly separate.
- Historical page displays original estimate and latest re-score separately.
- Outcome Explorer segments and suppresses low-sample cells correctly.
- Operations page exposes overdue pending rows and failed jobs.
- Drift warnings appear only when configured thresholds are breached.
- Navigation link and run-scoped entry points are present.

Exit criteria:

- A user can capture, mature, estimate, filter, inspect, reproduce, segment, monitor, and export OWPE evidence through the app without mistaking research evidence for a trading command.

## Phase 9: Calibration, Drift, Model Registry, and Governance

Goal: make baseline health measurable, detect deterioration, and safely manage active, shadow, rejected, promoted, and retired versions.

Primary files:

- `app/services/winner_probability/calibration_service.py`
- `app/services/winner_probability/drift_service.py`
- `app/services/winner_probability/model_registry.py`
- `app/services/winner_probability/model_artifact_service.py`
- `app/services/winner_probability/probability_estimator.py`
- `tests/winner_probability/test_calibration_service.py`
- `tests/winner_probability/test_drift_service.py`
- `tests/winner_probability/test_model_registry.py`
- `tests/winner_probability/test_model_artifact_service.py`

Tasks:

1. Persist model/cohort versions with:
   - algorithm,
   - feature schema,
   - label and entry model,
   - training window/cutoff,
   - hyperparameters,
   - calibration method,
   - metrics,
   - status,
   - config/code/dependency versions,
   - artifact format/path or JSON payload,
   - artifact hash and size.
2. Calculate reliability bins:
   - probability bin,
   - n/effective n,
   - mean prediction,
   - observed rate,
   - confidence/credible interval,
   - error.
3. Calculate metrics:
   - Brier score,
   - log loss,
   - ECE,
   - calibration slope/intercept where applicable,
   - coverage,
   - segment stability.
4. Calculate drift indicators over configured rolling windows:
   - rolling observed win rate,
   - rolling Brier score and ECE,
   - prediction-distribution shift,
   - probability-bin population shift,
   - feature/data-coverage shift,
   - setup/regime/sector segment degradation,
   - PSI/KS or equivalent statistics where appropriate and sufficiently sampled.
5. Store drift metrics with as-of date, comparison window, segment, sample sufficiency, threshold, and breach status.
6. Implement promotion gates:
   - calibration threshold,
   - coverage threshold,
   - minimum sample,
   - no critical segment degradation,
   - no unresolved critical drift breach.
7. Make promotion and retirement transactional and auditable:
   - actor,
   - reason,
   - old status,
   - new status,
   - replacement version when applicable,
   - timestamp,
   - lifecycle event record.
8. Keep candidates in `SHADOW` or `REJECTED` unless promotion gates pass and an explicit local action promotes them.
9. Prevent retirement of the only production source unless a documented fallback cohort/model is active.
10. Validate model artifacts before activation by checking schema compatibility, dependencies, hash, feature ordering, and calibration payload.

Tests:

- Calibration bins compare predicted probability with observed frequency correctly.
- Drift metrics detect synthetic distribution and performance changes and remain insufficient when n is too low.
- Promotion fails closed when sample, calibration, drift, or segment gates fail.
- Promotion and retirement record lifecycle audit data atomically.
- A failed candidate cannot become `ACTIVE`.
- A retired model cannot serve new latest re-scores.
- Corrupt or incompatible artifacts cannot be activated.

Exit criteria:

- The cohort baseline has measurable calibration and drift health.
- The registry safely manages active/shadow/rejected/retired versions with reproducible artifacts and lifecycle audit.

## Phase 10: Similarity and Shadow Models

Goal: add supporting evidence and future model candidates without changing default production behavior.

Primary files:

- `app/services/winner_probability/similarity_service.py`
- `app/services/winner_probability/model_training.py`
- `tests/winner_probability/test_similarity_service.py`
- `tests/winner_probability/test_quantitative_validation.py`

Tasks:

1. Implement weighted Gower distance:
   - bounded numeric normalization,
   - categorical equality distance,
   - missing values excluded from pair denominator,
   - separate similarity coverage score.
2. Enforce temporal and episode safety:
   - neighbor predictions must precede the current prediction cutoff,
   - selected label must be mature,
   - one representative per dependent episode by default.
3. Return:
   - distance,
   - rank,
   - top feature contributions,
   - similarity coverage,
   - outcome summary,
   - exact neighbor prediction/outcome revision IDs used.
4. Label similarity as supporting evidence, not the primary probability source.
5. Add shadow model training scaffolding:
   - regularized logistic regression,
   - gradient-boosted trees only if dependencies are approved,
   - time-ordered walk-forward folds,
   - fold-safe preprocessing,
   - group constraints by episode.
6. Keep model activation behind Phase 9 promotion gates.
7. Persist `winner_model_training_runs` with fold cutoffs, episode groups, feature schema, preprocessing artifacts, metrics, warnings, and candidate artifact hash.
8. Persist similarity caches as rebuildable data with a cache version and source cutoff; never use current/future neighbors for a historical decision-time view.

Tests:

- Mixed numeric/categorical distance is stable.
- Missingness reduces coverage without inventing distance.
- Current/future predictions and dependent episodes are excluded.
- Walk-forward folds never leak future outcomes or same episodes across train/test.
- Candidate reports compare against cohort and global baselines.

Exit criteria:

- The app can show nearest historical analogues and train shadow candidates without changing production estimates.

## Phase 11: Backfill, Rollout, and Acceptance

Goal: turn the subsystem on safely, with visible evidence quality, complete decision-time records, and clear operational controls.

Primary files:

- `app/services/winner_probability/backfill.py`
- `app/services/winner_probability/job_handlers.py`
- `docs/release_notes_winner_probability_engine.md`
- `tests/winner_probability/test_acceptance_fixture.py`

Tasks:

1. Apply configuration and migrations with all capture/admin feature flags disabled.
2. Verify Phase 0 lease heartbeat/fencing in a long-running staging job.
3. Enable manual capture only after the real Phase 6 decision-time estimator is available.
4. Capture selected completed runs and validate:
   - snapshot counts and hashes,
   - planned entry sessions,
   - pending outcome rows,
   - episode assignments,
   - decision-time estimate or persisted `Insufficient` record,
   - exact evidence manifest/hash,
   - warnings and source lineage.
5. Validate estimate reproduction for selected captures before enabling pipeline integration.
6. Enable `CAPTURING_WINNER_PREDICTIONS` as a nonfatal final pipeline step. A ticker is complete only after snapshot, pending outcomes, and decision-time estimate/insufficient record are persisted.
7. Backfill historical run snapshots only where point-in-time source records are trustworthy.
8. Mark reconstructed history with `reconstruction_method`, source cutoff, and quality flags.
9. Exclude reconstructed records from production training by default unless explicitly approved in a versioned cohort/model configuration.
10. For reconstructed predictions that need decision-time evidence, replay strictly as of the original prediction cutoff and label the estimate `AS_OF_REPLAY`; do not imply it was historically displayed.
11. Enable daily outcome maturation after the completed US session and monitor overdue pending rows.
12. Refresh cohorts only after material new outcomes mature or an explicit administrative request.
13. Enable run evidence filters, ticker evidence page, Outcome Explorer, and operations view in cold-start mode.
14. Enable calibration and drift dashboards after sufficient baseline data exists.
15. Consider similarity and shadow models only after the cohort baseline is stable.
16. Verify retention behavior and cleanup only for rebuildable/cache/export data.

Acceptance checklist:

- All Must requirements are implemented or explicitly waived in a release note.
- A full run can be captured, assigned to an episode, given pending outcomes, assigned a decision-time estimate/insufficient record, matured, calibrated, displayed, filtered, segmented, reproduced, exported, and audited end to end.
- Point-in-time leakage tests pass, including historical source mutation, strict training cutoff, outcome revision as-of, and exact evidence-membership tests.
- Corporate-action, missing-bar, holiday, incomplete-session, same-bar conflict, diagnostic-entry, and revised-bar fixtures pass.
- The same input payload and evidence manifest produce the same prediction hash and probability estimate.
- Decision-time estimates never change after later outcomes, cohort refreshes, model promotions, or bar revisions.
- The dashboard never presents a high-confidence probability when evidence rules fail.
- Outcome Explorer suppresses or labels insufficient segments.
- Operations view identifies overdue pending outcomes and failed/stale jobs.
- Model promotion and retirement are transactional and auditable.
- No code path can place a broker order.

Exit criteria:

- OWPE is safe to use as a local research evidence layer with transparent uncertainty, immutable decision-time evidence, exact audit lineage, reproducible estimates, and operational monitoring.

## Test Matrix

Core unit tests:

- Config validation and hash stability.
- Horizon-counting, estimate-kind, as-of, retention, and entry-model separation rules.
- Feature schema availability and missingness rules.
- Canonical feature serialization and hash stability.
- Mandatory capture-field completeness and universe/screener provenance.
- Episode assignment and cooldown boundaries.
- Pending outcome materialization and due-session calculation.
- Trading-session resolution across weekends, holidays, daylight-saving transitions, and incomplete current days.
- Return, SPY excess, sector excess, MFE, MAE, and sessions-to-extreme calculations.
- Target-first, stop-first, neither, and same-bar conflict target/stop cases.
- Strict isolation of `NEXT_OPEN` and `SIGNAL_CLOSE_DIAGNOSTIC`.
- Cohort backoff, smoothing, intervals, interval width, and evidence grades.
- Decision-time cutoff anchoring and latest-rescore separation.
- Exact evidence-membership persistence and manifest hashing.
- Estimate reproduction from immutable inputs and evidence revisions.
- Calibration metrics, drift metrics, promotion gates, and retirement gates.
- Similarity distance, missingness coverage, temporal exclusions, episode deduplication, and exact neighbor IDs.

Worker and concurrency tests:

- Live heartbeats prevent stale recovery.
- Lease expiry permits exactly one recovery.
- Fencing prevents an old worker from committing.
- Cancellation is observed before the next bounded batch.
- Idempotent handlers tolerate retry after partial commit.
- Concurrent capture cannot create duplicate active snapshots, pending outcomes, or decision-time estimates.

Integration tests:

- Alembic migration up/down/up with constraints and indexes on the project’s real PostgreSQL test path.
- Full pipeline creates prediction snapshots after sector rotation only when capture is enabled.
- Capture transaction creates snapshot, episode, pending outcomes, target/stop rows, and decision-time estimate/insufficient record.
- Manual capture is idempotent.
- Outcome job matures fixture rows using real `PriceBar` rows and approved market calendar.
- Revised bars create new outcome revisions and preserve previous evidence.
- Exact decision-time evidence still references the original outcome revisions after later revisions exist.
- Run API returns estimates and all compound filters with realistic data volumes.
- Historical `as_of` API never exposes later revisions or outcomes.
- Reproduction endpoint detects deliberate manifest/config tampering.
- Outcome Explorer segments realistic data and suppresses insufficient groups.
- Model promotion and retirement are transactional and auditable.
- Historical source rows can change without altering captured features or decision-time estimates.

Quantitative validation tests:

- No-look-ahead test rejects deliberately future-dated features.
- Future outcome never enters an earlier decision-time estimate or training fold.
- Same episode cannot appear in both train and test sets.
- Synthetic calibrated probabilities recover expected observed rates within tolerance.
- Small-sample shrinkage moves extreme raw rates toward the prior.
- Segment metrics with inadequate n are marked insufficient.
- Synthetic drift fixtures trigger the expected rolling performance/distribution warnings.
- Reconstructed history remains excluded unless an approved configuration includes it.

API/UI/export tests:

- Filters include probability, lower bound, interval width, return, MFE, MAE, target-first rate, evidence, earnings risk, regime, sector, and data quality.
- Sorting remains deterministic with ticker tie-breaks.
- Cold-start UI shows raw counts and reasons without fake probability.
- Decision-time and latest-rescore values are visually distinct.
- Outcome Explorer, operations dashboard, calibration, and drift views render required states.
- Exports include entry model, estimate kind, cutoff, evidence manifest hash, n/effective n, interval, grade, and outcome definition.

Performance tests:

- Capture 1,000 tickers, including pending-row and estimate creation, within the configured target excluding upstream scoring/fetch. Initial target: under 30 seconds on the reference local environment.
- Filtered current-run evidence query for 1,000 tickers returns within 2 seconds at p95.
- Outcome maturation handles 100,000 pending horizon rows in bounded resumable batches or under the documented target runtime.
- Outcome Explorer returns bounded high-cardinality segment queries within the configured p95 target.
- Evidence manifest retrieval uses pagination or compressed artifacts for large cohorts.

Security/safety tests:

- Administrative mutation endpoints are local-only under existing app controls.
- Exports exclude secrets and broker connection details.
- No OWPE path imports or calls an order-placement client.
- UI uses research-only labeling and no trade button.
- Model artifacts cannot execute arbitrary code during loading.

## Requirement Traceability

The phase-level mapping below must be supplemented during implementation by a machine-readable or tabular requirement matrix containing requirement ID, implementing service, migration/table, API/UI surface, test ID, status, and waiver reference. A section-level assertion alone is not sufficient for release acceptance.

- Prediction capture: Phases 0, 1, 2, 3, and 6 cover `FR-CAP-001` through `FR-CAP-012`, including mandatory fields, point-in-time cutoffs, episodes, pending outcome materialization, and capture-time decision estimates.
- Outcome maturation: Phases 1, 2, 3, and 5 cover `FR-OUT-001` through `FR-OUT-015`, including entry-day-inclusive sessions, pending/due states, benchmarks, MFE/MAE, target/stop ambiguity, diagnostic entry models, and revisions.
- Probability and calibration: Phases 2, 6, and 9 cover `FR-PROB-001` through `FR-PROB-015`, including smoothing, backoff, exact evidence membership, immutable decision-time estimates, latest re-scores, calibration, drift, and model governance.
- Similarity: Phase 10 covers `FR-SIM-001` through `FR-SIM-006`, including temporal safety, episode deduplication, coverage, exact neighbor IDs, and supporting-only status.
- Filtering and ranking: Phases 1, 7, and 8 cover `FR-FLT-001` through `FR-FLT-008`, including probability, lower bound, interval width, expected/median return, MFE, MAE, target-first rate, evidence, earnings, regime, sector, and data-quality filters. Saved filters remain deferred as optional scope.
- UI and explainability: Phase 8 covers `FR-UI-001` through `FR-UI-010`, including run evidence, ticker detail, raw cold-start evidence, Outcome Explorer, operations state, calibration/drift, decision-time versus latest view, and research-only labeling.
- APIs/jobs/admin: Phases 0, 4, and 7 cover `FR-API-001` through `FR-API-007`, including hardened durable jobs, as-of APIs, cohort refresh, reproduction, promotion, retirement, and bounded exports.
- Auditability/governance: Phases 0, 2, 3, 5, 6, 7, 9, and 11 cover `FR-AUD-001` through `FR-AUD-006`, including permanent immutable evidence, exact manifests, source/outcome revisions, processing/training lineage, artifact hashes, reproduction, lifecycle events, and retention.
- Non-functional requirements: all phases include idempotency, performance, observability, local-only security, maintainability, bounded operations, heartbeat/fencing, deterministic calculations, data integrity, and retention.

Release rule:

- Every `Must` requirement must map to at least one implementation artifact and one passing test, or to an explicit approved waiver in the release notes.
- Every persisted probability shown to the user must be reproducible from immutable inputs and exact evidence membership.
- No production capture may be enabled while any P0 prerequisite in this plan remains incomplete.

## Implementation Readiness Gate

Implementation may begin phase by phase, but production enablement is blocked until all of the following are true:

- Phase 0 worker heartbeat/fencing tests pass.
- Horizon, entry, same-bar, as-of, estimate-kind, and evidence-membership semantics are locked.
- The schema supports pending outcomes and exact evidence membership.
- Capture creates snapshot, episode, pending outcomes, and a decision-time estimate or explicit `Insufficient` record.
- The estimator uses only evidence fully mature before the prediction cutoff.
- Estimate reproduction succeeds from immutable inputs and exact evidence revisions.
- Compound filters, Outcome Explorer, and cold-start raw evidence meet SRS acceptance criteria.
- Model promotion and retirement are auditable and fail closed.
- No broker-order path is reachable from OWPE.

Recommended implementation order for pull requests:

1. PR 1: Phase 0 settings, semantic decisions, and worker lease hardening.
2. PR 2: Phase 1 configuration/schema registry and Phase 2 database migration.
3. PR 3: Phase 3 capture, episodes, pending outcomes, and estimator interface.
4. PR 4: Phase 5 outcome maturation and revision lineage.
5. PR 5: Phase 6 cohort estimator, decision-time records, exact manifests, and reproduction.
6. PR 6: Phase 4 completed job handlers and administrative triggers.
7. PR 7: Phase 7 APIs, filters, as-of views, and exports.
8. PR 8: Phase 8 run/ticker UI, Outcome Explorer, and operations view.
9. PR 9: Phase 9 calibration, drift, model registry, artifacts, promotion, and retirement.
10. PR 10: Phase 10 similarity and shadow models.
11. PR 11: Phase 11 controlled rollout, backfill, acceptance fixtures, and release notes.

## Deferred or Optional Scope

Defer unless the MVP is already stable:

- Saved filters.
- Intraday bars for resolving same-bar ordering.
- Evidence-aware ranking blend that mutates existing rankings. OWPE evidence sorting remains in scope.
- External point-in-time news, estimates, or catalyst data.
- Options, shorts, crypto, forex, and non-US markets.
- Automated model activation or retirement. Explicit local governance remains required.
- Deep-learning models.
