# SwingLens Final Wave and Phase 2 Consolidated Review Report

**Review date:** 2026-08-02  
**Review target:** SwingLens at the review baseline used by the supplied phase reports  
**Included primary phases:** 2, 16, 17, 18, 19, and 20  
**Phase 21 use:** Validation and final-decision cross-check only  
**Overall status:** **No-Go / Not release-ready**

## 1. Scope and Interpretation

This report consolidates the uploaded reviews for:

- Phase 2: Architecture, Modularity, and Dependency Direction
- Phase 16: User Experience, Explainability, and Accessibility
- Phase 17: Performance, Capacity, and Resource Safety
- Phase 18: Test Strategy, CI, and Release Quality Gates
- Phase 19: Observability, Operations, Backup, and Recovery
- Phase 20: Documentation, Maintainability, and Release Governance

Phase 21 is not treated as an additional source of new implementation findings because it is the final verification and overall release decision. It is used to:

- confirm which earlier findings remained open;
- confirm the final No-Go recommendation;
- cross-check regression, migration, backup, browser, CI, and governance status;
- update the earlier Phase 21 statement that no Phase 2 artifact was available.

Based on the prior wave sequence, Phases 16–20 are treated here as the final operational/governance wave. Phase 2 was omitted from the earlier wave consolidation on purpose or by workflow design and is now integrated as the architecture foundation.

## 2. Executive Summary

SwingLens has a recognizable and increasingly mature service-oriented architecture, a strong local automated test baseline, useful domain-specific operational surfaces, and extensive explainability in its newer analytical engines.

However, the final wave and Phase 2 reveal that the application has grown beyond the safety envelope of its original local MVP structure.

The principal release risks are not isolated defects. They are system-shaping gaps:

1. **Architecture boundaries are inconsistent.**  
   Shared ORM modules are central dependencies, routers contain workflow and persistence logic, imports create runtime side effects, and worker/domain modules contain dependency cycles.

2. **Release quality is not automated.**  
   The local test suite is green, but there are no committed CI workflows enforcing clean PostgreSQL migrations, browser smoke tests, real-database concurrency, coverage, security scans, dependency review, performance budgets, or golden-data governance.

3. **The application is not operations-ready.**  
   Backup/restore is undocumented and untested, readiness is shallow, app-wide metrics and alerting are absent, structured logging is inconsistent, and incident/rollback playbooks are missing.

4. **Capacity is only informally bounded.**  
   Upload bytes and page sizes have limits, but CSV row/column counts, query counts, memory, export size, chart history, queue depth, and disk cleanup are not fully controlled.

5. **Accessibility and evidence communication are inconsistent.**  
   Progress polling, charts, keyboard navigation, table semantics, async feedback, evidence provenance, and research-only disclaimers need cross-product standardization.

6. **Maintainability and governance are underdeveloped.**  
   Setup documentation contains a PostgreSQL port mismatch, top-level docs lag the actual route surface, high-risk ownership is undefined, ADRs and contributor controls are absent, and versioning rules are implicit.

### Final decision

The supplied Phase 21 report remains valid after adding Phase 2:

> **Overall release recommendation: No-Go.**

The Phase 2 report closes the review-coverage gap but strengthens the No-Go case. It identifies three High and three Medium architectural findings that connect directly to already open migration, security, testing, operations, and governance risks.

## 3. Finding Inventory

### 3.1 Formally identified source findings

The included reports contain **28 formally identified findings or defects**:

| Source severity scheme | Count |
|---|---:|
| S1 High | 3 |
| S2 Medium | 3 |
| P1 | 11 |
| P2 | 9 |
| P3 | 2 |
| **Total** | **28** |

Breakdown:

| Phase | Formal IDs | Count |
|---|---|---:|
| Phase 2 | `PH2-001` to `PH2-006` | 6 |
| Phase 16 | `UX16-01` to `UX16-08` | 8 |
| Phase 19 | `PH19-001` to `PH19-007` | 7 |
| Phase 20 | `PH20-001` to `PH20-007` | 7 |

Phases 17 and 18 use prioritized backlogs, quality gates, and exit-criteria gaps rather than numbered finding registers. Their issues are preserved as source-derived phase gap clusters rather than being assigned invented phase IDs.

### 3.2 Release-blocking themes

The source findings and gap registers consolidate into the following major gates:

1. Architecture and dependency boundaries
2. Migration and persistence ownership
3. UX, accessibility, and evidence provenance
4. Capacity, query, memory, queue, and retention safety
5. Test pyramid, CI, and release quality gates
6. Readiness, metrics, logging, alerting, and redaction
7. Backup, restore, incident response, and rollback
8. Documentation, ADRs, ownership, and versioning
9. Remediation verification and final release decision

## 4. Evidence Baseline

### 4.1 Architecture evidence

The architecture review reported:

