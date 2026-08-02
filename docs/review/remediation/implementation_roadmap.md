# SwingLens Remediation Implementation Roadmap

Date: 2026-08-02

## Source and Scope Notes

The requested consolidated wave files were not present in this checkout. This roadmap is based on
the available phase review reports and the Phase 21 final release decision.

Do not implement unrelated findings in the migration blocker PR. Do not update golden fixtures. Do
not change scoring behavior.

## Baseline Verification Results

Commands were run on 2026-08-02 from `C:\Users\Ivica\Documents\SwingLens`.

| Check | Command | Result | Notes |
| --- | --- | --- | --- |
| Lint | `uv run ruff check app tests` | Passed | `All checks passed!` |
| Full tests | `uv run pytest -q` | Passed | `943 passed, 1 warning in 78.45s`; warning is Starlette/httpx `TestClient` deprecation |
| Alembic head graph | `uv run alembic heads` | Passed | Single head: `0021_add_ceri_earnings_consensus_reason (head)` |
| Current configured DB revision | `uv run alembic current` | Passed | `0021_add_ceri_earnings_consensus_reason (head)` |
| Clean PostgreSQL migration | Disposable DB `swinglens_remediation_clean`; `uv run alembic upgrade head` | Failed | Fails at `0019_add_ceri_ingestion_audit_fields` with duplicate `ceri_ingestion_runs.retry_count`; disposable DB was dropped |
| Metadata drift | `uv run alembic check` | Failed | Detects seven removed indexes: `idx_combined_results_complete`, `idx_combined_results_decision`, `idx_combined_results_score`, `idx_combined_results_ticker`, `idx_combined_results_warning`, `idx_upload_runs_status`, `idx_upload_runs_uploaded_at_desc` |
| Metadata/schema tests | `uv run pytest tests/test_schema_phase2.py tests/setup_lifecycle/test_setup_lifecycle_schema.py tests/winner_probability/test_schema.py tests/ceri/test_ceri_schema.py -q` | Passed | `57 passed in 6.32s` |
| Metadata inventory | `Base.metadata` import/count probe | Completed | `tables=60 indexes=144 unique_constraints=45 check_constraints=3 jsonb_columns=145 numeric_columns=148` |
| Migration import scan | `Select-String alembic/versions/*.py` for `app.models` imports | Failed | Live model imports in revisions `0016`, `0017`, `0018` |

## First Implementation Plan: Clean PostgreSQL Migration Blocker

### Objective

Make a clean PostgreSQL database upgrade to Alembic head and add automated protection so the
failure cannot recur. This plan only covers MF-001 and the migration subset of MF-002/MF-025.

### Current Blocker

`0018_add_ceri_tables` imports live `CERI_TABLES` from `app.models.ceri_tables`. The current model
already includes `ceri_ingestion_runs.retry_count` and `checkpoint_json`. The next migration,
`0019_add_ceri_ingestion_audit_fields`, tries to add those same columns. A clean database therefore
fails before reaching head.

Three revision files also import live app model metadata:

- `alembic/versions/20260731_0016_add_winner_probability_engine.py`
- `alembic/versions/20260801_0017_create_setup_lifecycle_tables.py`
- `alembic/versions/20260801_0018_add_ceri_tables.py`

Do not modify migration files until the deployment-support question below is answered.

### Strategy 1: Clean/Squashed Pre-Release Migration Baseline

Use this if the repository does not need to support upgrades of already deployed external
databases.

Scope:

- Replace the drift-prone pre-release chain with one clean baseline revision or repair/squash the
affected late revisions so they express the current intended head schema directly.
- Remove live ORM imports from revisions by writing frozen Alembic operations or local `sa.Table`
definitions.
- Make `0019` no longer add columns that the immediately prior revision already creates, or fold
the `0019` intent into the baseline.
- Align metadata/index drift by adding the seven migration-created indexes to ORM metadata or adding
documented Alembic filters.
- Add static migration import lint and clean PostgreSQL migration CI.

Benefits:

- Smallest long-term migration surface for an unreleased local tool.
- Clean installs, CI, and disaster-recovery restores become straightforward.
- Avoids preserving broken historical migration behavior.

Risks:

- Any external DB whose `alembic_version` points to old revisions may not be able to upgrade without
manual stamping or a compatibility bridge.
- Downgrade history before the new baseline may be intentionally discarded.

Acceptance tests:

- Clean PostgreSQL `alembic upgrade head` passes.
- `alembic current` reports `0021_add_ceri_earnings_consensus_reason` or a new approved baseline
head.
- Static lint rejects `app.models` imports in all revision files.
- `alembic check` passes or documented filters are covered by tests.
- Existing full suite stays green.

### Strategy 2: Forward Corrective Migrations Preserving Existing Revision History

Use this if any external deployed database must upgrade from existing revision history.

Scope:

- Preserve existing revision IDs and add forward corrective migrations for live databases that have
already reached head or a prior revision.
- Add compatibility logic or branch-specific repair paths only where necessary to make externally
deployed databases safe.
- Separately repair clean-install behavior. Because the current clean failure occurs before any
new forward revision can run, this strategy may still require editing historical revision `0018` or
introducing a replacement baseline path plus upgrade-fixture tests.
- Add upgrade tests for at least one representative already-deployed database snapshot.
- Add clean PostgreSQL migration CI, migration import lint, and metadata drift checks.

Benefits:

- Protects existing external users and databases.
- Keeps historical revision IDs meaningful for already deployed environments.

Risks:

- More complex and easier to get wrong because clean installs fail before a forward fix can run.
- Requires real deployed DB snapshots or at least exact `alembic_version` and schema-state evidence.
- May need both a historical repair and a forward compatibility migration.

Acceptance tests:

- Clean PostgreSQL `alembic upgrade head` passes.
- Upgrade from every supported prior external revision snapshot passes.
- Current configured DB at head remains compatible without data loss.
- Static migration import lint and `alembic check` pass.
- Full suite stays green.

### Information Needed Before Choosing

The maintainer/release owner must provide or confirm:

- Whether SwingLens has ever been deployed outside this local repository/workstation.
- Whether any external PostgreSQL databases exist that must upgrade without manual rebuild.
- For every such database: current `alembic_version`, SwingLens commit/version used to create it,
  and whether it has CERI/winner/setup lifecycle tables populated.
- Whether those databases can be backed up, rebuilt from source data, or manually stamped after a
  clean baseline.
- Whether downgrade support is required, or only forward upgrade and restore-to-head.
- Whether release history before Phase 21 is considered pre-release/internal and can be squashed.
- Whether provider/licensed CERI data exists in any database, because purge/lifecycle obligations
  may affect backup and migration tests.
- Which PostgreSQL setup is canonical for CI and local docs: native `127.0.0.1:5432` or Docker
  Compose host port `5433`.

Recommended decision rule:

- If no external database requires revision-history upgrades, choose Strategy 1.
- If any external database must be upgraded in place, choose Strategy 2 and require an upgrade
  fixture before editing migrations.

Selected decision: Strategy 1, clean/squashed pre-release migration baseline. The repository is
treating the late CERI migrations as pre-release cleanup scope; clean installs and local recovery are
the priority over preserving upgrades from externally deployed intermediate databases.

## Pull Request Roadmap

### Batch 1: Clean Migrations and Migration CI

#### PR 1.1 - Migration Strategy and Baseline Test Harness

Source finding IDs: PH2-001, PH4-001, PH4-002, PH4-003, PH18 migration gate, PH21 clean migration.

Precise scope:

- Record the chosen migration strategy after deployment-support facts are supplied.
- Add a migration test harness that can create a disposable PostgreSQL database, run
  `alembic upgrade head`, verify current revision, and drop the database.
- Add static lint preventing `app.models` imports from Alembic revision files.

Non-goals:

- No migration file edits until the strategy decision is made.
- No scoring, golden fixture, CERI purge, or route-security changes.

Prerequisites:

- Answer the deployment-support questions in the first implementation plan.
- Confirm CI PostgreSQL connection details.

Acceptance tests:

- Harness fails on current clean migration blocker before migration fix.
- Static migration import lint reports revisions `0016`, `0017`, and `0018`.
- Existing lint and full suite remain green.

Rollback concerns:

- Test-only additions can be reverted safely; no schema change yet.

Implementation note: `tests/test_migration_remediation.py` now contains the disposable PostgreSQL
clean-upgrade harness and the static live-model import scanner. The clean migration harness passes
after the CERI baseline repair.

#### PR 1.2 - Repair Clean Migration Chain

Source finding IDs: PH2-001, PH4-001, PH4-002, Phase21 clean migration.

Precise scope:

- Apply the chosen migration strategy.
- Freeze or replace live-model imports in migrations.
- Ensure CERI `0018`/`0019` semantics are coherent for clean upgrade.
- Preserve external upgrade path if Strategy 2 is chosen.

