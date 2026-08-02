# SwingLens Wave A Consolidated Software Review Report

**Review date:** 2026-08-02  
**Review target commit:** `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`  
**Repository:** `ivobog/swing-lens`  
**Overall status:** **Not release-ready**  
**Recommended operating posture:** Localhost-only, single-worker, research-only mode until the blocking findings are closed.

## 1. Purpose

This report consolidates the following Wave A reviews into one actionable assessment:

1. `phase0_baseline.md`
2. `phase1_requirements_traceability.md`
3. `phase3_configuration_feature_flags.md`
4. `phase4_database_migrations_transactions.md`
5. `phase14_background_jobs_concurrency_recovery.md`
6. `phase15_web_security_local_admin.md`

The consolidation preserves all original finding IDs, removes duplicated diagnosis, and organizes remediation around release risk and dependency order.

## 2. Executive Summary

SwingLens has a strong functional baseline and a broad automated test suite. The reviewed baseline completed dependency synchronization, linting, the full test suite, and the golden pipeline successfully. The advanced analytical subsystems also show thoughtful safeguards around point-in-time evidence, data lineage, read-only broker access, advisory boundaries, feature gating, and row-level background-job fencing.

The principal problem is not missing functionality. It is that several cross-cutting safety properties are not yet enforced centrally:

- A fresh PostgreSQL database cannot currently migrate to Alembic head.
- Historical migrations import mutable live ORM models.
- State-changing browser routes lack a uniform local-admin and CSRF boundary.
- The existing CERI CSRF token is static and publicly known.
- Unsafe settings, public binding with debug, and contradictory feature flags are accepted.
- Core scoring outputs cannot always be reproduced from a persisted effective configuration hash.
- Duplicate pipeline starts, retries, and lease loss can repeat or interleave committed side effects.
- The research-only and no-broker-order boundary is documented and currently observed, but not protected by a repository-wide regression gate.
- CI, dependency auditing, migration smoke tests, and governance controls are not committed or verified.

### Original finding distribution

| Severity | Count |
|---|---:|
| High | 13 |
| Medium | 19 |
| Low | 2 |
| **Total** | **34** |

Several original findings describe the same root cause from different review angles. This report groups them into consolidated risk themes while retaining every original ID in the traceability appendix.

## 3. Overall Readiness Decision

### Decision: No-go for broader release or multi-worker operation

The current build should not be treated as ready for:

- network exposure beyond loopback;
- multi-worker processing;
- clean-machine or disaster-recovery installation guarantees;
- security-sensitive administrative browser operations;
- licensed-data purge guarantees;
- reproducible quantitative audit based solely on persisted run artifacts.

### Acceptable interim use

The current build remains suitable for controlled local research when all of the following are true:

- bound only to `127.0.0.1`;
- used by a trusted local user;
- one worker process is active;
- advanced admin and purge features remain disabled unless specifically tested;
- no claim is made that clean database reconstruction is reliable;
- outputs remain advisory and are independently reviewed before financial decisions.

## 4. Baseline Snapshot

| Area | Result |
|---|---|
| Locked dependency synchronization | Passed |
| Ruff lint | Passed |
| Full pytest baseline | Passed, `943 passed` |
| Golden pipeline regression | Passed |
| Config-focused tests | Passed, `86 passed` |
| Database/schema-focused tests | Passed, `115 passed` |
| Background/pipeline-focused tests | Passed, `72 passed` |
| Security route-focused tests | Passed, `85 passed` |
| Current configured DB upgrade to head | Passed |
| Fresh empty DB upgrade to head | **Failed** |
| Alembic metadata check | **Failed** |
| GitHub Actions workflows | None observed |
| Branch protection/status checks | Not verified |
| IB order-capable application calls | None found in reviewed scans |
| Row-level PostgreSQL lease contention probes | Passed |

The focused test counts overlap and must not be added together.

## 5. Positive Controls and Strengths

### 5.1 Functional and quantitative discipline