- 210 Python modules under `app`;
- 652 internal import edges;
- `app.models.tables` imported by 88 modules;
- `app.models.ceri_tables` imported by 30 modules;
- eight detected dependency cycles;
- large concentration in shared services, routers, and model modules;
- direct persistence operations across router files;
- import-time creation of event-loop, settings-dependent directories, database engine, and sessions;
- architecture-focused regression slice: `82 passed, 1 warning`.

### 4.2 UX evidence

The Phase 16 review covered:

- uploads and dashboard;
- IB fetch planning and progress;
- full pipeline progress;
- run detail and cockpit;
- charts;
- market and sector dashboards;
- setup lifecycle;
- winner probability;
- CERI;
- HTML, JSON, CSV, and audit/export surfaces.

Focused route/export regression result:

```text
108 passed, 1 warning
```

### 4.3 Performance evidence

Phase 17 reported:

- upload limit default: 20 MB;
- page-size clamps across major APIs;
- conservative IB pacing;
- one local worker and capped lease event history;
- local upload footprint: 71 files, approximately 10.609 MB;
- focused resource suite: `48 passed, 1 warning`;
- current practical target: small to medium local research workloads;
- large workloads require batching, streaming, query budgets, and cleanup automation.

### 4.4 Testing evidence

Phase 18 reported:

- 175 test files;
- 943 collected tests;
- strong service/unit and feature-package coverage;
- only one integration test file;
- heavy use of fake database collaborators;
- no browser automation tooling;
- no coverage tooling;
- no property or mutation testing tooling;
- Ruff passed;
- full pytest passed;
- one Alembic head;
- offline Alembic SQL generation failed at migration `0014_sector_metadata`.

### 4.5 Operations evidence

Phase 19 reported:

- useful `/health` and `/ready` foundations;
- worker lifecycle, leases, retries, cancellation, and stale recovery;
- strong CERI-specific observability and redaction;
- focused operational slice: `75 passed, 1 warning`;
- no tested PostgreSQL backup/restore workflow;
- no external metrics system or alerts;
- no complete incident or rollback runbooks.

### 4.6 Documentation and governance evidence

Phase 20 reported:

- useful README and domain documentation base;
- domain release notes and persisted config/model versions;
- PostgreSQL setup port mismatch;
- missing top-level coverage for OWPE and CERI workflows;
- no `.github` directory;
- no `CODEOWNERS`;
- no `CONTRIBUTING.md`;
- no `CHANGELOG.md`;
- no ADR directory or index;
- no explicit multi-layer versioning policy.

### 4.7 Phase 21 validation evidence

Phase 21 confirmed:

- Ruff passed.
- Full suite passed: `943 passed, 1 warning`.
- Specialized slice passed: `459 passed, 1 warning`.
- Current configured database was at Alembic head.
- A clean PostgreSQL migration failed with a duplicate `retry_count` column.
- Backup/restore verification was not run.
- Browser smoke was not run.
- Security, static, dependency, and CI gates were not run.
- No remediation change set was available.

The green Python regression baseline is a health signal, not release closure.

# 5. Consolidated Release Gates

## 5.1 Gate A: Establish Enforceable Architecture Boundaries

**Priority:** Release-critical structural work  
**Source findings:** `PH2-001`, `PH2-002`, `PH2-003`, `PH2-004`, `PH2-005`, `PH2-006`

### Current problem

SwingLens uses two architectural generations:

- an older flat application layer with shared models, broad routers, direct SQL/session handling, and global initialization;
- newer domain packages with DTOs, repositories, query services, job handlers, config objects, and tests.

The newer pattern is stronger, but it has not been consistently applied to the older core.

### Specific risks

- Shared ORM modules create a large change blast radius.
- Historical migrations imported mutable model metadata.
- Routers own queries, commits, rollbacks, orchestration, and UI projection.
- Importing packages can create directories, event-loop state, engines, and sessions.
- Worker code imports domain handlers while handlers import worker contracts.
- Setup adapter helpers and registries form cycles.
- Providers, scoring engines, setup families, exports, and job handlers use inconsistent extension mechanisms.

### Required actions

1. Define and enforce layers:
   - app bootstrap;
   - routers;
   - command/application services;
   - query/projection services;
   - repositories;
   - pure domain engines;
   - provider adapters;
   - job contracts and handlers;
   - persistence models;
   - config loaders.
2. Split ORM ownership by domain:
   - core uploads/fundamentals;
   - jobs/pipeline;
   - market/sector;
   - setup lifecycle;
   - winner probability;
   - CERI.
3. Keep one metadata aggregator without making one module the implementation owner of all tables.
4. Extract route orchestration into command/query/projection services.
5. Establish one transaction boundary pattern.
6. Make settings resolution side-effect-free.
7. Move filesystem and database initialization into explicit bootstrap/lifespan paths.
8. Extract background-job contracts from the worker implementation.
9. Separate setup adapter contracts, helpers, and registry.
10. Standardize provider, scoring, job, setup-family, and export extension contracts.
11. Add architecture tests:
    - import-cycle scan;
    - forbidden dependency rules;
    - import-side-effect checks;
    - router persistence scan;
    - migration import lint.

