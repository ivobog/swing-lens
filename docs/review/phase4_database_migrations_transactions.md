# SwingLens Phase 4 Database Schema, Migrations, Transactions, and Data Lifecycle

Review date: 2026-08-02
Phase 0 baseline: `docs/review/phase0_baseline.md`
Phase 1 traceability: `docs/review/phase1_requirements_traceability.md`
Phase 3 configuration: `docs/review/phase3_configuration_feature_flags.md`
Review target commit: `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`

## Objective

Phase 4 verifies persistence integrity, migration reproducibility, transaction safety, retention,
and safe evolution of evidence and derived results. The review emphasized clean-database upgrades,
schema/model drift, referential behavior, delete-and-insert refreshes, purge execution, timestamp
handling, JSONB growth, and idempotency constraints.

## Evidence Log

Inspected files and surfaces:

- `alembic.ini`, `alembic/env.py`, all `alembic/versions/*.py`
- `app/models/tables.py`, `app/models/ceri_tables.py`
- write-heavy services: upload, scoring refresh, market regime, sector rotation, CERI purge,
  CERI job handlers, background job service, background worker, pipeline services
- schema and persistence tests under `tests/test_schema_phase2.py`,
  `tests/setup_lifecycle/test_setup_lifecycle_schema.py`, `tests/winner_probability/test_schema.py`,
  `tests/ceri/test_ceri_schema.py`, `tests/test_background_job_service.py`,
  `tests/test_pipeline_service.py`, `tests/test_pipeline_executor.py`,
  `tests/test_upload_service_v2.py`, and `tests/test_csv_upload_services.py`

Command evidence:

| Command | Result | Notes |
|---|---:|---|
| `uv run alembic heads; uv run alembic history --indicate-current` | Head is current | Single head: `0021_add_ceri_earnings_consensus_reason` |
| Temporary DB migration smoke: create fresh DB, `uv run alembic upgrade head` | Failed | `0019_add_ceri_ingestion_audit_fields` tries to add duplicate `retry_count` to `ceri_ingestion_runs`; temp DB was dropped |
| `uv run alembic check` | Failed | Autogenerate detects seven removed indexes present in DB but absent from metadata |
| Metadata inventory script over `Base.metadata` | Completed | 60 tables, 144 indexes, 45 unique constraints, 3 check constraints, 145 JSONB columns, 120 timezone-aware datetime columns, 170 numeric columns |
| `uv run pytest tests/test_schema_phase2.py tests/setup_lifecycle/test_setup_lifecycle_schema.py tests/winner_probability/test_schema.py tests/ceri/test_ceri_schema.py tests/test_background_job_service.py tests/test_pipeline_service.py tests/test_pipeline_executor.py tests/test_upload_service_v2.py tests/test_csv_upload_services.py -q` | Passed | `115 passed in 7.63s` |

Assumptions and limitations:

- `EXPLAIN (ANALYZE, BUFFERS)` was deferred because representative high-volume datasets are not
  present in the review workspace.
- Historical upgrade testing stopped at the first clean-database failure. Downgrade and
  representative historical hop testing cannot be trusted until the fresh upgrade path is repaired.
- Tests are mostly metadata/unit/fake-session tests; they do not yet substitute for real
  PostgreSQL migration, concurrency, rollback, and explain-plan coverage.

## Schema Inventory

Current SQLAlchemy metadata:

| Dimension | Count |
|---|---:|
| Tables | 60 |
| Indexes | 144 |
| Unique constraints | 45 |
| Check constraints | 3 |
| JSONB columns | 145 |
| Datetime columns | 120 |
| Timezone-aware datetime columns | 120 |
| Numeric columns | 170 |
| Unbounded numeric columns | 80 |

Foreign-key delete behavior:

| `ondelete` policy | Count |
|---|---:|
| `SET NULL` | 50 |
| `CASCADE` | 22 |
| `RESTRICT` | 12 |
| not specified | 32 |

Representative table shape:

| Table | Columns | Indexes | Unique constraints | Check constraints | FKs |
|---|---:|---:|---:|---:|---:|
| `upload_runs` | 11 | 0 | 0 | 0 | 0 |
| `raw_company_rows` | 12 | 5 | 0 | 0 | 1 |
| `fundamental_scores` | 31 | 1 | 1 | 0 | 1 |
| `technical_scores` | 43 | 1 | 1 | 0 | 1 |
| `combined_results` | 25 | 3 | 1 | 0 | 1 |
| `ranking_results` | 33 | 6 | 1 | 0 | 2 |
| `background_jobs` | 22 | 5 | 0 | 0 | 0 |
| `pipeline_runs` | 11 | 3 | 0 | 0 | 1 |
| `pipeline_steps` | 11 | 1 | 1 | 0 | 1 |
| `ceri_ingestion_runs` | 26 | 1 | 1 | 0 | 0 |
| `ceri_source_records` | 21 | 3 | 2 | 0 | 2 |
| `ceri_purge_audits` | 12 | 1 | 1 | 0 | 0 |

Positive evidence:

- Time columns in metadata use `DateTime(timezone=True)`.
- Newer subsystems define natural-key uniqueness for snapshots, source records, estimates,
  lifecycle events, winner artifacts, alerts, and CERI changes.
- Background job claiming uses `FOR UPDATE SKIP LOCKED` and execution-token fencing.
- CERI, SLSE, OWPE, sector rotation, and market regime artifacts carry explicit version/hash fields
  more consistently than early core scoring tables.

## Findings Register

ID: PH4-001
Title: Fresh Alembic upgrade to head fails on an empty PostgreSQL database
Severity: S1 High
Confidence: Confirmed
Affected components: `alembic/versions/20260801_0018_add_ceri_tables.py`,
`alembic/versions/20260801_0019_add_ceri_ingestion_audit_fields.py`,
`app/models/ceri_tables.py`
Evidence: A temporary clean database failed during `uv run alembic upgrade head` with
`psycopg.errors.DuplicateColumn: column "retry_count" of relation "ceri_ingestion_runs" already
exists`. Migration `0018` creates CERI tables by importing live `CERI_TABLES`; the current model
already includes `retry_count` and `checkpoint_json`, while migration `0019` also adds them.
Reproduction steps: Create a new PostgreSQL database using the configured user, set `DATABASE_URL`
to that database, and run `uv run alembic upgrade head`.
Expected behavior: A fresh database upgrades from `<base>` to `head` without errors.
Observed behavior: Upgrade fails at `0019_add_ceri_ingestion_audit_fields`.
Impact: Fresh install is broken. Disaster recovery into a clean database and CI migration smoke
tests would fail.
Root cause or likely cause: Historical migration `0018` imports mutable application model metadata
instead of containing a frozen table definition.
Recommended remediation: Rewrite `0018` as explicit Alembic `op.create_table` operations matching
the intended schema at that revision, or squash/repair the CERI migration chain before release.
Ensure `0019` only adds columns that are absent from the immediately prior revision.
Acceptance criteria: A temporary clean PostgreSQL database can run `alembic upgrade head`,
`alembic downgrade base`, and representative historical upgrade hops successfully.
Regression tests required: Real PostgreSQL clean-database migration smoke in CI.
Owner profile: Backend/database engineer
Dependencies: Decide whether pre-release migration surgery or a corrective forward migration is
acceptable for this branch.

ID: PH4-002
Title: Several migrations import live ORM tables, making historical upgrades non-deterministic
Severity: S1 High
Confidence: Confirmed
Affected components: migrations `0016_add_winner_probability_engine`,
`0017_create_setup_lifecycle_tables`, `0018_add_ceri_tables`
Evidence: `rg` shows all three migrations import `app.models.tables` or `app.models.ceri_tables`
and call `table.create(bind=bind, checkfirst=False)`. The CERI migration has already drifted enough
to break clean upgrades.
Reproduction steps: Inspect the migrations above; change an imported ORM table; rerun the historical
migration in a clean database.
Expected behavior: Historical migrations are immutable executable records of the schema at the time
they were authored.
Observed behavior: Migration behavior changes when model classes change later.
Impact: Future changes to winner, setup lifecycle, or CERI models can silently alter old migration
steps, breaking upgrades, downgrades, or disaster recovery.
Root cause or likely cause: Migration authoring shortcut reused live SQLAlchemy tables.
Recommended remediation: Replace live-model table creation in migrations with explicit Alembic
operations or frozen `sa.Table` definitions local to the migration file.
Acceptance criteria: No migration under `alembic/versions` imports application models except for
Alembic `env.py` metadata discovery.
Regression tests required: Static migration lint that rejects `from app.models` imports in revision
files, plus clean PostgreSQL upgrade tests.
Owner profile: Backend/database engineer
Dependencies: Same migration-chain decision as PH4-001.