- The full test suite and golden pipeline pass at the reviewed commit.
- Missing, insufficient, stale, and low-confidence evidence is represented in many services, persisted artifacts, exports, and UI surfaces.
- Market regime and sector rotation are implemented as advisory context rather than silent score mutation.
- Setup lifecycle and winner-probability tests include forward-only and point-in-time safeguards.
- CERI includes provider gating, export redaction, evidence identifiers, config hashes, and point-in-time correction behavior.
- Core IB connections reviewed use `readonly=True`.
- No application code match was found for the reviewed broker-order sentinel APIs.

### 5.2 Persistence and operations

- Newer subsystems use natural-key constraints, config hashes, calculation versions, and immutable evidence patterns more consistently than earlier core tables.
- Timestamp columns are broadly timezone-aware.
- Background-job claiming uses PostgreSQL `FOR UPDATE SKIP LOCKED`.
- Job completion and stale-worker updates use execution-token fencing.
- Direct PostgreSQL probes confirmed distinct worker claims and rejection of an old token after stale recovery.

### 5.3 Browser rendering

- Jinja autoescaping and `tojson` are commonly used.
- App-owned JavaScript generally uses `textContent`.
- No app template usage of Jinja `|safe` was found in the security review.
- Dynamic ordering paths reviewed use allowlists or explicit maps.

These strengths reduce risk, but they do not replace the missing cross-cutting release gates described below.

# 6. Consolidated Risk Register

## 6.1 Gate A: Browser-Local Security Boundary

**Severity:** High  
**Source findings:** `PH1-001`, `PH1-002`, `PH15-001`, `PH15-002`

### Problem

State-changing routes across uploads, pipelines, recalculation, IB operations, setup lifecycle, market and sector operations, winner probability, and CERI do not share one centrally enforced browser-local authorization and CSRF model. Winner-probability admin routes have local-host checks but no CSRF. CERI checks a static token that is hard-coded, emitted to the page, and accepted from the query string.

### Impact

A malicious website opened in the same browser can attempt requests to localhost. If the app is accidentally exposed on a network interface, the same surface becomes reachable from other machines. Expensive, destructive, or state-changing operations are protected inconsistently.

### Required actions

1. Build a complete route matrix for every POST and other state-changing method.
2. Classify each route as:
   - ordinary trusted local workflow;
   - local-admin action;
   - internal callback;
   - explicitly exempt, with justification.
3. Add a shared dependency or middleware for state-changing routes.
4. Replace static CSRF tokens with unpredictable per-session or double-submit-cookie tokens.
5. Reject query-string CSRF tokens.
6. Validate `Origin` and `Sec-Fetch-Site` where practical.
7. Require appropriate content types for JSON endpoints.
8. Add a route-map regression test that fails when a new state-changing route lacks a guard or exemption.

### Acceptance gate

- Every state-changing route is classified.
- Admin actions require loopback or explicit secure admin mode.
- Browser state changes require a real CSRF mechanism.
- Static and query-string tokens are rejected.
- Non-local requests and missing/invalid tokens receive deterministic `403` responses.

### Owner profile

Backend/security engineer.

---

## 6.2 Gate B: Host, Debug, and Public Binding Policy

**Severity:** High  
**Source findings:** `PH3-001`, `PH15-003`

### Problem

Runtime settings accept unsafe combinations such as public binding with debug enabled. Host headers are not constrained by an observed allowlist middleware.

### Impact

A configuration mistake can invalidate all localhost-only security assumptions, expose debug details, and widen the state-changing route surface.

### Required actions

1. Add bounded Pydantic fields for ports and related runtime values.
2. Reject public bind plus debug unless an explicit dangerous override is supplied.
3. Add `TrustedHostMiddleware` or an equivalent allowlist.
4. Define supported reverse-proxy and forwarded-header behavior.
5. Change production-oriented defaults or startup diagnostics so unsafe exposure is unmistakable.
6. Test host spoofing and all public-bind/debug combinations.