### Acceptance gate

- No worker/domain or setup adapter import cycles remain.
- Importing application modules does not create directories or database engines.
- Routers delegate nontrivial persistence and workflow operations.
- Domain model ownership is discoverable.
- Extension contracts carry explicit version/config lineage.
- Architecture checks run in CI.

### Owner profiles

Architecture lead, backend owner, database owner, platform/operations owner.

---

## 5.2 Gate B: Repair Migration Ownership and Make Clean Database Creation a Required Gate

**Priority:** Stop-the-line  
**Source:** `PH2-001`, Phase 18 migration finding, Phase 21 validation

### Current problem

The architecture review connects shared ORM ownership directly to migration drift. Phase 18 found offline migration generation failure, while Phase 21 reproduced failure of a clean PostgreSQL upgrade.

Two different migration capabilities are currently unreliable:

1. offline SQL generation fails at a data-backfill migration;
2. clean database upgrade fails later in the chain because mutable live ORM metadata caused duplicate column creation.

### Required actions

1. Rewrite historical revisions that import live application models using frozen Alembic operations or revision-local table definitions.
2. Decide whether offline SQL generation is:
   - a supported release artifact that must be repaired; or
   - explicitly unsupported and removed from required gates.
3. Add clean PostgreSQL CI:
   - create empty DB;
   - upgrade to head;
   - verify current revision;
   - execute schema smoke queries.
4. Add representative prior-schema upgrade fixtures.
5. Add migration static lint:
   - reject `from app.models` in revisions;
   - check valid down revision;
   - enforce single head.
6. Add restore-to-clean-environment migration verification.
7. Link migration ownership to CODEOWNERS and PR checklist.

### Acceptance gate

- Empty PostgreSQL database upgrades to head.
- Supported historical upgrade fixtures pass.
- No revision imports mutable application model definitions.
- The documented offline migration policy is tested.
- A restored database can be validated at the expected schema head.

### Owner profile

Backend/database owner.

---

## 5.3 Gate C: Make Core Workflows Accessible and Operationally Explainable

**Priority:** P1/P2  
**Source defects:** `UX16-01` through `UX16-08`

### Current problem

SwingLens exposes many useful states, explanations, warnings, cutoffs, and lineage fields, especially in newer detailed views. The weakest areas are interaction semantics and cross-page consistency.

### Required actions

#### Progress and polling

- Add `role="progressbar"`.
- Maintain `aria-valuenow`, `aria-valuemin`, `aria-valuemax`, and `aria-valuetext`.
- Add polite live regions for step and status changes.
- Show visible connection-lost and retrying states after polling failures.

#### Charts

- Add accessible chart summary:
  - date range;
  - latest close;
  - trend state;
  - selected moving averages;
  - stop and target;
  - warning state.
- Add a compact table or text fallback.
- Add a non-color-only legend.

#### Keyboard navigation

- Add skip link to main content.
- Add `id="main-content"`.
- Add global high-contrast `:focus-visible` rules for interactive controls.

#### Tables

- Add captions.
- Add `scope="col"` and row-header semantics.
- Initialize and update `aria-sort`.

#### Evidence provenance

Create one shared badge/payload model containing:

- evidence mode;
- source cutoff;
- freshness;
- correction state;
- live-derived, reconstructed, or simulated status;
- model/config/calculation version;
- confidence;
- completeness.

Use it consistently in HTML, JSON, CSV, and Markdown.

#### Research-only guidance

- Place one concise shared disclaimer near every decision, probability, gate, and size-hint surface.
- Rename size guidance where appropriate to `Research size hint`.
- Keep the no-orders boundary visible near actionable-looking labels.

#### Async feedback and confirmations

- Add live regions to async admin and expansion actions.
- Preserve retry controls on failure.
- Map known errors to recovery steps.
- Replace generic browser confirms for high-impact actions with contextual confirmation panels.
- Show existing queued/running jobs instead of creating duplicate indistinguishable work.

### Acceptance gate

- Keyboard-only navigation covers the main workflows.
- Screen readers receive progress, async, and error changes.
- Charts have equivalent text/data access.
- Decision and evidence provenance is consistent across all output formats.
- High-impact actions expose target, impact, duplicate status, and recovery path.

### Owner profiles

Frontend/UI owner, accessibility reviewer, backend projection/API owner.

---

## 5.4 Gate D: Define and Enforce a Supported Capacity Envelope

**Priority:** P1/P2  
**Source:** Phase 17 performance and capacity review

### Current problem

Current controls are useful but incomplete. The application is likely suitable for small and medium local workloads, but large-run behavior is not bounded or proven.

### Current practical envelope from the source report

- upload bytes: configured maximum 20 MB;
- operational preference: at most approximately 5,000 CSV rows until row-count and memory refusal limits exist;
- run detail: approximately 1,000 tickers maximum before eager-load risk grows;
- technical scoring: medium batches;
- exports: current scale only, not large artifact guarantees;
- workers: one local worker and serialized heavy work.

### High-priority performance risks