Non-goals:

- Do not change runtime CERI models except where required to match intended schema.
- Do not implement purge lifecycle or scoring changes.

Prerequisites:

- PR 1.1 merged.
- Backups/snapshots captured if external DBs exist.

Acceptance tests:

- Clean PostgreSQL `alembic upgrade head` passes.
- Supported upgrade fixtures pass.
- `uv run pytest -q` passes.
- Migration import lint passes.

Rollback concerns:

- Migration-history edits are hard to roll back after external use. Require backup and explicit
  release note before merge.

Implementation note: revisions `0016`, `0017`, and `0018` now use frozen table/index DDL instead of
importing live ORM table objects. The static migration import scanner enforces zero `app.models`
imports under `alembic/versions`.

#### PR 1.3 - Metadata Alignment and Migration CI

Source finding IDs: PH4-003, PH0-001, PH18, PH20-003.

Precise scope:

- Add missing ORM indexes or documented Alembic include filters.
- Make `uv run alembic check` pass.
- Add CI workflow for dependency sync, lint, tests, Alembic heads, clean migration, and metadata check.

Non-goals:

- No branch protection changes unless admin access is available.

Prerequisites:

- PR 1.2 merged.

Acceptance tests:

- `uv run alembic check` passes.
- CI migration job passes against PostgreSQL.
- Full suite remains green.

Rollback concerns:

- Index metadata additions are low risk but autogenerate behavior changes; inspect generated diffs.

Implementation note: metadata/index drift is clean under `uv run alembic check`, and
`.github/workflows/ci.yml` now enforces locked dependency sync, lint, Alembic head/current/check,
the disposable clean PostgreSQL migration harness, and the full test suite against PostgreSQL.

### Batch 2: Centralized State-Changing Route Security

#### PR 2.1 - Unsafe Route Inventory and Guard Framework

Source finding IDs: PH1-001, PH1-002, PH3-001, PH11-003, PH15-001, PH15-002, PH15-003.

Precise scope:

- Generate/classify all unsafe routes.
- Add shared local-admin and CSRF dependencies.
- Add Host/debug/public-bind validation and tests.

Non-goals:

- No business workflow refactors except those required to apply guards.

Prerequisites:

- D-004 and D-005 accepted.

Acceptance tests:

- Route-map tests fail for unclassified POST routes.
- Persisted setup lifecycle replay requires confirmation, reason, and requester.
- Static/query-string CERI CSRF tokens are rejected.
- Host spoof and public-debug bind tests pass.

Rollback concerns:

- Guard rollout may block existing local workflows; use explicit exemptions with comments/tests.

Implementation note: `app/security.py` now centralizes unsafe-route classification, local-admin
host checks, generated header-only CSRF tokens, TrustedHost middleware setup, and public-bind/debug
settings validation. All current state-changing routes are classified by the route inventory test.
CERI admin routes reject static/query-string CSRF tokens and setup lifecycle persisted replay now
requires confirmation, reason, and requester.

#### PR 2.2 - No-Order Boundary Gate

Source finding IDs: PH1-003, PH15-006.

Precise scope:

- Add repository-wide no-order static scan.
- Add fake-IB read-only runtime assertions.

Non-goals:

- No IB identity or bar-quality changes.

Prerequisites:

- D-038 accepted.

Acceptance tests:

- Forbidden broker-order calls/routes fail tests.
- Existing IB service tests remain green.

Rollback concerns:

- Static terms can be noisy; keep allowlist narrow and documented.

Implementation note: `tests/test_no_order_boundary.py` now enforces a first-party app static scan
for concrete broker-order APIs/classes and fake-IB runtime assertions that connection and fetch
paths use `readonly=True` and do not invoke order-capable methods.

### Batch 3: Temporal Correctness and No-Look-Ahead

#### PR 3.1 - Technical Pivot and Relative-Strength Causality

Source finding IDs: PH8-001, PH8-003.

Precise scope:

- Shift score-producing pivot state to confirmation date.
- Make relative-strength alignment degrade to insufficient data.

Non-goals:

- Do not update golden fixtures in this PR unless an explicitly approved scoring-change process is
  created first.

Prerequisites:

- D-035 golden governance in place if behavior changes affect fixtures.

Acceptance tests:

- No-look-ahead pivot fixtures.
- Empty alignment returns missing/low-confidence flags.

Rollback concerns:

- Scoring movement is likely; gate behind explicit model/version decision.

#### PR 3.2 - As-Of Market/Sector Context Selection

Source finding IDs: PH10-001, PH11-002, PH11-005.

Precise scope:

- Replace latest-global fallback with `as_of <= cutoff` lookups.
- Emit missing-context warnings when no eligible context exists.

Non-goals:

- No immutable revision work yet.

Prerequisites:

- Evidence cutoff vocabulary from D-006.

Acceptance tests:

- Future-only global context is not attached.
- Missing eligible context is explicit.
- Generated lifecycle transition tests preserve terminal, active-episode, state-age, and replay
  invariants.

Rollback concerns:

- Historical recalculations may become lower confidence; document expected output changes.

#### PR 3.3 - Winner and CERI Cutoff Audit Integration

Source finding IDs: PH12-004, PH13-002.

Precise scope:

- Invoke winner per-feature cutoff validators.
- Implement or rename CERI score-history modes.

Non-goals:

- No winner promotion gate changes.
- No CERI purge implementation.

Prerequisites:

- D-033 resolved.

Acceptance tests:

- Feature after cutoff fails/nulls by policy.
- AS_KNOWN and LATEST_CORRECTED responses diverge in correction fixture, or endpoint is renamed.

Rollback concerns:

- Public API semantics may change; version response metadata.

### Batch 4: Immutable Evidence and Correction Lineage

#### PR 4.1 - Price-Bar Revision and Source Correction Lineage

Source finding IDs: PH6-003, PH13-001.

Precise scope:

- Add append-only price-bar revision records.
- Support corrected provider records with supersession chain.

Non-goals:

- No purge lifecycle changes.

Prerequisites:

- Clean migration CI from Batch 1.

Acceptance tests:

- Revised bar preserves prior values.
- Corrected same provider record creates auditable successor.

Rollback concerns:

- New tables/data are evidence-bearing; take backup before migration.

#### PR 4.2 - Immutable Market/Sector/Setup/Combined Evidence

Source finding IDs: PH9-003, PH10-002, PH11-001.

Precise scope:

- Add append-only revisions or calculation debug payloads.
- Separate current/latest materialized pointers from immutable evidence.

Non-goals:

- No route UI redesign beyond minimal evidence display needed for tests.

Prerequisites:

- D-006 accepted.

Acceptance tests:

- Reprocessing revised inputs creates new revision or reconstructable debug evidence.
- Old evidence remains queryable.

Rollback concerns:

- Schema changes affect historical views; require backup and migration test.

### Batch 5: Background-Job Idempotency and Concurrency

#### PR 5.1 - Queue Request Keys and Duplicate Coalescing

Source finding IDs: PH14-001, PH14-004, PH14-006.

Precise scope:

- Add queue-level request key and active-job uniqueness.
- Convert CERI/winner/setup/pipeline enqueue paths to shared coalesce API.

Non-goals:

- No UI duplicate-action improvements yet.

Prerequisites:

- D-019 and D-020 accepted.

Acceptance tests:

- Two-session PostgreSQL duplicate enqueue tests.
- Existing fake DB tests updated only as needed.

Rollback concerns:

- Unique constraints can conflict with existing duplicate active jobs; include pre-migration cleanup/report.

#### PR 5.2 - Lease Fencing, Retry Semantics, and Token Redaction

Source finding IDs: PH14-002, PH14-003, PH14-005, PH4-006, PH13-004, PH19-005.

Precise scope:

- Add lease guard before side-effect commits.
- Document and test per-step resume/replay behavior.
- Redact execution tokens and job payload/result/error surfaces.

Non-goals:

- No performance queue-depth limits.

Prerequisites:

- D-021 and D-023 accepted.

Acceptance tests:

- Stale old worker cannot commit after lease loss.
- Raw tokens and sensitive payloads do not appear in job status/log/export surfaces.

Rollback concerns:

- Fencing can cause jobs to abort where they previously completed; expose clear retry status.

### Batch 6: Ingestion, Ticker Identity, IB Identity, Bar Quality, and Export Safety

#### PR 6.1 - Strict CSV Loader and Upload Artifact Lifecycle

Source finding IDs: PH5-002, PH5-004, PH5-005, PH4-007.

Precise scope:

- Enforce header/width/dialect policy.
- Harden filenames, non-seekable streams, and DB-failure cleanup.

Non-goals:

- No duplicate ticker model beyond rejection/quarantine policy chosen in D-010.

Prerequisites:

- D-009, D-012, D-013 accepted.

Acceptance tests:

- Hostile CSV and upload artifact failure fixtures pass.

Rollback concerns:

- Stricter parsing may reject previously accepted malformed files; release note required.

#### PR 6.2 - Canonical Ticker/Instrument Identity

Source finding IDs: PH5-003, PH7-003, PH6-001.

Precise scope:

- Enforce duplicate ticker policy before scoring.
- Treat ambiguous IB contracts by policy.

Non-goals:

- No full exchange-qualified identity redesign unless selected.

Prerequisites:

- D-010 and D-025 accepted.

Acceptance tests:

- Duplicate/conflicting rows cannot blend across downstream services.
- Multiple IB qualified contracts do not silently pick first.

Rollback concerns:

- Existing duplicate runs may need migration/read-only compatibility.

#### PR 6.3 - Bar Quality, Price Source, and Export Safety

Source finding IDs: PH5-001, PH5-006, PH6-002, PH6-004, PH6-005, PH6-006, PH8-004, PH8-005, PH8-006.

Precise scope:

- Apply spreadsheet formula neutralization.
- Add export schema IDs.
- Implement selected price/volume/stale/bar-quality policies.

Non-goals:

- No Pine fixture yet unless price-source policy is settled.

Prerequisites:

- D-011, D-024, D-026 accepted.

Acceptance tests:

- Export formula tests for all CSV families.
- Split adjusted/trades fixture.
- Duplicate/gap/invalid OHLC and stale-threshold tests.

Rollback concerns:

- Price-source changes may move scores; coordinate with golden governance.

### Batch 7: Scoring, Ranking, and Probability Semantics

#### PR 7.1 - Fundamental and Config Governance

Source finding IDs: PH3-002, PH3-003, PH3-004, PH3-005, PH7-001, PH7-002, PH7-004, PH7-005, PH7-006.

Precise scope:

- Add typed config validation/hash lineage.
- Resolve `Quality risk` label/penalty.
- Add formula contract and broader golden/property tests.

Non-goals:

- No behavior changes without version/golden decision.

Prerequisites:

- D-006, D-007, D-008, D-029, D-035 accepted.

Acceptance tests:

- Invalid configs fail.
- Label/risk boundary tests pass.
- Golden governance requirements are enforced.

Rollback concerns:

- Model semantics can change; require version bump and review.

#### PR 7.2 - Combined, Ranking, Market/Sector Semantics

Source finding IDs: PH9-001, PH9-002, PH9-004, PH9-006, PH10-003, PH10-004, PH10-005.

Precise scope:

- Resolve inert missing-data policy.
- Add growth-trap cap or documented override.
- Normalize labels and market/sector confidence wording.

Non-goals:

- No UI-wide accessibility work beyond necessary label display.

Prerequisites:

- D-015, D-027, D-028, D-030, D-031 accepted.

Acceptance tests:

- Missing-data knob tests.
- Contradiction and threshold matrix tests.
- ETF/universe wording tests.

Rollback concerns:

- Ranking output may move; gate with version/golden process.

#### PR 7.3 - Winner Probability Promotion and Visibility

Source finding IDs: PH12-001, PH12-002, PH12-003.

Precise scope:

- Enforce registry algorithm allow-list.
- Require persisted quantitative gate report.
- Display model/calibration status in UI/API/export.

Non-goals:

- No new algorithms.

Prerequisites:

- D-032 and versioning policy accepted.

Acceptance tests:

- Unapproved registered model cannot promote.
- Missing/stale gate metrics block promotion.
- Estimate views show calibration/model state.

Rollback concerns:

- Previously promotable models may be blocked; keep shadow fallback visible.

### Batch 8: CERI Purge and Licensed-Data Lifecycle

#### PR 8.1 - CERI Purge Lifecycle Decision Implementation

Source finding IDs: PH4-004, PH13-003, PH19-006.

Precise scope:

- Implement or rename audit-only purge according to D-016.
- Add derivative invalidation/rebuild obligations where purge restricts data.

Non-goals:

- No provider integration expansion.

Prerequisites:

- Legal/provider decision D-016.
- Backup/restore procedure available before destructive/invalidation migration.

Acceptance tests:

- Purge preview/execute changes data availability according to policy.
- Affected exports/queries are blocked or marked invalidated until rebuild.

Rollback concerns:

- Destructive or restrictive lifecycle changes require backup and explicit recovery plan.