### Acceptance gate

- `0.0.0.0` plus debug fails startup by default.
- Unknown or disallowed hosts are rejected.
- Loopback startup remains simple and documented.
- Reverse-proxy behavior is explicit rather than inferred.

### Owner profile

Backend/security engineer.

---

## 6.3 Gate C: Clean Database Migration and Historical Determinism

**Severity:** High  
**Source findings:** `PH4-001`, `PH4-002`

### Problem

A fresh PostgreSQL database fails while upgrading to Alembic head because migration `0018` creates tables from current live ORM metadata and migration `0019` then attempts to add columns already present. Other historical revisions also import application models.

### Impact

Fresh installation, disaster recovery, CI migration smoke, historical upgrades, and downgrades cannot be trusted. Future ORM changes can silently rewrite the behavior of old migrations.

### Required actions

1. Choose a migration-chain repair policy:
   - repair or squash pre-release migrations; or
   - introduce a carefully tested corrective path.
2. Replace imports from `app.models` inside revision files with explicit Alembic operations or migration-local frozen `sa.Table` definitions.
3. Repair the CERI `0018` to `0019` transition.
4. Test:
   - base to head;
   - head to base where supported;
   - representative historical hops;
   - upgrade after each selected prior revision.
5. Add a static lint rule that rejects application-model imports from migration revisions.

### Acceptance gate

A temporary empty PostgreSQL database successfully completes the documented upgrade and downgrade matrix, and no revision file imports mutable application ORM models.

### Owner profile

Backend/database engineer.

---

## 6.4 Gate D: CERI Licensed-Data Purge Semantics

**Severity:** High  
**Source finding:** `PH4-004`

### Problem

The purge workflow records audit activity but does not actually purge, redact, tombstone, quarantine, or invalidate affected source and derivative data.

### Impact

The product can imply a stronger licensed-data deletion guarantee than the implementation provides. Derived features and scores may remain usable after a purported purge.

### Required actions

1. Decide the intended lifecycle semantics:
   - physical delete;
   - tombstone;
   - redaction;
   - quarantine;
   - audit-only.
2. Align service names, UI wording, routes, jobs, documentation, and tests with the decision.
3. If purge is required, implement one transaction covering:
   - source handling;
   - derivative invalidation;
   - rebuild obligations;
   - audit preservation.
4. Add real PostgreSQL tests for preview, confirmation, execution, rollback, and retry.

### Acceptance gate

Execution either performs the declared data lifecycle action and marks all affected derivatives, or every surface clearly states that the operation is audit-only.

### Owner profile

Backend/data-governance engineer.

---

## 6.5 Gate E: Duplicate Pipeline Starts and Queue Coalescing

**Severity:** High  
**Source findings:** `PH14-001`, `PH14-006`

### Problem

Starting a full pipeline twice for the same upload run creates two pipelines and jobs. Duplicate coalescing patterns differ by subsystem and are not consistently protected by database uniqueness.

### Impact

Multiple workers can process the same run concurrently, repeat IB calls, and race destructive score or snapshot refreshes.

### Required actions

1. Define the canonical rerun policy for an upload run.
2. Add a stable request key for full-pipeline jobs.
3. Add database-backed uniqueness for active nonterminal work where PostgreSQL supports it.
4. Implement one atomic enqueue-or-return-existing API used by all job families.
5. Require an explicit rerun key or override for intentional parallel or repeated work.
6. Add concurrent duplicate-start tests using real PostgreSQL sessions.

### Acceptance gate

Two concurrent duplicate requests produce at most one active pipeline and job unless an explicitly authorized rerun identity is provided.

### Owner profile

Backend engineer.

---

## 6.6 Gate F: Pipeline Retry and Resume Semantics

**Severity:** High  
**Source finding:** `PH14-002`

### Problem

Pipeline steps commit progress and side effects, but a retried `FULL_PIPELINE` handler restarts from the beginning. There is no complete checkpoint/resume policy and no proof that every step is safely repeatable.