1. Technical scoring performs per-ticker OHLCV loading and DataFrame construction.
2. Winner-probability run evidence loads all predictions and performs per-prediction estimate access before paging.
3. Run detail eagerly loads multiple complete relationships.
4. Large CSV/JSON exports materialize complete strings in memory.
5. High-cardinality APIs rely on offset paging.
6. Operations status paths can use unbounded list helpers.
7. Cleanup and retention declarations do not consistently execute.

### Required actions

#### Limits

- Add CSV row and column limits.
- Add maximum technical-refresh ticker threshold.
- Add maximum chart bars.
- Add export row and byte refusal thresholds.
- Add queue-depth limits per job type and run.
- Add statement timeouts for web requests.
- Use longer timeouts only in background workers.

#### Query architecture

- Batch-load OHLCV by ticker/data type/timeframe.
- Push filtering, sorting, and paging into SQL.
- Introduce server-side cockpit pagination and lazy detail fetch.
- Add keyset pagination for high-cardinality lists.
- Review history queries with real PostgreSQL plans.

#### Streaming and memory

- Stream high-volume CSV and JSON outputs.
- Avoid complete `StringIO` or full-object materialization where data can be generated incrementally.
- Add peak-memory tests for upload, technical scoring, winner training, and export.

#### Retention

Implement scheduled or operator-triggered cleanup for:

- uploads according to run/source retention;
- persisted exports;
- rebuildable caches;
- completed operational jobs;
- temporary winner-probability artifacts;
- rebuildable CERI artifacts;
- expired delivery attempts.

Preserve immutable evidence according to declared policy.

#### Budgets

Formalize and test the Phase 17 targets, including:

- dashboard/list p95;
- run-detail p95;
- 250-ticker technical scoring;
- 1,000-ticker fetch planning;
- large export time/size;
- setup lifecycle list APIs at 100,000 snapshots;
- winner API page performance;
- worker polling and heavy-job concurrency.

### Acceptance gate

- Capacity limits are documented and enforced before work begins.
- Common large paths do not load the entire dataset before paging.
- Query-count and peak-memory tests run against real PostgreSQL.
- Large exports stream or reject safely.
- Cleanup jobs keep disk and table growth within policy.
- Supported workload sizes are visible to users and operators.

### Owner profiles

Backend performance owner, database owner, operations owner.

---

## 5.5 Gate E: Convert Local Test Strength into Enforced CI and Release Gates

**Priority:** Release-blocking  
**Source:** Phase 18

### Current problem

The suite is broad but heavily unit/fake-database oriented. Local commands pass, yet no repository workflow enforces them.

### Required PR gates

1. Frozen dependency sync
2. Ruff
3. Full pytest
4. One Alembic head
5. Clean PostgreSQL upgrade
6. Secret scan
7. Dependency review

### Required release gates

1. All PR gates
2. Real PostgreSQL integration suite
3. Browser smoke suite
4. Coverage report and accepted-baseline comparison
5. Performance smoke suite
6. Golden fixture diff review
7. Representative prior-database upgrade
8. Backup restore and validation
9. Operational readiness check
10. Release checklist approval

### Test pyramid improvements

#### Real PostgreSQL

Add tests for:

- constraints;
- partial indexes;
- JSONB behavior;
- `FOR UPDATE SKIP LOCKED`;
- concurrent claims;
- transaction rollback;
- uniqueness races;
- clean migrations;
- representative upgrade paths.

#### Browser smoke

Use a browser framework for:

- upload;
- dashboard;
- run detail;
- progress polling;
- chart rendering;
- filters;
- keyboard navigation;
- export download;
- admin confirmation disabled/enabled paths;
- recovery from polling/API failures.

#### Coverage

- Add line and branch coverage.
- Establish a baseline.
- Ratchet by module rather than using an arbitrary single percentage.

#### Property-based tests

Cover:

- CSV normalization;
- numeric/date parsers;
- technical/gate monotonicity;
- lifecycle invariants;
- job state sequences;
- winner cutoff ordering;
- CERI correction and `AS_KNOWN` stability.

#### Mutation and fault injection

Test that the suite detects:

- inverted earnings gates;
- swapped risk mappings;
- removed cutoff filters;
- removed lease fencing;
- removed redaction;
- unauthorized golden changes.

#### Golden-data governance

- Require a dedicated PR label.
- Require before/after outputs and reason.
- Require config/model version impact.
- Store comparison artifacts.
- Block unreviewed fixture changes.

### Acceptance gate

- CI workflows are checked in.
- Branch protection requires them.
- Real DB and browser gates are release prerequisites.
- Coverage, performance, security, dependency, and golden checks are automated.
- Warnings are tracked and eventually fail the build after compatibility remediation.

### Owner profiles

Test/quality owner, platform owner, security owner, database owner.

---

## 5.6 Gate F: Build a Real Operational Readiness Surface

**Priority:** P1  
**Source findings:** `PH19-002`, `PH19-003`, `PH19-004`, `PH19-005`

### Current problem