ID: PH4-003
Title: Alembic metadata drift makes `alembic check` fail
Severity: S2 Medium
Confidence: Confirmed
Affected components: `app/models/tables.py`, migration `0009_history_indexes`, Alembic
autogeneration workflow
Evidence: `uv run alembic check` reports pending operations to remove
`idx_upload_runs_uploaded_at_desc`, `idx_upload_runs_status`,
`idx_combined_results_ticker`, `idx_combined_results_decision`,
`idx_combined_results_score`, `idx_combined_results_warning`, and
`idx_combined_results_complete`. These indexes exist in migrations/current DB but are not represented
in SQLAlchemy metadata.
Reproduction steps: Run `uv run alembic check` against the configured head database.
Expected behavior: Metadata and head database are aligned, or intentional migration-only indexes are
documented and filtered.
Observed behavior: Alembic sees seven removed indexes.
Impact: Alembic autogeneration is noisy and unsafe; a future autogenerated migration may drop useful
history/dashboard indexes accidentally.
Root cause or likely cause: Indexes added by migration were not added to ORM `__table_args__`, and
no Alembic include filter documents the divergence.
Recommended remediation: Add the indexes to ORM metadata or configure/justify Alembic filters for
intentional DB-only indexes.
Acceptance criteria: `uv run alembic check` passes against a head database.
Regression tests required: CI job for `alembic check`.
Owner profile: Backend/database engineer
Dependencies: Decide whether ORM metadata should be the canonical index source.

ID: PH4-004
Title: CERI purge execution records an audit but does not purge or invalidate source data
Severity: S1 High
Confidence: Confirmed
Affected components: `app/services/ceri/purge_service.py`,
`app/services/ceri/job_handlers.py`, `app/routers/ceri_routes.py`,
`tests/ceri/test_ceri_acceptance_fixture.py`
Evidence: `CeriPurgeService.execute` validates preview scope and confirmation token, then sets
`audit.status = "EXECUTED"` and `executed_at`; it does not delete, tombstone, quarantine, redact, or
invalidate matching `CeriSourceRecord` or derivative rows. The acceptance test is explicitly named
`test_purge_preview_and_execute_are_audited_without_deleting_sources` and asserts `db.deleted == []`.
Reproduction steps: Create a source record matching a preview scope, call `preview`, then call
`execute` with the generated token.
Expected behavior: A feature named provider-license purge either performs the documented purge or is
renamed/documented as audit-only preview/attestation.
Observed behavior: Execution marks the audit as executed while source data remains unchanged.
Impact: Operators can believe licensed data was purged when it was only counted and audited. If
provider-license deletion is required, this is a data-retention compliance and trust risk.
Root cause or likely cause: Purge controls were implemented as preview-first governance, but the
destructive/invalidation phase has not been implemented or intentionally deferred.
Recommended remediation: Define the intended lifecycle semantics. If purge is required, implement
transactional tombstone/delete/redaction plus derivative invalidation and rebuild obligations. If
audit-only is intended, rename routes/jobs/status labels and update requirements.
Acceptance criteria: Executing purge either removes/restricts affected data and records rebuild
requirements, or the UI/API clearly state that execution is audit-only.
Regression tests required: Real DB tests proving source rows and derivatives transition to the
chosen lifecycle state and audit rows are preserved.
Owner profile: Backend/product owner
Dependencies: Product/legal decision on licensed-data retention obligations.