### Impact

External market-data calls, derived rows, alerts, snapshots, and advanced-engine captures can be repeated or duplicated after a later failure.

### Required actions

1. Decide between:
   - resume from persisted checkpoints; or
   - full replay with explicit idempotency contracts.
2. Record attempt numbers and replay status.
3. Specify idempotency for each step and each external side effect.
4. Add failure injection immediately after every pipeline step.
5. Verify no duplicate evidence, alerts, outcomes, or external calls after retry.
6. Make partial and replayed results observable in operations UI and exports.

### Acceptance gate

A retry after failure at every step has documented behavior and preserves exactly-once logical outcomes, even if physical execution occurs more than once.

### Owner profile

Backend/data engineer.

---

## 6.7 Gate G: Lease-Loss Side-Effect Fencing

**Severity:** High  
**Source finding:** `PH14-003`

### Problem

Job-row completion is fenced with an execution token, but long-running pipeline dependencies can continue and commit side effects after their lease expires and another worker reclaims the job.

### Impact

An old and new worker can interleave committed changes for the same run. The job table may look correct while downstream data is corrupted or duplicated.

### Required actions

1. Pass a lease guard and heartbeat callback into all long-running operations.
2. Check lease ownership before every commit, destructive refresh, external publish, or alert creation.
3. Abort the current unit of work if token fencing fails.
4. Keep leases alive during long IB, model, backfill, purge, and reconstruction operations.
5. Add a two-worker stale-recovery test where the old worker attempts a late side-effect commit.

### Acceptance gate

After lease transfer, the old worker cannot commit pipeline progress or any derived side effect.

### Owner profile

Backend/concurrency engineer.

---

## 6.8 Gate H: Runtime Settings, Environment, and Feature-Flag Validation

**Severity:** High  
**Source findings:** `PH3-001`, `PH3-003`, `PH3-004`

### Problem

Negative ports, delays, sizes, retries, page sizes, and worker timings are accepted. Unknown environment keys are ignored. Child engine flags can be enabled without validated parent-engine semantics.

### Impact

Typos and contradictory settings can silently disable safeguards, change pipeline composition, expose the app, break pagination, remove upload limits, or activate partial subsystems.

### Required actions

1. Add numeric bounds to every operational setting.
2. Add cross-field validators for:
   - debug and public bind;
   - parent and child feature flags;
   - purge and preview requirements;
   - capture and engine activation;
   - worker timing relationships;
   - page-size defaults and maxima.
3. Decide whether unknown `.env` keys fail startup or produce a prominent startup warning.
4. Generate a feature-flag compatibility matrix.
5. Add pairwise tests for high-risk combinations.
6. Add a startup self-check summary without leaking secrets.

### Acceptance gate

Invalid, contradictory, and misspelled settings are rejected or explicitly reported before the app starts work.

### Owner profile

Backend engineer.

---

## 6.9 Gate I: Core Scoring Configuration Schema and Reproduction Lineage

**Severity:** High  
**Source findings:** `PH3-002`, `PH3-005`

### Problem

Core scoring configurations rely on raw dictionaries or partial validation, and effective full configuration hashes are not consistently persisted for fundamental, technical, and combined outputs. Technical config overrides can introduce unknown keys silently.

### Impact

A score, label, or position-size hint can change without durable evidence of the exact effective configuration that produced it.

### Required actions

1. Implement typed schemas for:
   - `scoring_weights.yaml`;
   - `fundamentals_v2.yaml`;
   - `pine_defaults.yaml`;
   - `technical_scoring_v4.yaml`.
2. Reject unknown keys and unsafe threshold relationships.
3. Create stable canonical serialization and hash functions.
4. Persist run-level effective configuration snapshots or populate `EngineParameters`.
5. Attach model version and config hash to every score-producing artifact.
6. Add hash-stability, invalid-config, and lineage-persistence tests.

### Acceptance gate

Every fundamental, technical, combined, and profile result can identify the exact model version and effective configuration used.