The application has local diagnostic pages and strong CERI-specific observability, but no unified system-level readiness, metrics, logging, alerts, or redaction contract.

### Readiness requirements

Separate:

- `/health`: process liveness;
- `/ready`: operational readiness.

Readiness should report structured status for:

- database connectivity;
- Alembic revision;
- filesystem access;
- free disk capacity;
- worker enabled and alive;
- queue depth;
- stale jobs;
- pipeline health;
- export/cache growth;
- required config/version/hash checks;
- optional IB availability;
- optional CERI/provider availability;
- backup freshness;
- restore-validation freshness.

Use statuses such as:

- `ok`;
- `degraded`;
- `failed`;
- `optional_unavailable`.

Do not return raw exception details.

### Metrics and alerts

Add app-wide metrics for:

- pipeline success/failure/duration;
- background queue depth and stale jobs;
- retry and cancellation;
- IB fetch outcomes and freshness;
- scoring coverage;
- setup lifecycle evaluation;
- winner processing, calibration, and model health;
- CERI processing and provider health;
- exports;
- disk usage;
- cleanup;
- backup and restore-validation status.

Export through a standard endpoint or telemetry collector. Define alert thresholds and ownership.

### Logging and correlation

- Add request correlation middleware.
- Propagate correlation id into jobs and processing runs.
- Standardize event names.
- Include run, pipeline, job, provider, model, config, and request-key context.
- Use stable error codes.
- Test normal, retry, cancel, stale-recovery, and exception logs.

### Redaction

Promote the strongest redaction behavior into a shared service and apply it to:

- background errors;
- pipeline errors;
- upload errors;
- IB errors;
- readiness;
- logs;
- exports;
- operations views.

### Acceptance gate

- One correlation id traces request through background work to final status.
- Operators can configure alerts without scraping UI pages or querying PostgreSQL manually.
- Readiness detects migration, worker, queue, disk, and config problems.
- Operational errors never expose secrets, local paths, or SQL details.

### Owner profiles

Operations/platform owner, backend owner, security owner.

---

## 5.7 Gate G: Prove Backup, Restore, Incident Recovery, and Rollback

**Priority:** P1 / release-blocking  
**Source findings:** `PH19-001`, `PH19-007`

### Current problem

No tested backup/restore procedure or incident/rollback runbook exists.

### Required backup/restore artifacts

- `docs/operations/backup_restore.md`
- backup script
- restore script
- restore-validation script
- machine-readable validation report

### Restore validation must check

- schema head;
- critical row counts;
- foreign-key integrity;
- upload runs;
- pipeline runs;
- background jobs;
- market/sector evidence;
- setup episodes/events;
- winner predictions/outcomes/model artifacts;
- CERI source and revision evidence;
- config/model hashes;
- purge audit preservation;
- representative point-in-time queries.

### Required incident playbooks

- corrupted upload;
- failed or bad migration;
- database corruption;
- IB outage;
- provider outage;
- duplicate or stuck jobs;
- incorrect scoring/model release;
- leaked secret;
- disk exhaustion;
- bad config;
- bad model artifact;
- failed purge or cleanup;
- failed restore.

### Required rollback plans

- code;
- configuration;
- database schema;
- scoring engine;
- export schema;
- model artifact;
- provider adapter;
- operational policy.

### Acceptance gate

- A backup restores into a clean PostgreSQL environment.
- Validation produces pass/fail evidence.
- Release requires current backup and successful restore validation.
- Operators can execute incident and rollback procedures without reverse-engineering the codebase.

### Owner profiles

Operations owner, database owner, incident commander, domain owner.

---

## 5.8 Gate H: Create Maintainable Documentation and Governance

**Priority:** P1/P2  
**Source findings:** `PH20-001` through `PH20-007`

### Current problem

Documentation exists, but it does not yet form a reliable maintainer operating system.

### Setup

- Align PostgreSQL host-port instructions.
- Provide one clean Docker/local setup path.
- Use `uv run` commands.
- Include migration and `/ready` smoke checks.
- Test the README from a clean checkout.

### Top-level subsystem inventory

Add discoverable coverage for:

- fundamentals;
- technical/Pine parity;
- combined decisions;
- ranking profiles;
- market regime;
- sector rotation;
- setup lifecycle;
- winner probability;
- CERI;
- background jobs;
- admin and destructive operations;
- exports.

Generate or verify route/export inventory.

### Governance files

Add:

- `CONTRIBUTING.md`;
- pull request template;
- `CODEOWNERS`;
- changelog or release directory;
- migration checklist;
- scoring/model checklist;
- provider/licensing checklist;
- admin/security checklist;
- purge/retention checklist;
- release checklist.

### ADRs

Create ADRs for at least:

- local-only/no-orders boundary;
- persistence ownership;
- migration immutability;
- application layering;
- worker topology and leases;
- point-in-time evidence;
- research-only guidance;
- provider licensing and redaction;
- purge semantics;
- model promotion and rollback;
- universe/breadth semantics;
- backup and restore.