ID: PH4-005
Title: Core score tables have weak database-level value constraints and many unbounded numerics
Severity: S2 Medium
Confidence: Strong
Affected components: `price_bars`, `fundamental_scores`, `technical_scores`,
`combined_results`, `ranking_results`, older migrations
Evidence: Metadata inventory found 170 numeric columns, 80 with no precision/scale, and only 3
check constraints across 60 tables. Early migrations use `sa.Numeric()` broadly for prices, scores,
and ranking values. Core score tables have no database checks for expected score ranges,
non-negative volume, rank positivity, or probability-like bounds.
Reproduction steps: Inspect early migrations and model metadata for `sa.Numeric()`/`Numeric()` and
`CheckConstraint`.
Expected behavior: High-impact persisted research values have database constraints where invalid
values would be materially misleading.
Observed behavior: Most constraints are service-layer only.
Impact: A service bug, manual DB write, malformed migration, or import script can persist impossible
values that later appear authoritative.
Root cause or likely cause: Early schema optimized for flexibility before score contracts stabilized.
Recommended remediation: Add conservative check constraints for stable invariants: non-negative
volume, positive ranks, bounded percentages/probabilities/confidence where semantics are settled,
and explicit numeric precision for score and price families.
Acceptance criteria: Database rejects clearly impossible values without relying solely on Python
services.
Regression tests required: Constraint tests against PostgreSQL for representative invalid values.
Owner profile: Backend/quant engineer
Dependencies: Finalize score/range semantics with quantitative owners.

ID: PH4-006
Title: Delete-and-insert refresh patterns lack rollback and reader-consistency tests
Severity: S2 Medium
Confidence: Strong
Affected components: `fundamental_score_service`, `combined_decision`,
`sector_rotation_repository`, `market_regime_repository`, pipeline executor
Evidence: `recalculate_run_fundamentals` deletes all `FundamentalScore` rows for a run before
inserting recalculated scores; `refresh_combined_results` does the same for `CombinedResult`;
`SectorRotationRepository.save_snapshot` deletes prior snapshot rows before adding replacements.
These operations are flushed within the caller's transaction, but targeted mid-failure rollback and
concurrent reader tests were not found.
Reproduction steps: Inject an exception after the delete and before replacement insert under a real
PostgreSQL session; observe rollback behavior and concurrent reads.
Expected behavior: Refreshes are atomic and never leave committed empty/partial derived results;
concurrent readers either see old complete data or new complete data.
Observed behavior: Code likely behaves atomically when callers commit only after the full operation,
but this invariant is not encoded by tests or transaction wrappers.
Impact: Future refactors that introduce intermediate commits could create transient or committed
empty result sets for research views.
Root cause or likely cause: Transaction ownership is implicit in route/worker callers rather than
made explicit per refresh unit.
Recommended remediation: Add real PostgreSQL failure-injection tests and wrap destructive refresh
units in explicit transaction boundaries or repository-level contracts.
Acceptance criteria: Tests prove rollback after injected mid-refresh failure leaves prior data
unchanged, and concurrent readers do not observe partial replacement state.
Regression tests required: PostgreSQL transaction tests for fundamental, combined, sector, and
pipeline refreshes.
Owner profile: Backend engineer
Dependencies: Testcontainers or a local PostgreSQL integration-test harness.

ID: PH4-007
Title: Upload files are written outside the database transaction without cleanup on DB failure
Severity: S2 Medium
Confidence: Strong
Affected components: `app/services/upload_service.py`, upload artifact lifecycle
Evidence: `create_upload_run` validates size and calls `_save_upload` before creating and committing
the `UploadRun`. CSV validation failures are represented in the database, but unexpected database
errors after file save do not remove the saved file or record an orphan cleanup obligation.
Reproduction steps: Inject a database exception after `_save_upload` and before final commit.
Expected behavior: Either file and DB lifecycle are reconciled, or orphaned upload artifacts are
tracked and cleaned.
Observed behavior: The saved file can outlive a failed DB transaction with no corresponding run row.
Impact: Disk grows with orphaned artifacts, and forensic upload inventory becomes incomplete after
unexpected DB failures.
Root cause or likely cause: Filesystem side effect happens before durable database state exists.
Recommended remediation: Add try/except cleanup around unexpected DB failures, or create a pending
artifact record before file write and reconcile on startup.
Acceptance criteria: Injected DB failure after save leaves no orphan file, or creates a visible
cleanup record.
Regression tests required: Failure-injection upload artifact test using a temp upload directory.
Owner profile: Backend engineer
Dependencies: Decide whether failed artifacts should be retained for forensics.

## Action Backlog

Immediate:

- Fix the CERI migration chain so `alembic upgrade head` works from a clean database.
- Remove live ORM imports from migration revision files or freeze those definitions locally.
- Add a clean PostgreSQL migration smoke test to CI before further schema work lands.

Near term:

- Resolve Alembic metadata/index drift and make `uv run alembic check` a passing quality gate.
- Define CERI purge lifecycle semantics and align service, tests, UI, and route names with the
  decision.
- Add PostgreSQL rollback tests for delete-and-insert score refreshes and snapshot replacement.
- Add database constraints for stable quantitative invariants.

Structural:

- Establish a migration authoring rule: revision files are immutable and do not import application
  models.
- Build a data-lifecycle matrix for each evidence class: source, derived score, snapshot, alert,
  audit, export, upload artifact.
- Add representative high-volume fixture packs and `EXPLAIN (ANALYZE, BUFFERS)` review for history,
  run detail, CERI point-in-time, sector, winner, and setup lifecycle queries.

## Test Additions Proposal

- Clean PostgreSQL migration test: create temp DB, upgrade head, downgrade base, and upgrade through
  representative historical points.
- Static migration lint: fail when `alembic/versions/*.py` imports `app.models`.
- `alembic check` CI job.
- Constraint tests for non-negative price/volume, positive ranks, bounded probability/percentage
  fields, and valid lifecycle statuses where DB enums/checks are adopted.
- Transaction failure-injection tests for fundamental refresh, combined refresh, sector row
  replacement, market regime delete-for-run, full pipeline step failure, and upload artifact cleanup.
- CERI purge lifecycle tests using real PostgreSQL rows and assertions for audit preservation,
  source/derivative state transition, and rebuild obligations.
- Explain-plan tests or reviewed scripts for high-volume dashboards and JSONB-heavy query services.

## Decision Records Needed

- DR-PH4-001: Migration policy for pre-release drift: repair/squash existing revisions versus add
  corrective forward migrations.
- DR-PH4-002: Whether ORM metadata is the canonical home for all runtime indexes.
- DR-PH4-003: CERI licensed-data purge semantics: delete, tombstone, redact, quarantine, or audit
  only.
- DR-PH4-004: Database-level invariant policy for quantitative fields.
- DR-PH4-005: Upload artifact retention and orphan cleanup policy.

## Phase Scorecard

| Dimension | Rating | Rationale |
|---|---|---|
| Fresh install migrations | Red | Clean DB upgrade to head fails at CERI migration `0019` |
| Historical migration determinism | Red | Several migrations import mutable live ORM tables |
| Downgrade confidence | Red | Cannot complete downgrade smoke until fresh upgrade is repaired |
| Metadata/schema alignment | Amber/Red | `alembic check` fails on migration-created indexes missing from metadata |
| Referential integrity | Amber | Many FKs and uniqueness constraints exist, but delete semantics need lifecycle review |
| Transaction atomicity | Amber | Code generally flushes within caller transactions, but mid-failure tests are missing |
| Data retention and purge | Amber/Red | CERI purge is audited but not destructive/invalidation-capable |
| Timestamp handling | Green | Metadata consistently uses timezone-aware datetime columns |
| Query/index performance evidence | Amber | Many indexes exist; explain-plan evidence is deferred |
| Test coverage | Amber | 115 focused tests pass, but real PostgreSQL migration/rollback/concurrency gaps remain |

## Exit Report

Passed checks:

- Alembic has a single current head in the configured database.
- Focused schema, background-job, pipeline, upload, and CSV tests passed: `115 passed`.
- Metadata shows broad use of timezone-aware datetime fields, many natural-key uniqueness
  constraints, and explicit indexes in newer subsystems.
- Temporary database creation/drop worked, so a migration smoke harness is feasible.

Failed checks:

- Fresh PostgreSQL `alembic upgrade head` fails on an empty database.
- `uv run alembic check` fails because metadata and current database indexes diverge.
- Historical migration files import live ORM tables.
- CERI purge execution does not purge or invalidate affected data.

Deferred items:

- `EXPLAIN (ANALYZE, BUFFERS)` on representative high-volume queries.
- Full downgrade and representative historical upgrade matrix after migration repair.
- Real PostgreSQL rollback/failure-injection tests for refresh operations.
- Full retention review for JSONB-heavy evidence, upload artifacts, exports, and provider records.

Phase 4 status: not release-ready. The most important issue is the broken clean-database migration
path; fix that before relying on fresh installs, disaster recovery, or migration CI. After that,
the next layer is making migrations immutable, aligning Alembic metadata, and proving destructive
refresh and purge operations with real PostgreSQL tests.