### Owner profile

Backend/quant engineer.

---

## 6.10 Gate J: Research-Only and No-Broker-Order Boundary

**Severity:** High  
**Source findings:** `PH1-003`, `PH15-006`

### Problem

The no-order boundary is documented, reviewed scans found no order APIs, and IB access is read-only, but there is no repository-wide test guarding future Python, templates, JavaScript, routes, and runtime calls.

### Impact

A future change could introduce an order-capable method, route, UI action, or dependency usage without triggering a release gate.

### Required actions

1. Add a repository-wide static safety test covering application Python, templates, and static assets.
2. Scan for order-capable API calls, route names, command labels, and broker mutations.
3. Maintain a small explicit allowlist for documentation and safety-test sentinel strings.
4. Add fake-IB runtime tests proving fetch/test/resolve flows use read-only connections and invoke no order methods.
5. Run this gate in CI.

### Acceptance gate

Any new order-capable code or user action fails CI unless the safety boundary is intentionally re-approved through a formal decision.

### Owner profile

Backend/safety engineer.

# 7. Important Medium and Low Risks

## 7.1 CI and Security Automation

**Severity:** Medium/Low  
**Source findings:** `PH0-001`, `PH15-007`

No GitHub Actions workflows were observed. Dependency audit, migration smoke, secret scanning, and vendored-asset integrity checks are manual.

**Actions**

- Add CI for frozen dependency sync, Ruff, full pytest, golden pipeline, clean PostgreSQL migrations, `alembic check`, dependency audit, secret scan, and vendor SHA verification.
- Make required checks branch-protection prerequisites.

**Acceptance**

Pull requests cannot merge without all required checks.

---

## 7.2 Branch Protection and Governance Evidence

**Severity:** Medium  
**Source finding:** `PH0-002`

Branch protection, required reviews, status checks, secret scanning, and admin enforcement were not verified.

**Actions**

- Capture settings with authenticated repository access.
- Enable or document required checks, review requirements, force-push/deletion policy, and administrative bypass.
- Record governance evidence in the review log.

---

## 7.3 IB Runtime Flag Coverage

**Severity:** Medium  
**Source finding:** `PH0-003`

High-impact IB settings lack direct positive and negative tests.

**Actions**

Add explicit tests for RTH behavior, stale-bar rules, benchmark fetching, revision auditing, conservative pacing, retry/backoff, and duration modes.

---

## 7.4 Local Database Setup Consistency

**Severity:** Medium  
**Source finding:** `PH0-004`

README and environment defaults use port `5432`, while the reviewed Docker mapping used a different host port.

**Actions**

Choose one canonical setup, align README, `.env.example`, and Compose, and add a readiness diagnostic that reports the effective target without credentials.

---

## 7.5 Product and Safety Glossary

**Severity:** Medium  
**Source finding:** `PH1-004`

Decision, confidence, freshness, completeness, actionability, and position-size terms are spread across modules and documents.

**Actions**

Create one glossary mapping each critical term to its semantic definition, source configuration, persistence fields, UI/API labels, and tests.

---

## 7.6 XLSX Requirement Drift

**Severity:** Low  
**Source finding:** `PH1-005`

Older requirements mention XLSX, while current documented exports are CSV, JSON, and Markdown.

**Actions**

Either mark XLSX as deferred/removed or implement and test it.

---

## 7.7 Alembic Metadata Drift

**Severity:** Medium  
**Source finding:** `PH4-003`

`alembic check` reports indexes present in the database but absent from ORM metadata.

**Actions**

Represent the indexes in metadata or configure a documented Alembic filter for intentional database-only indexes. Make `alembic check` a CI gate.

---

## 7.8 Database-Level Quantitative Constraints

**Severity:** Medium  
**Source finding:** `PH4-005`

Many numeric fields are unbounded and core score tables rely heavily on service-layer validation.

**Actions**

Add conservative database checks for stable invariants such as non-negative volume, positive rank, valid probability/percentage ranges, and bounded scores. Define precision and scale by numeric family.