### Quantitative-engine documentation

Add compact maintainer references for:

- fundamental scoring;
- technical scoring and Pine parity;
- combined decisions and ranking profiles;
- winner probability.

Each must cover:

- inputs;
- formula;
- config;
- versions;
- persistence;
- evidence lineage;
- limitations;
- validation;
- fixtures;
- release rules.

### Versioning

Define written rules for:

- application version;
- Alembic revision;
- calculation/engine version;
- config schema version;
- config hash;
- export schema version;
- model artifact schema/version/hash;
- provider terms/version.

Automate checks requiring version bumps for high-risk contract changes.

### Stale development markers

- Rename unexplained phase/stub/pending identifiers.
- Document identifiers that must remain for compatibility.
- Add release checklist scan.

### Acceptance gate

- A new maintainer can set up, operate, test, and understand every subsystem from docs.
- High-risk files have named owners.
- PRs contain required checklists.
- Architectural choices are captured in ADRs.
- Version changes follow enforceable written rules.

### Owner profiles

Maintainer lead, architecture owner, documentation owner, domain owners.

# 6. Cross-Phase Dependency Map

The most important insight from combining Phase 2 with the final wave is that many operational and governance defects share architectural causes.

| Architectural condition | Downstream effects |
|---|---|
| Shared ORM model hub | Migration drift, wide review blast radius, unclear ownership |
| Workflow-heavy routers | Harder CSRF centralization, duplicated HTML/API behavior, difficult browser characterization |
| Import-time globals | Test isolation problems, alternate deployment friction, hidden DB/filesystem effects |
| Worker/domain cycles | Harder worker extraction, topology testing, and independent handler registration |
| Inconsistent extension points | Uneven versioning, documentation, ownership, and contract testing |
| Missing service boundaries | Inconsistent logging, redaction, metrics, errors, transactions, and capacity controls |
| No checked-in CI | Local correctness does not become merge/release protection |
| Missing CODEOWNERS/ADRs | High-risk architecture and model decisions have no mandatory review path |

This dependency map means remediation should not be organized as isolated tickets only. A few architectural extractions can unlock multiple security, UX, performance, testing, and operational improvements.

# 7. Prioritized Remediation Program

## Stage 0: Immediate operating restrictions

Until release gates are closed:

- keep use local and loopback-only;
- use one worker;
- avoid large runs and unbounded exports;
- manually reject duplicate high-impact actions;
- keep high-risk optional admin/purge/model operations disabled;
- do not rely on `/ready` as full operational readiness;
- take manual PostgreSQL backups before schema or model changes;
- treat restore capability as unproven;
- retain current evidence and config artifacts;
- do not describe the build as release-ready.

## Stage 1: Stop-the-line release blockers

1. Repair clean PostgreSQL migration.
2. Freeze migration definitions.
3. Add clean migration CI.
4. Create backup and restore scripts.
5. Prove restore validation.
6. Add required CI workflows.
7. Add central governance files and owners.
8. Define liveness versus readiness.
9. Add browser-local/security gates inherited from earlier phases.
10. Preserve Phase 21 No-Go until these checks pass.

## Stage 2: Architecture extraction

1. Side-effect-free settings and imports.
2. Explicit DB/bootstrap factories.
3. Split domain model ownership.
4. Extract job contracts and registry.
5. Remove worker/domain cycles.
6. Split setup adapter contracts/helpers/registry.
7. Extract run command/query/projection services.
8. Standardize extension contracts.

## Stage 3: Operational platform

1. App-wide structured logging.
2. Correlation IDs.
3. Shared redaction.
4. Metrics export.
5. Alert definitions.
6. Queue and stale-job visibility.
7. Disk and backup freshness.
8. Incident and rollback runbooks.

## Stage 4: Capacity and UX

1. Row, column, query, memory, export, chart, and queue limits.
2. Batch and SQL-side data access.
3. Streaming exports.
4. Cleanup jobs.
5. Accessible progress and polling.
6. Chart fallback.
7. Keyboard/focus remediation.
8. Evidence provenance badges.
9. Contextual confirmation and duplicate-job UI.

## Stage 5: Maintainability and release governance

1. Complete README and subsystem index.
2. Quant engine maintainer docs.
3. ADR backlog.
4. Versioning policy.
5. Changelog and release process.
6. Golden-data governance.
7. Property, mutation, and fault-injection expansion.
8. Browser, PostgreSQL, performance, and restore gates required for release.

# 8. Required Decision Records