#### PR 8.2 - Setup Lifecycle Purge Policy Gate

Source finding IDs: PH11-004.

Precise scope:

- Prevent repository purge execution while config disables purge.
- Route all purge behavior through policy-aware service.

Non-goals:

- No setup lifecycle evidence redesign.

Prerequisites:

- D-017 accepted.

Acceptance tests:

- Purge execution rejected when `purge_enabled=false`.

Rollback concerns:

- Minimal; blocks an internal unsafe path.

### Batch 9: Backup, Restore, Readiness, Metrics, and Incident Operations

#### PR 9.1 - Backup/Restore and Restore Validation

Source finding IDs: PH19-001, PH20-001, Phase21 backup residual risk.

Precise scope:

- Add backup/restore runbook and scripts.
- Add restore validation command/report covering schema head, row counts, FKs, evidence hashes.
- Align local PostgreSQL setup docs.

Non-goals:

- No hosted deployment automation.

Prerequisites:

- D-034 accepted.

Acceptance tests:

- Restore into clean DB validates successfully from representative backup.

Rollback concerns:

- Scripts are additive; verify they cannot target/drop non-disposable DBs without confirmation.

#### PR 9.2 - Readiness, Metrics, Logging, Redaction, and Runbooks

Source finding IDs: PH19-002, PH19-003, PH19-004, PH19-005, PH19-007.

Precise scope:

- Split liveness/readiness semantics.
- Add migration/worker/storage/job readiness checks.
- Add metrics/log schema and incident/rollback runbooks.
- Apply shared redaction to readiness/errors.

Non-goals:

- No full observability stack deployment.

Prerequisites:

- D-022 and D-023 accepted.

Acceptance tests:

- Readiness degrades for stale jobs/migration mismatch/storage failure.
- Metrics emit for job/pipeline/export paths.
- Error strings are redacted.

Rollback concerns:

- Stricter readiness can mark app unavailable until environment is fixed.

### Batch 10: UX, Accessibility, Performance, Documentation, and Governance

#### PR 10.1 - UX Accessibility and Evidence Provenance

Source finding IDs: PH15-005, UX16-01, UX16-02, UX16-03, UX16-04, UX16-05, UX16-06, UX16-07, UX16-08, PH9-005.

Precise scope:

- Add skip link/focus-visible, progress live semantics, chart summary/fallback, table captions/scope.
- Add shared evidence provenance and research disclaimer components.
- Surface duplicate/already-running action state after job coalescing exists.

Non-goals:

- No design-system rewrite.

Prerequisites:

- Batch 5 duplicate coalescing; D-015 accepted.

Acceptance tests:

- Route/browser accessibility smoke tests for progress/chart/tables/forms.
- Hostile-content fixtures remain escaped/inert across upload, run detail, setup lifecycle, CERI,
  winner probability, market/sector, and export surfaces.
- Provenance appears in HTML/export metadata where decisions/probabilities are shown.

Rollback concerns:

- Template changes are broad; keep each surface covered by snapshot/route tests.

#### PR 10.2 - Performance Budgets and Cleanup Jobs

Source finding IDs: Phase17 performance/resource review, PH19-006.

Precise scope:

- Enforce row/byte/page/query limits.
- Add streaming/refusal for large exports.
- Add cleanup dry-run/execution for rebuildable artifacts.

Non-goals:

- No large-enterprise scale promise.

Prerequisites:

- D-036 and retention decisions accepted.

Acceptance tests:

- Query-count/memory/export refusal tests.
- Cleanup dry-run and execution tests.

Rollback concerns:

- Users with large local files may hit new limits; document override path only where safe.

#### PR 10.3 - Documentation, ADRs, Versioning, and Maintainer Governance

Source finding IDs: PH1-004, PH1-005, PH20-002, PH20-003, PH20-004, PH20-005, PH20-006, PH20-007.

Precise scope:

- Add route/export inventory, glossary, versioning policy, maintainer docs, ADR template/index,
  CONTRIBUTING, CODEOWNERS, PR template, changelog/release checklist.
- Mark XLSX as deferred or implement separately if product chooses.

Non-goals:

- No broad architectural refactor beyond documentation/governance.

Prerequisites:

- D-014, D-015, D-035, D-037 accepted.

Acceptance tests:

- Docs drift checks where scripts exist.
- Review checklist covers migrations, model changes, exports, purge, and admin routes.

Rollback concerns:

- Governance docs are additive; avoid assigning owners who have not accepted ownership.