---

## 7.9 Refresh Transaction and Reader Consistency

**Severity:** Medium  
**Source finding:** `PH4-006`

Delete-and-insert refreshes lack real PostgreSQL rollback and concurrent-reader tests.

**Actions**

Add mid-refresh failure injection, prove rollback preserves the previous complete state, and verify readers cannot observe partial replacement.

---

## 7.10 Upload Artifact Cleanup

**Severity:** Medium  
**Source finding:** `PH4-007`

Uploaded files are written before database work is guaranteed to succeed, without a complete orphan cleanup contract.

**Actions**

Delete files after unexpected database failure or persist a pending artifact record and reconcile orphaned files during startup/maintenance.

---

## 7.11 Automated PostgreSQL Concurrency Suite

**Severity:** Medium  
**Source finding:** `PH14-004`

Manual two-session probes passed, but the behavior is not a committed integration test.

**Actions**

Automate claim contention, stale recovery, old-token rejection, duplicate enqueue, cancellation races, and late-worker commits using real PostgreSQL.

---

## 7.12 Execution-Token Redaction

**Severity:** Medium  
**Source finding:** `PH14-005`

Raw execution tokens can be persisted in lease-event metadata and log paths.

**Actions**

Persist hashes or short suffixes, redact full tokens from logs, UI, exports, and diagnostics, and test the redaction.

---

## 7.13 Public Error and Hostile-Content Safety

**Severity:** Medium  
**Source findings:** `PH15-004`, `PH15-005`

Some routes return raw exception strings. Current rendering patterns look mostly safe, but there is no broad XSS regression suite covering uploaded and provider-controlled fields.

**Actions**

- Return stable public error codes and redacted messages.
- Log detailed exceptions server-side with structured redaction.
- Add hostile-content tests across upload, run detail, CERI, setup lifecycle, winner probability, market regime, sector rotation, and exports.
- Add a static guard for new `innerHTML` use.

# 8. Remediation Program

## Stage 0: Immediate Containment

Apply these controls before code-level remediation is complete:

- bind only to `127.0.0.1`;
- do not expose through a proxy or LAN;
- use a single worker;
- keep CERI admin and purge disabled;
- avoid simultaneous pipeline starts for the same upload run;
- treat retries after partial pipeline progress as potentially duplicative;
- set debug off for normal research sessions where feasible;
- preserve backups before migration or purge experiments.

## Stage 1: Stop-the-Line Release Gates

Complete first:

1. Repair the clean database migration path.
2. Remove live ORM imports from migrations.
3. Add central local-admin and CSRF protection.
4. Replace the static CERI token.
5. Enforce host/debug/public-bind policy.
6. Add full-pipeline deduplication.
7. Fence side effects after lease loss.
8. Define retry/resume semantics.
9. Validate runtime settings and feature-flag dependencies.
10. Add effective config hashes for core scoring.
11. Add the repository-wide no-order boundary test.

## Stage 2: Data Integrity and Lifecycle

1. Resolve CERI purge semantics.
2. Make `alembic check` pass.
3. Add rollback and reader-consistency tests.
4. Add database constraints for stable quantitative invariants.
5. Add upload artifact cleanup.
6. Add real PostgreSQL duplicate-enqueue and pipeline failure tests.

## Stage 3: Operational and Security Hardening

1. Redact execution tokens.
2. Redact public error responses.
3. Add hostile-content/XSS tests.
4. Add stuck-job and recovery runbooks.
5. Document one-worker and separate-worker topology.
6. Add configuration self-check and safe startup diagnostics.

## Stage 4: Governance and Maintainability

1. Add GitHub Actions and required checks.
2. Verify and configure branch protection.
3. Add dependency, secret, license, and vendor-integrity checks.
4. Add product glossary and requirements traceability maintenance.
5. Resolve XLSX requirement drift.
6. Align database setup documentation.

# 9. Master Verification Plan