| ID | Decision |
|---|---|
| DR-F-001 | What is the target application layering and dependency rule set? |
| DR-F-002 | Which domain owns each ORM table and migration? |
| DR-F-003 | What transaction boundary may routers use? |
| DR-F-004 | Are import-time directory, engine, session, or event-loop side effects allowed? |
| DR-F-005 | What is the supported worker topology? |
| DR-F-006 | What is the common extension protocol for providers, scoring, jobs, setup families, and exports? |
| DR-F-007 | Is offline Alembic SQL generation supported? |
| DR-F-008 | What workload sizes are supported for uploads, runs, technical scoring, exports, and queues? |
| DR-F-009 | What memory, query-count, response-size, and runtime budgets apply? |
| DR-F-010 | What is retained permanently, temporarily, or as rebuildable cache? |
| DR-F-011 | Which accessibility standard and browser/screen-reader matrix applies? |
| DR-F-012 | What is the canonical evidence-provenance payload? |
| DR-F-013 | What research-only disclaimer wording is mandatory? |
| DR-F-014 | Which PR and release checks are mandatory? |
| DR-F-015 | What coverage ratchet policy applies? |
| DR-F-016 | What changes require golden-fixture approval? |
| DR-F-017 | What constitutes operational readiness? |
| DR-F-018 | Which metrics and alerts are mandatory for local and shared deployment? |
| DR-F-019 | What shared redaction policy applies to every operational path? |
| DR-F-020 | What backup cadence, retention, encryption, and restore target apply? |
| DR-F-021 | Which incidents require tested playbooks? |
| DR-F-022 | What rollback guarantees exist for code, config, schema, and models? |
| DR-F-023 | Which high-risk files require named owners? |
| DR-F-024 | Which architectural decisions require ADRs? |
| DR-F-025 | What versioning rules apply to app, schema, engine, config, export, and model artifacts? |
| DR-F-026 | Which stale phase/stub identifiers are contractual and which should be renamed? |

# 9. Master Verification Plan

## 9.1 Architecture

- forbidden dependency test;
- import-cycle test;
- import-side-effect test;
- router SQL/session scan;
- application factory with multiple settings;
- worker registry import smoke;
- domain metadata ownership smoke.

## 9.2 Database and migration

- empty PostgreSQL upgrade;
- prior-schema fixture upgrade;
- single-head check;
- migration static lint;
- supported offline SQL generation;
- restore then migrate;
- schema/integrity validation.

## 9.3 UX and accessibility

- keyboard-only browser smoke;
- skip link and focus order;
- progressbar semantics;
- live polling status;
- chart text/data fallback;
- scoped table headers and captions;
- async error/retry announcement;
- evidence provenance across HTML and exports;
- research disclaimer consistency.

## 9.4 Capacity

- 5,000-row CSV time and peak memory;
- 1,000-ticker run detail query count;
- 250-ticker technical scoring;
- 2,000-prediction winner paging;
- 5,000-row CERI streaming export;
- 100,000-snapshot lifecycle query;
- export byte refusal;
- 100-job retry storm;
- cleanup dry run;
- disk threshold readiness.

## 9.5 CI and quality

- frozen lock;
- Ruff;
- full pytest;
- Python version matrix;
- PostgreSQL integration;
- browser smoke;
- line/branch coverage;
- security/static scan;
- dependency audit;
- performance smoke;
- golden review guard;
- warnings policy.

## 9.6 Operations

- liveness versus readiness;
- worker missing/dead;
- stale jobs;
- migration mismatch;
- disk low;
- optional provider down;
- correlation trace;
- metrics emission;
- alert firing;
- shared redaction;
- backup freshness.

## 9.7 Backup and incidents

- backup;
- restore to clean DB;
- validation report;
- bad migration rollback;
- corrupted upload;
- stuck/duplicate job;
- provider outage;
- IB outage;
- leaked secret;
- bad model/config rollback;
- purge failure;
- disk exhaustion.

## 9.8 Documentation and governance

- README clean-checkout test;
- generated route/export inventory;
- link checker;
- CODEOWNERS coverage;
- PR checklist enforcement;
- ADR index validation;
- version bump checks;
- changelog/release-note check;
- stale marker scan.

# 10. Traceability

## 10.1 Phase 2

| Finding | Consolidated gate |
|---|---|
| PH2-001 Shared ORM model module | Architecture; migration ownership |
| PH2-002 Route workflow and persistence | Architecture; UX/security centralization |
| PH2-003 Import-time globals | Architecture; testability; operations |
| PH2-004 Worker/handler cycles | Architecture; worker topology |
| PH2-005 Setup adapter cycles | Architecture; extension points |
| PH2-006 Inconsistent extensions | Architecture; versioning; governance |

## 10.2 Phase 16

| Defect | Consolidated gate |
|---|---|
| UX16-01 Progress semantics | UX/accessibility |
| UX16-02 Chart accessibility | UX/accessibility |
| UX16-03 Keyboard navigation | UX/accessibility |
| UX16-04 Table semantics | UX/accessibility |
| UX16-05 Evidence provenance | UX/explainability/governance |
| UX16-06 Research disclaimers | UX/safety communication |
| UX16-07 Async feedback | UX/operations |
| UX16-08 High-impact confirmations | UX/idempotency/operations |

## 10.3 Phase 17 source gap clusters