The following tests should become required release evidence.

## 9.1 Migration tests

- Empty PostgreSQL database: `upgrade head`.
- Supported `downgrade base`.
- Representative historical revision hops.
- `alembic check`.
- Static rejection of application-model imports in revision files.

## 9.2 Security tests

- Route inventory test for all state-changing methods.
- Missing, invalid, static, and query-string CSRF rejection.
- Cross-origin and non-loopback client rejection.
- Host-header allowlist.
- Public bind plus debug startup rejection.
- Stable redacted public errors.
- Stored and reflected hostile-content payloads.
- Repository-wide no-order static scan.
- Fake-IB runtime no-order assertions.

## 9.3 Configuration tests

- Numeric lower and upper bounds.
- Unknown environment variable behavior.
- Parent/child feature-flag matrix.
- Unknown YAML keys.
- Invalid thresholds and weights.
- Effective config hash stability.
- Lineage persistence for every score artifact.

## 9.4 Database and transaction tests

- Mid-refresh rollback.
- Concurrent reader consistency.
- CERI purge rollback and derivative invalidation.
- Database constraint violations.
- Upload file cleanup after database failure.
- Large JSONB and history-query performance fixtures.

## 9.5 Concurrency tests

- Two-worker `SKIP LOCKED` claims.
- Duplicate full-pipeline start race.
- Duplicate generic job enqueue race.
- Stale recovery and old-token rejection.
- Long step outliving lease.
- Retry after failure at every pipeline step.
- Cancellation before and during each long-running operation.

## 9.6 CI and governance tests

- Frozen dependency synchronization.
- Ruff.
- Full pytest.
- Golden pipeline.
- Clean migration smoke.
- Dependency audit.
- Secret scan.
- Vendored asset hash verification.
- Required branch checks.

# 10. Decision Records Required

The following decisions block clean implementation:

| Decision | Question |
|---|---|
| DR-A-001 | Which state-changing routes are ordinary local actions and which require local-admin authorization? |
| DR-A-002 | What CSRF/session model will a local-only browser app use? |
| DR-A-003 | Is public binding supported at all, and under what security mode? |
| DR-A-004 | Will the pre-release migration chain be repaired/squashed or preserved with corrective revisions? |
| DR-A-005 | What are the authoritative CERI purge semantics? |
| DR-A-006 | Can two pipelines for the same upload run ever execute concurrently? |
| DR-A-007 | Does pipeline retry resume from checkpoints or replay idempotent steps? |
| DR-A-008 | What is the supported worker topology: embedded single worker, separate worker, or multi-worker? |
| DR-A-009 | Where is the canonical effective configuration snapshot stored? |
| DR-A-010 | Should unknown `.env` keys fail startup or produce warnings? |
| DR-A-011 | Which quantitative invariants are enforced by PostgreSQL? |
| DR-A-012 | What is the upload, export, provider evidence, and derived-data retention policy? |
| DR-A-013 | What exact language distinguishes advisory research labels from trading instructions? |
| DR-A-014 | Is XLSX still a product requirement? |

# 11. Exit Criteria for Wave A

Wave A can close only when all of the following are true:

## Release blockers

- Fresh PostgreSQL migration to head passes.
- Historical migrations no longer import live application models.
- Central state-changing route protection is implemented.
- Real CSRF protection replaces static/query-string tokens.
- Host, debug, and public binding rules are enforced.
- Duplicate pipeline starts are coalesced.
- Retry/resume behavior is documented and tested.
- Lease loss prevents all later side-effect commits.
- Runtime and feature-flag validation fails fast.
- Core scoring artifacts persist effective model/config lineage.
- The no-broker-order boundary is enforced in CI.

## Quality gates

- `alembic check` passes.
- Real PostgreSQL migration, rollback, and concurrency tests run in CI.
- Public errors are redacted.
- Hostile-content tests pass.
- CERI purge behavior matches its documented name and contract.
- CI, dependency audit, secret scan, and vendor integrity checks are required.
- Branch protection evidence is captured.
- Operational runbooks and configuration documentation are current.