| Source cluster | Consolidated gate |
|---|---|
| Technical OHLCV per-ticker loading | Capacity/query architecture |
| Winner all-before-page access | Capacity/query architecture |
| Run-detail eager loading | Capacity/query architecture |
| In-memory exports | Capacity/resource limits |
| Missing row/column/chart/queue limits | Capacity/resource limits |
| Retention declarations without cleanup | Capacity/operations |
| Missing real-DB query/memory tests | CI/performance |

## 10.4 Phase 18 source gap clusters

| Source cluster | Consolidated gate |
|---|---|
| No checked-in CI | CI/release |
| Fake-database overuse | CI/test pyramid |
| Thin integration coverage | CI/test pyramid |
| No browser tests | CI/UX |
| No coverage tooling | CI/quality |
| Offline migration SQL failure | Migration |
| No clean PostgreSQL CI | Migration |
| Missing property/mutation/fault tests | CI/quality |
| Golden policy not enforced | Governance/release |

## 10.5 Phase 19

| Finding | Consolidated gate |
|---|---|
| PH19-001 Backup/restore absent | Backup/recovery |
| PH19-002 Readiness shallow | Operations |
| PH19-003 Metrics not exported | Operations |
| PH19-004 Logging/correlation inconsistent | Operations |
| PH19-005 Shared redaction incomplete | Operations/security |
| PH19-006 Cleanup/purge incomplete | Capacity/operations |
| PH19-007 Incident/rollback runbooks absent | Recovery/governance |

## 10.6 Phase 20

| Finding | Consolidated gate |
|---|---|
| PH20-001 Setup port mismatch | Documentation |
| PH20-002 Docs lag route surface | Documentation |
| PH20-003 Governance files absent | Governance |
| PH20-004 ADRs absent | Architecture/governance |
| PH20-005 Quant docs uneven | Documentation/model governance |
| PH20-006 Version policy implicit | Versioning/reproducibility |
| PH20-007 Stale phase/stub markers | Maintainability |

# 11. Phase 21 Consideration

Phase 21 is taken into consideration in the following limited way:

- Its No-Go decision is retained.
- Its verification results are treated as the final baseline.
- Its reproduction of the clean migration failure is treated as confirmation.
- Its statement that no Phase 2 artifact was available is now superseded by the supplied Phase 2 report.
- No finding is marked closed merely because the full suite passed.
- No remediation is assumed because the source report states that no remediation change set was provided.

The new Phase 2 evidence changes the final scorecard from:

```text
Phase 2: Not verified
```

to:

```text
Phase 2: Amber/Red
Architecture is recognizable and newer domains use stronger patterns, but shared ORM ownership,
workflow-heavy routers, import-time side effects, dependency cycles, and inconsistent extension
contracts require remediation.
```

This closes an assessment gap, not a software risk.

# 12. Final Release Gate

## No-Go conditions that remain open

- clean database migration fails;
- historical migration ownership is unsafe;
- CI workflows are absent;
- real PostgreSQL gates are absent;
- browser smoke and accessibility gates are absent;
- backup/restore is unproven;
- operational readiness is incomplete;
- app-wide metrics and alerts are absent;
- incident and rollback procedures are absent;
- capacity budgets are not enforced;
- cleanup execution is incomplete;
- architecture dependency boundaries are not enforced;
- documentation and governance are insufficient for high-risk maintenance;
- earlier security, temporal-evidence, and data-integrity blockers remain open according to Phase 21.

## Permitted interim posture

- local research only;
- loopback-only;
- trusted data and operator;
- one worker;
- moderate workload;
- no broad/shared release claim;
- high-risk optional/admin/destructive operations disabled;
- manual backup before material changes;
- explicit acknowledgement that restore is unverified.

## Conditions for reconsidering Go

A new final verification should be run only after a remediation change set is available. At minimum it must include:

1. clean PostgreSQL migration;
2. backup and successful restore validation;
3. required CI workflows;
4. real PostgreSQL integration;
5. browser smoke and accessibility checks;
6. operational readiness and redaction;
7. Phase 17 performance budgets;
8. architecture dependency checks;
9. governance and versioning controls;
10. re-verification of all earlier S0/S1 findings.

# 13. Final Assessment

SwingLens is an ambitious local research system with a deep feature surface and a strong unit-level safety net. Its newer domains show a clear evolution toward better packaging, lineage, configuration, and operational modeling.

The software has now crossed the threshold where local discipline and broad unit testing are not enough.

The final challenge is to turn implicit engineering knowledge into enforced system boundaries:

- architecture rules instead of conventions;
- clean migrations instead of a database that happens to be current;
- CI gates instead of successful local commands;
- operational readiness instead of diagnostic pages;
- restore proof instead of presumed recoverability;
- capacity limits instead of hopeful workload assumptions;
- accessible evidence semantics instead of visually rich but uneven interfaces;
- ADRs, ownership, and versioning instead of tribal knowledge.

Phase 2 explains why many of the final-wave gaps recur. The original flat architecture became the root system beneath a much larger tree. The branches are sophisticated, but the trunk now needs reinforcement.

The correct release decision remains **No-Go** until the release, operations, architecture, and recovery gates are implemented and verified.