# 12. Original Finding Traceability

| Original ID | Severity | Consolidated section |
|---|---|---|
| PH0-001 | Medium | 7.1 CI and Security Automation |
| PH0-002 | Medium | 7.2 Branch Protection and Governance |
| PH0-003 | Medium | 7.3 IB Runtime Flag Coverage |
| PH0-004 | Medium | 7.4 Local Database Setup Consistency |
| PH1-001 | High | 6.1 Browser-Local Security Boundary |
| PH1-002 | Medium | 6.1 Browser-Local Security Boundary |
| PH1-003 | High | 6.10 Research-Only Boundary |
| PH1-004 | Medium | 7.5 Product and Safety Glossary |
| PH1-005 | Low | 7.6 XLSX Requirement Drift |
| PH3-001 | High | 6.2 and 6.8 Runtime Safety |
| PH3-002 | High | 6.9 Scoring Configuration Lineage |
| PH3-003 | Medium | 6.8 Runtime Safety |
| PH3-004 | Medium | 6.8 Runtime Safety |
| PH3-005 | Medium | 6.9 Scoring Configuration Lineage |
| PH4-001 | High | 6.3 Clean Database Migration |
| PH4-002 | High | 6.3 Historical Migration Determinism |
| PH4-003 | Medium | 7.7 Alembic Metadata Drift |
| PH4-004 | High | 6.4 CERI Purge Semantics |
| PH4-005 | Medium | 7.8 Database Constraints |
| PH4-006 | Medium | 7.9 Refresh Transactions |
| PH4-007 | Medium | 7.10 Upload Cleanup |
| PH14-001 | High | 6.5 Pipeline Deduplication |
| PH14-002 | High | 6.6 Retry and Resume |
| PH14-003 | High | 6.7 Lease-Loss Fencing |
| PH14-004 | Medium | 7.11 PostgreSQL Concurrency Suite |
| PH14-005 | Medium | 7.12 Execution-Token Redaction |
| PH14-006 | Medium | 6.5 Queue Coalescing |
| PH15-001 | High | 6.1 Browser-Local Security Boundary |
| PH15-002 | High | 6.1 Browser-Local Security Boundary |
| PH15-003 | High | 6.2 Host and Public Binding |
| PH15-004 | Medium | 7.13 Public Error Safety |
| PH15-005 | Medium | 7.13 Hostile-Content Safety |
| PH15-006 | Medium | 6.10 Research-Only Boundary |
| PH15-007 | Low | 7.1 CI and Security Automation |

# 13. Phase-Level Status Summary

| Phase | Status | Main conclusion |
|---|---|---|
| Phase 0 | Baseline established, governance gaps | Functional baseline is green; CI/governance and clean setup remain incomplete |
| Phase 1 | Not fully closed | Requirements are broadly traceable; safety and admin controls are inconsistent |
| Phase 3 | Not fully closed | Advanced config governance is strong; core runtime and scoring config need hardening |
| Phase 4 | **Not release-ready** | Fresh migration path is broken and historical migrations are mutable |
| Phase 14 | Single-worker only | Row-level leases are promising; pipeline idempotency and recovery are unproven |
| Phase 15 | **Not exit-ready** | Browser-local state changes, CSRF, and host policy need central enforcement |

## Final Assessment

SwingLens demonstrates substantial engineering depth and unusually strong domain-aware testing for a local research application. The Wave A issues are concentrated in the connective tissue between subsystems: startup policy, migration history, browser-local trust, configuration lineage, and durable-work recovery.

The correct next move is not a broad refactor. It is a narrow sequence of release gates:

1. secure the browser-local boundary;
2. repair and freeze migration history;
3. make durable work idempotent and lease-safe;
4. make configuration reproducible;
5. automate the gates in CI.

Closing those five areas will convert the current collection of strong subsystem implementations into a system whose safety properties are enforced end to end.
