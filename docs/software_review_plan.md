# SwingLens Deep Software Review Plan

**Document status:** Proposed review program  
**Repository:** `ivobog/swing-lens`  
**Default branch reviewed:** `main`  
**Review type:** Deep, phased software and quantitative-system assessment  
**Primary objective:** Produce a prioritized, evidence-backed remediation backlog that improves correctness, safety, reproducibility, maintainability, security, operability, and user trust without changing the product’s decision-support-only boundary.

---

## 1. Purpose

SwingLens is a local-first stock research web application that combines uploaded fundamental data, Interactive Brokers OHLCV data, technical indicators, configurable scoring engines, market-regime and sector overlays, setup-lifecycle analysis, winner-probability estimation, and catalyst/estimate-revision intelligence.

Because the application can influence trading research decisions, the review must go beyond conventional code quality. It must verify that:

- calculations are correct and reproducible;
- historical results do not use future information;
- data revisions and timestamps are handled consistently;
- incomplete, stale, or conflicting inputs cannot silently appear trustworthy;
- concurrent background work remains idempotent and recoverable;
- configuration changes are traceable and safely validated;
- database migrations preserve data and rollback expectations;
- local-only assumptions are enforced rather than merely documented;
- exported information is safe, complete, and consistent with the UI;
- operational failures degrade visibly and do not corrupt prior evidence;
- the application cannot place, modify, or cancel broker orders.

This document defines the review process. It does not itself certify the software.

---

## 2. Review principles

1. **Evidence over intuition.** Every finding must reference code, configuration, a test, a query plan, runtime evidence, or a reproducible experiment.
2. **Point-in-time correctness first.** Any future-data leakage, revision leakage, look-ahead bias, or market-session error is treated as a release-blocking defect.
3. **Actionable output.** Every accepted finding must identify impact, evidence, recommended action, owner profile, priority, and verification method.
4. **Risk-based depth.** Review effort is weighted toward code that affects scores, ranking, lifecycle state, probability estimates, data lineage, and persistence.
5. **Configuration is production code.** YAML files, environment flags, migration scripts, templates, static assets, and export schemas are reviewed with the same seriousness as Python modules.
6. **No silent degradation.** Missing data, stale data, provider conflicts, partial pipeline execution, and fallback behavior must be visible and testable.
7. **Reproducibility before optimization.** Performance improvements must not alter scoring or historical results without an explicit, reviewed model-version change.
8. **Small remediation slices.** Findings should be converted into independently testable issues and pull requests wherever practical.

---

## 3. System areas in scope

### 3.1 Application and web layer

- FastAPI application lifecycle and router registration
- Jinja2 templates, HTMX behavior, JavaScript, and static assets
- request validation, pagination, filtering, sorting, and response contracts
- local-admin actions, CSRF controls, feature flags, and unsafe methods
- health, readiness, error handling, and user-facing failure states

### 3.2 Data ingestion and persistence

- CSV upload, parsing, canonical mapping, validation, and raw preservation
- PostgreSQL schema, constraints, indexes, JSONB usage, and transaction boundaries
- Alembic migration ordering, upgrade safety, downgrade policy, and data backfills
- local upload, export, and cache directories
- IB contract metadata, OHLCV retrieval, revision tracking, and stale-data detection
- catalyst, estimate, guidance, and provider-ingestion lineage

### 3.3 Decision and quantitative engines

- fundamental scoring
- technical indicators and Pine-compatible behavior
- combined decision scoring
- ranking profiles, penalties, gates, and position-size hints
- market-regime and sector-rotation calculations
- setup lifecycle and signal-change engine
- outcome-calibrated winner-probability engine
- catalyst and estimate-revision intelligence
- earnings-risk logic, trading calendars, time zones, and effective sessions

### 3.4 Background processing and operations

- durable pipeline orchestration
- job claiming, leases, heartbeat, cancellation, retry, stale recovery, and idempotency
- feature-flag combinations and partial completion
- logging, metrics, redaction, diagnostics, and failure recovery
- data retention, preview-first purge, replay, reconstruction, and audit trails

### 3.5 Engineering system

- dependency management and lockfile reproducibility
- test architecture and coverage quality
- linting, static analysis, security scanning, and CI quality gates
- documentation, architecture records, release process, and repository governance

### 3.6 Explicitly out of scope unless separately authorized

- financial advice or validation of an investment strategy’s profitability
- penetration testing against systems not owned by the repository owner
- live brokerage order execution
- destructive production-data tests without a backup and written approval
- vendor-license interpretation beyond identifying technical enforcement gaps

---

## 4. Risk classification

### 4.1 Severity

| Severity | Definition | Required response |
|---|---|---|
| **S0 Critical** | Can cause future-data leakage, materially wrong research output, data loss, unauthorized destructive action, secret exposure, or violation of the no-order boundary | Stop affected use; fix before release or research reliance |
| **S1 High** | Can produce incorrect scores, duplicate processing, inconsistent historical results, unsafe migration, broken authorization, or unrecoverable operational failure | Fix in the next remediation milestone |
| **S2 Medium** | Causes degraded reliability, maintainability, performance, observability, or confusing but detectable behavior | Schedule with owner and target release |
| **S3 Low** | Limited-impact quality, usability, documentation, or cleanup concern | Add to normal backlog |

### 4.2 Confidence

- **Confirmed:** reproduced or proven directly from code and data.
- **Strong:** highly likely, with clear supporting evidence but not yet reproduced end to end.
- **Tentative:** plausible concern requiring an experiment.

### 4.3 Finding format

Every finding must contain:

```text
ID:
Title:
Severity:
Confidence:
Affected components:
Evidence:
Reproduction steps:
Expected behavior:
Observed behavior:
Impact:
Root cause or likely cause:
Recommended remediation:
Acceptance criteria:
Regression tests required:
Owner profile:
Dependencies:
```

---

## 5. Deliverables produced by every phase

Each phase must produce all applicable artifacts:

1. **Evidence log** with inspected files, commands, datasets, test runs, and assumptions.
2. **Findings register** using the standard format above.
3. **Action backlog** grouped into immediate, near-term, and structural work.
4. **Test additions proposal** identifying missing unit, integration, property, migration, concurrency, or end-to-end tests.
5. **Decision record** for disputed or intentionally accepted risks.
6. **Phase scorecard** with Red, Amber, Green ratings for each review dimension.
7. **Exit report** stating passed checks, failed checks, deferred items, and blockers.

A phase is not complete merely because files were read. It is complete when its findings are converted into verifiable actions.

---

# Review phases

## Phase 0: Review setup, inventory, and reproducible baseline

### Objective

Create a trustworthy baseline before analyzing behavior. Establish exactly what version, environment, configuration, schema, and test state are being reviewed.

### Review activities

- Record the commit SHA, branch, Python version, PostgreSQL version, operating system, and IB Gateway/TWS version.
- Inventory application modules, routers, services, models, migrations, templates, static assets, configuration files, tests, and documentation.
- Map feature flags and identify valid, invalid, and untested combinations.
- Verify `pyproject.toml` and `uv.lock` consistency using a clean environment.
- Run the documented baseline commands:
  - `uv sync --frozen --extra dev`
  - `alembic upgrade head`
  - `ruff check app tests`
  - `pytest -q`
  - `pytest tests/test_golden_pipeline.py -q`
- Capture test duration, warnings, flaky behavior, skipped tests, and environment-dependent failures.
- Inspect repository protection, CI workflows, dependency automation, secret scanning, and status checks.
- Confirm that no untracked local input referenced by documentation is required for ordinary tests.
- Produce a dependency and subsystem map.

### Actionable outputs

- Baseline build-and-test report.
- Repository inventory and architecture map.
- Feature-flag matrix with untested combinations highlighted.
- CI and repository-governance gap list.
- Immediate issue for any undocumented prerequisite or non-reproducible setup.

### Exit criteria

- A clean environment can install the locked dependencies.
- The current schema can be created from zero.
- Baseline tests and linting have recorded, repeatable outcomes.
- The exact review commit and configuration are frozen in the evidence log.

---

## Phase 1: Product boundaries, requirements, and traceability

### Objective

Verify that implementation behavior can be traced to explicit product and safety requirements.

### Review activities

- Convert the README, execution plans, release notes, configuration comments, and domain documentation into a requirements catalogue.
- Trace each major pipeline step to inputs, outputs, persistence tables, APIs, UI views, and tests.
- Verify the decision-support-only boundary, especially that IB integration is read-only and no order endpoints, order models, or order-capable service calls exist.
- Identify ambiguous product terms such as “Strong candidate,” “Full starter,” “confidence,” “fresh,” “complete,” and “ready.”
- Verify that warnings, incomplete-data states, and research limitations are consistently represented in UI and exports.
- Check whether admin operations, replay, reconstruction, purge, and backfill have explicit authorization and confirmation requirements.
- Identify undocumented assumptions about market, currency, fiscal period, source reliability, or ticker identity.

### Actionable outputs

- Requirements traceability matrix.
- Safety-boundary verification report.
- Glossary of domain labels and exact semantics.
- Issues for requirements with no implementation, implementation with no requirement, or safety rules with no automated guard.

### Exit criteria

- Every critical subsystem has documented inputs, outputs, invariants, and owner.
- Safety-critical boundaries have executable tests or a scheduled remediation item.

---

## Phase 2: Architecture, modularity, and dependency direction

### Objective

Assess whether the architecture supports safe evolution of a rapidly growing codebase.

### Review activities

- Build a module dependency graph for routers, services, models, configuration loaders, and shared helpers.
- Review `app.main`, application lifespan, route introspection, static mounting, and embedded worker startup.
- Identify import-time side effects, global settings, global database engine creation, hidden filesystem creation, and test-isolation risks.
- Check whether routers contain business logic or direct persistence behavior that belongs in services.
- Evaluate boundaries among core scoring, optional research engines, provider adapters, persistence repositories, and UI projection logic.
- Review internal/private function reuse across modules and coupling to implementation details.
- Identify oversized modules, circular dependencies, duplicate concepts, inconsistent DTO patterns, and weak abstraction seams.
- Assess whether the web process and background worker should remain co-located or support separate deployment modes.
- Review extension points for providers, scoring versions, ranking profiles, setup families, and job handlers.

### Actionable outputs

- Current-state architecture diagram.
- Dependency-rule proposal.
- List of architectural hotspots ranked by change risk.
- Refactoring epics with safe sequencing and characterization-test requirements.
- Architecture Decision Record proposals for worker topology, configuration lifecycle, and quantitative-engine versioning.

### Exit criteria

- Critical dependency violations and import-time side effects are documented.
- Each structural recommendation has a migration path that preserves behavior.

---

## Phase 3: Configuration, secrets, and feature-flag correctness

### Objective

Ensure configuration is validated, deterministic, secure, and observable.

### Review activities

- Review all Pydantic settings, `.env.example`, YAML configuration files, defaults, and runtime loaders.
- Validate numeric ranges, incompatible settings, required paths, unknown keys, duplicate keys, and missing sections.
- Confirm that configuration loading has predictable working-directory behavior.
- Verify that scoring and policy configuration is immutable during a run or captured with a hash/version.
- Test malformed YAML, invalid enum values, negative thresholds, weights that do not sum as expected, and contradictory gates.
- Create pairwise and risk-based tests for feature-flag combinations.
- Review debug defaults and ensure unsafe settings cannot be accidentally exposed beyond localhost.
- Verify secrets are environment-only, not logged, not exported, and not stored in JSONB evidence.
- Verify provider license restrictions are enforced by code, not only documentation.

### Actionable outputs

- Configuration schema and validation gap report.
- Feature-flag compatibility matrix.
- Secret-flow and redaction map.
- Proposed startup validation checks.
- Issues for any setting that can silently change output without lineage capture.

### Exit criteria

- Invalid high-risk configuration fails fast with a clear message.
- Every score-producing run can identify its effective configuration and model version.

---

## Phase 4: Database schema, migrations, transactions, and data lifecycle

### Objective

Verify persistence integrity and safe evolution of all evidence and derived results.

### Review activities

- Review all Alembic migrations from an empty database to head.
- Test upgrades from representative historical schema points.
- Review downgrade behavior or explicitly document forward-only migrations.
- Inspect foreign keys, delete rules, unique constraints, check constraints, nullability, timestamps, indexes, and numeric precision.
- Verify that source evidence is not unintentionally cascaded when upload runs are deleted.
- Review transaction boundaries for upload, scoring refresh, pipeline steps, background jobs, purge, replay, and backfill.
- Simulate mid-transaction failures and confirm rollback leaves a consistent state.
- Review `delete then insert` refresh patterns for atomicity and reader consistency.
- Check large JSONB fields for indexing, retention, redaction, and uncontrolled growth.
- Review timezone-aware database columns and Python datetime usage.
- Verify idempotency keys, uniqueness constraints, and race handling.
- Review purge previews, confirmation tokens, audit preservation, and reconstruction obligations.
- Use `EXPLAIN (ANALYZE, BUFFERS)` on representative high-volume queries.

### Actionable outputs

- Schema integrity report.
- Migration test matrix and upgrade evidence.
- Missing index/constraint recommendations.
- Transaction and rollback defect list.
- Data-retention and purge policy actions.

### Exit criteria

- Fresh install and supported upgrade paths succeed.
- S0/S1 referential-integrity and migration risks are resolved or block release.
- Destructive operations have preview, authorization, audit, and recovery expectations.

---

## Phase 5: CSV ingestion, normalization, identity, and export safety

### Objective

Ensure external files and mapped business data are handled safely and consistently.

### Review activities

- Test encoding variants, byte-order marks, delimiter anomalies, quoting, embedded newlines, duplicate headers, blank rows, malformed numerics, extreme values, NaN/Infinity, and oversized files.
- Verify size validation cannot be bypassed by unusual file objects or streaming behavior.
- Test filename sanitization, path traversal, reserved names, very long names, and collisions.
- Confirm failed uploads do not leave orphaned files or partially persisted rows unless intentionally retained.
- Review column alias precedence, ambiguous mappings, duplicate tickers, ticker case, exchange-qualified symbols, and identity collisions.
- Verify raw-row preservation exactly matches the intended forensic requirement.
- Test earnings-date parsing across locales and ambiguous formats.
- Test sector normalization and unknown-sector handling.
- Review spreadsheet formula injection in every CSV/XLSX export field beginning with `=`, `+`, `-`, or `@`.
- Verify export encoding, quoting, stable column order, schema versioning, and round-trip behavior.
- Compare UI, JSON, CSV, and Markdown representations for semantic consistency.

### Actionable outputs

- Hostile-file test suite proposal.
- Data-normalization defect register.
- Identity-resolution rules and unresolved ambiguity report.
- Export safety and schema-versioning actions.
- Cleanup policy for failed and expired upload artifacts.

### Exit criteria

- Malformed inputs fail safely and visibly.
- Exports cannot execute formulas by default when opened in spreadsheet software.
- Duplicate and ambiguous identities have deterministic handling.

---

## Phase 6: Interactive Brokers integration and market-data integrity

### Objective

Verify that market data is fetched, identified, cached, revised, and interpreted correctly.

### Review activities

- Confirm all IB connections use read-only mode and no order-capable path exists.
- Review event-loop compatibility, thread affinity, client-ID collision behavior, reconnects, timeouts, and clean disconnects.
- Test rate limiting, retries, backoff, pacing violations, partial responses, and cancellation.
- Review contract resolution for ambiguous tickers, exchanges, currencies, share classes, ADRs, ETFs, and delisted symbols.
- Verify `TRADES` versus adjusted data semantics and corporate-action handling.
- Validate bar ordering, duplicates, missing sessions, zero volume, stale detection, holidays, half-days, DST transitions, and current-day incomplete bars.
- Review benchmark and sector-data alignment.
- Verify revision hashes, first-seen/last-seen/revised timestamps, and audit behavior.
- Test interrupted fetches and repeated fetch idempotency.
- Verify market-data gaps cannot silently produce confident classifications.

### Actionable outputs

- IB integration failure-mode matrix.
- Contract-resolution ambiguity backlog.
- Market-calendar and stale-data test additions.
- Data-revision lineage report.
- Operational runbook for IB outages and pacing violations.

### Exit criteria

- Read-only enforcement is tested.
- Incomplete or ambiguous market data is visible and cannot masquerade as complete.
- Repeated fetches are idempotent and revisions are auditable.

---

## Phase 7: Fundamental scoring correctness

### Objective

Validate parsing, transformations, penalties, labels, and explanation lineage for fundamental scores.

### Review activities

- Trace every input column to canonical field, parser, component score, penalty, final score, label, explanation, and persisted debug evidence.
- Review handling of percentages, ratios, units, currencies, negatives, missing values, outliers, and contradictory metrics.
- Verify weights, caps, floors, normalization, and rounding.
- Test invariants such as score bounds, monotonic behavior where intended, and deterministic output.
- Verify duplicate ticker behavior and row-selection policy.
- Create boundary-value tests at every label threshold.
- Compare implementation with documented model versions and golden fixtures.
- Review whether missing-data rescaling or penalties can unintentionally reward sparse rows.
- Validate warning and trap flags against independent hand calculations.
- Establish controlled procedures for changing scoring weights and golden expectations.

### Actionable outputs

- Fundamental formula specification.
- Independent calculation workbook or script for representative cases.
- Boundary and property-test backlog.
- Versioning and model-change governance proposal.
- Findings for unexplained, unreachable, or contradictory labels.

### Exit criteria

- Representative scores can be reproduced independently.
- All thresholds and missing-data policies have boundary tests.
- Model changes require an explicit version and reviewed regression update.

---

## Phase 8: Technical indicators and Pine parity

### Objective

Prove that technical features are mathematically correct, temporally valid, and equivalent to the intended Pine behavior.

### Review activities

- Review EMA, SMA, RMA, RSI, ATR, DMI/ADX, OBV, ROC, slopes, pivots, resampling, breakout, contraction, stage, and climax-risk calculations.
- Verify pandas seeding and minimum-period behavior against Pine semantics.
- Test flat series, one-direction series, zero volume, gaps, splits, missing rows, duplicate dates, and short histories.
- Review weekly resampling and confirmed-higher-timeframe behavior, including the treatment of incomplete weeks.
- Investigate centered rolling windows and any feature that could use future bars.
- Verify pivot confirmation delays are represented correctly and never backdated as known earlier.
- Compare Python output with Pine output using a frozen multi-ticker fixture and exact tolerances.
- Test timezone and date alignment before relative-strength merges.
- Review stop/target calculations and all risk flags for divide-by-zero and unstable values.
- Validate deterministic behavior across supported NumPy and pandas versions.

### Actionable outputs

- Indicator formula catalogue.
- Pine parity test harness and mismatch report.
- Look-ahead-risk register.
- Numerical tolerance policy.
- Golden multi-market dataset proposal.

### Exit criteria

- No unresolved future-bar usage exists in score-producing features.
- Critical indicators match approved references within documented tolerances.
- Insufficient history and partial periods are explicitly flagged.

---

## Phase 9: Combined decisions, ranking profiles, gates, and user guidance

### Objective

Validate how fundamental and technical evidence becomes ranking, decisions, warnings, and position-size hints.

### Review activities

- Trace weighted-score calculations, available-data rescaling, missing-data penalties, danger classifications, trap flags, liquidity warnings, and earnings gates.
- Test all score and label boundaries using values immediately below, at, and above thresholds.
- Verify clamping and rounding order.
- Confirm incomplete data cannot outrank complete, reliable candidates unintentionally.
- Review sort buckets and deterministic tie-breaking.
- Test contradictory evidence, such as high score with danger classification or strong fundamentals with insufficient technical history.
- Verify position-size hints are clearly research labels, not executable instructions.
- Compare combined decisions with ranking-profile decisions for semantic conflicts.
- Test every ranking profile’s component weights, penalties, gates, missing-data policy, and permissions.
- Verify debug payloads contain enough evidence to reconstruct a decision.
- Review earnings-date unknown, stale, and conflicting-source behavior.

### Actionable outputs

- Decision-table specification.
- Boundary and contradiction test suite.
- Ranking consistency report.
- UI wording and warning improvements.
- Issue list for any result that cannot be reconstructed from persisted evidence.

### Exit criteria

- Every decision label and position-size hint has explicit, tested conditions.
- Missing and conflicting data produce conservative, visible outcomes.
- Results are deterministically sortable and explainable.

---

## Phase 10: Market regime and sector rotation

### Objective

Validate cross-sectional and market-context overlays without introducing circularity, survivorship bias, or hidden look-ahead.

### Review activities

- Review benchmark selection, participation metrics, breadth, leadership, regime thresholds, risk-state mapping, and allowed-profile permissions.
- Verify universe construction and treatment of missing constituents.
- Review sector taxonomy, sector normalization, ETF confirmation, and unknown sectors.
- Check whether current run data is used consistently and whether stale benchmarks can contaminate a snapshot.
- Validate snapshot immutability and run association.
- Test extreme breadth cases, small universes, all-missing sectors, and ties.
- Verify no result is calculated using a later snapshot or revised data unavailable at the decision cutoff.
- Compare exports, dashboard, drill-down views, and briefs.
- Review whether regime or rotation state can override or merely annotate ranking, and test that policy explicitly.

### Actionable outputs

- Regime and rotation formula specifications.
- Universe and survivorship-risk report.
- Snapshot consistency tests.
- Policy conflict matrix.
- Data-freshness and minimum-coverage requirements.

### Exit criteria

- Regime and sector outputs are reproducible from frozen inputs.
- Stale or insufficient coverage produces an explicit low-confidence state.
- Policy overrides are documented and tested.

---

## Phase 11: Setup lifecycle and signal-change engine

### Objective

Verify lifecycle state transitions, episode identity, alerts, replay, and temporal correctness.

### Review activities

- Review canonicalization, setup-family adapters, snapshot construction, episode selection, state transitions, actionability policy, velocity, confidence, and alerts.
- Define state-machine invariants and prohibited transitions.
- Test repeated evaluation, out-of-order events, duplicate snapshots, missing days, revised source data, and concurrent workers.
- Verify episode identity remains stable where intended and splits where required.
- Validate primary-episode selection and tie-breaking.
- Review replay and reconstruction for isolation from live state.
- Verify promotion from replay requires explicit confirmation.
- Test alert deduplication, acknowledgement, stale alerts, and disabled-alert behavior.
- Review retention and purge controls.
- Confirm all state changes include source cutoff, calculation version, configuration hash, and evidence identifiers.

### Actionable outputs

- Formal state-transition table.
- Property-based lifecycle tests.
- Replay/reconstruction safety report.
- Alert idempotency and noise-reduction actions.
- Episode lineage and audit requirements.

### Exit criteria

- Invalid transitions are impossible or rejected.
- Reprocessing the same evidence is idempotent.
- Replay cannot silently mutate live research state.

---

## Phase 12: Winner-probability engine and quantitative validation

### Objective

Assess statistical validity, leakage controls, calibration, reproducibility, and safe presentation of probabilities.

### Review activities

- Review outcome definitions, episode selection, decision-time feature capture, target/stop semantics, pending outcomes, revisions, and label construction.
- Verify feature timestamps are at or before the decision cutoff.
- Review walk-forward folds, grouped episodes, embargo needs, and universe leakage.
- Check survivorship bias, selection bias, class imbalance, sample-size sufficiency, and repeated observations.
- Validate preprocessing fit only on training folds.
- Review model registry, artifact hashing, immutable feature order, training cutoff, configuration capture, and reproduction service.
- Compare model performance with global and cohort baselines.
- Evaluate log loss, Brier score, calibration curves, discrimination, confidence intervals, and cohort stability.
- Review drift detection and minimum evidence before promotion.
- Confirm unapproved algorithms cannot be trained or promoted.
- Test outcome revisions without retroactively changing historical “as known” predictions.
- Review probability wording, uncertainty, sample size, and model-status visibility in the UI.

### Actionable outputs

- Quantitative validation report.
- Leakage and bias checklist with evidence.
- Model-card template.
- Promotion-gate specification.
- Calibration and drift dashboard requirements.

### Exit criteria

- No unresolved temporal leakage exists.
- Every model artifact is reproducible from stored evidence.
- Probability output includes calibration status, sample context, and model version.
- Promotion requires documented quantitative gates and human approval.

---

## Phase 13: Catalyst and estimate-revision intelligence

### Objective

Validate provider controls, normalization, point-in-time queries, revisions, conflict handling, export restrictions, and licensed-data lifecycle.

### Review activities

- Review provider protocol, registry, credentials, health, capability reporting, retry, rate limiting, and manual-provider behavior.
- Verify source-record idempotency, content hashes, supersession chains, correction types, effective sessions, and canonical observation keys.
- Test fiscal period normalization, currency conversion, scale normalization, estimate/guidance/earnings normalization, and surprise calculations.
- Verify `AS_KNOWN` and `LATEST_CORRECTED` semantics across corrections and late arrivals.
- Review conflict detection, deduplication, confidence degradation, quarantine, and manual review.
- Validate catalyst taxonomy and deduplication.
- Review opportunity scoring, event risk, change detection, alerts, and historical lineage.
- Test export-policy redaction against nested secrets, URLs, filesystem paths, SQL details, raw payloads, and authorization headers.
- Verify provider-license purge is preview-first, confirmation-bound, audited, and rebuild-aware.
- Confirm provider outages never block the core SwingLens workflow.

### Actionable outputs

- Provider compliance and data-lineage report.
- Point-in-time correctness test matrix.
- Redaction attack test suite.
- Conflict and manual-review operating procedure.
- Purge/rebuild verification checklist.

### Exit criteria

- Historical queries have proven temporal semantics.
- Restricted information cannot leave approved service boundaries.
- Corrections, conflicts, and purges remain auditable.

---

## Phase 14: Background jobs, durable pipeline, concurrency, and recovery

### Objective

Prove that asynchronous work is safe under retries, crashes, multiple workers, cancellation, and partial failure.

### Review activities

- Review pipeline creation, step ordering, optional-step composition, state transitions, cancellation, and partial completion.
- Review job claim ordering, `FOR UPDATE SKIP LOCKED`, lease tokens, heartbeat, retry delays, stale recovery, and terminal states.
- Test two or more concurrent workers against PostgreSQL.
- Simulate worker death before commit, after external fetch, during scoring, during heartbeat, and after result persistence.
- Verify stale recovery cannot allow an old worker to commit after lease loss.
- Test duplicate enqueue, duplicate pipeline starts, cancellation races, and retry idempotency.
- Verify job handlers define transactional and external-side-effect boundaries.
- Review long-running operations to ensure heartbeat frequency is sufficient.
- Confirm one job cannot monopolize the worker indefinitely.
- Validate worker shutdown and web-process lifecycle behavior.
- Review operational metadata retention and lease-event redaction.

### Actionable outputs

- Concurrency test suite using real PostgreSQL.
- Failure-injection matrix.
- Idempotency requirements per job type.
- Worker topology recommendation.
- Recovery runbook and stuck-job diagnostics.

### Exit criteria

- Concurrency tests show at-most-one active lease owner per job.
- Retried work does not duplicate persisted results or alerts.
- Cancellation and shutdown produce known, recoverable states.

---

## Phase 15: Web security and local-admin controls

### Objective

Verify that local-first operation has enforceable security boundaries and safe defaults.

### Review activities

- Create a threat model covering browser, local network, malicious CSV, malicious provider payload, compromised dependency, and accidental public binding.
- Verify host binding, debug settings, forwarded-host behavior, proxy assumptions, and deployment documentation.
- Review all state-changing endpoints for method choice, local-admin enforcement, CSRF protection, feature flags, authorization, confirmation, and audit.
- Test Host-header spoofing, IPv4/IPv6 loopback behavior, reverse-proxy scenarios, and `testclient` exceptions.
- Review input validation for ticker, sorting, filters, pagination, identifiers, file paths, and JSON bodies.
- Verify Jinja autoescaping and inspect deliberate safe rendering.
- Test reflected/stored XSS through CSV content, provider content, warning messages, notes, and errors.
- Review SQL query construction for injection and unsafe dynamic ordering.
- Review export endpoints for path traversal and unauthorized sensitive fields.
- Scan dependencies, licenses, secrets, and static vendor assets.
- Verify logs and error pages do not reveal credentials, SQL, local paths, or provider payloads.
- Confirm broker order functionality is absent and protected by regression tests.

### Actionable outputs

- Threat model and attack-surface map.
- State-changing endpoint authorization matrix.
- Security test backlog.
- Dependency and license findings.
- Hardening guide for local and optional network deployment.

### Exit criteria

- No S0/S1 authorization, CSRF, XSS, path traversal, or secret-exposure issue remains.
- Unsafe public-binding combinations fail fast or are explicitly hardened.
- The no-order boundary has an automated repository-wide test.

---

## Phase 16: UI, accessibility, explainability, and error handling

### Objective

Ensure users can understand what the system knows, does not know, and why it produced a result.

### Review activities

- Walk every primary workflow: upload, fetch, pipeline, run detail, charts, exports, history, regime, sector, lifecycle, probability, and CERI.
- Verify loading, empty, disabled, partial, stale, failed, and retry states.
- Compare labels and warnings across HTML, JSON, CSV, and Markdown.
- Review score explanations, evidence links, model versions, cutoffs, confidence, and missing-data indicators.
- Test responsive behavior and large tables.
- Review keyboard navigation, focus order, form labels, semantic headings, color contrast, and screen-reader text against WCAG 2.2 AA expectations.
- Verify charts have accessible summaries and do not rely only on color.
- Review destructive-action confirmation and success/failure feedback.
- Test browser refresh, back navigation, duplicate submission, and stale page actions.
- Ensure financial-research disclaimers are visible where decisions and probabilities are shown.

### Actionable outputs

- Workflow defect register.
- Accessibility audit.
- Explainability consistency matrix.
- UI state catalogue and missing-state designs.
- Error-message and user-recovery improvements.

### Exit criteria

- Critical workflows have explicit non-happy-path behavior.
- Users can distinguish complete, incomplete, stale, corrected, simulated, and live-derived information.
- High-impact accessibility defects are remediated.

---

## Phase 17: Performance, capacity, and resource safety

### Objective

Establish practical limits and prevent resource exhaustion or performance collapse.

### Review activities

- Define representative small, medium, and large workloads for tickers, bars, runs, snapshots, alerts, and provider records.
- Profile upload, technical calculation, pipeline execution, dashboard queries, exports, replay, backfill, and model training.
- Measure database query counts and detect N+1 behavior.
- Benchmark pandas memory usage and avoid unnecessary full-frame copies.
- Test pagination and export streaming behavior.
- Review file and JSON payload size limits.
- Validate worker throughput, poll frequency, lease duration, and retry storms.
- Test concurrent browser requests during background work.
- Review local disk growth for uploads, exports, cache, logs, artifacts, and retained snapshots.
- Define capacity thresholds and graceful refusal behavior.

### Actionable outputs

- Performance baseline and capacity envelope.
- Query optimization backlog.
- Resource-limit recommendations.
- Retention and cleanup jobs proposal.
- Performance regression test candidates.

### Exit criteria

- Supported workload limits are documented.
- No common operation causes unbounded memory, disk, query, or thread growth.
- Critical UI/API targets have measurable budgets.

---

## Phase 18: Test strategy, CI, and release quality gates

### Objective

Turn the review’s key invariants into automated protection.

### Review activities

- Classify existing tests as unit, fake-database, integration, migration, contract, quantitative, performance, and end-to-end.
- Identify excessive reliance on fake database behavior where PostgreSQL semantics matter.
- Measure line and branch coverage, but prioritize invariant coverage over percentage alone.
- Add mutation testing or targeted fault injection for score and gate logic.
- Add property-based tests for parsers, scores, state machines, and idempotency.
- Add clean-database migration tests and representative upgrade tests.
- Add real-PostgreSQL integration tests for concurrency, constraints, and JSONB queries.
- Add browser-level smoke tests for critical workflows.
- Define deterministic fixture and clock-control standards.
- Establish CI jobs for supported Python versions, linting, tests, migration checks, dependency review, secret scanning, and static security analysis.
- Define required status checks and branch protection.
- Establish golden-fixture review rules so expected values cannot be updated casually.

### Actionable outputs

- Test pyramid and gap analysis.
- CI workflow design.
- Required quality-gate list.
- Flaky-test and slow-test backlog.
- Golden-data governance policy.

### Exit criteria

- S0/S1 invariants are protected by automated tests.
- CI runs from a clean environment and blocks regressions.
- Migration and concurrency behavior is tested against PostgreSQL.

---

## Phase 19: Observability, operations, backup, and recovery

### Objective

Ensure failures can be detected, diagnosed, contained, and recovered without losing research lineage.

### Review activities

- Review structured logging consistency, correlation IDs, run IDs, job IDs, provider IDs, model versions, and config hashes.
- Verify redaction in normal and exception paths.
- Define metrics and alerts for pipeline failures, stale jobs, data freshness, provider health, scoring coverage, export failures, and disk growth.
- Review readiness semantics: database, required directories, migrations, worker, and optional dependencies.
- Test graceful shutdown and startup after an interrupted job.
- Define PostgreSQL backup and restore procedures.
- Test restoration into a clean environment and verify evidence integrity.
- Review cleanup, retention, purge, and archive operations.
- Create incident playbooks for corrupted upload, bad migration, IB outage, provider outage, duplicate jobs, incorrect model release, and leaked secret.
- Define rollback expectations for code, configuration, schema, and model artifacts.

### Actionable outputs

- Operations readiness checklist.
- Logging and metrics schema.
- Backup/restore test report.
- Incident runbooks.
- Release rollback and model rollback procedure.

### Exit criteria

- A backup can be restored and validated.
- Operators can identify failed or stale research runs without database archaeology.
- Sensitive data is redacted from operational evidence.

---

## Phase 20: Documentation, maintainability, and release governance

### Objective

Make the system understandable and safely maintainable after the review.

### Review activities

- Verify setup, migrations, configuration, architecture, workflows, and troubleshooting documentation against actual behavior.
- Document each quantitative engine’s inputs, formulas, versions, limitations, and validation evidence.
- Establish ADRs for major design choices.
- Review comments and docstrings for stale or misleading statements.
- Create contributor guidance, code-review checklist, migration checklist, model-change checklist, and release checklist.
- Define semantic versioning for application, database schema, scoring engines, configuration schemas, exports, and model artifacts.
- Establish changelog and release-note requirements.
- Define ownership for subsystems and review requirements for high-risk files.
- Add `CODEOWNERS` recommendations and pull-request templates.

### Actionable outputs

- Documentation gap register.
- Maintainer handbook.
- Release and model-governance checklists.
- Ownership map.
- ADR backlog.

### Exit criteria

- A new maintainer can set up, test, operate, and trace the application using repository documentation.
- High-risk changes have explicit review and versioning rules.

---

## Phase 21: Remediation verification and final release decision

### Objective

Verify fixes, measure residual risk, and decide whether each subsystem is suitable for continued use.

### Review activities

- Reproduce every S0/S1 finding before and after remediation.
- Run the complete automated suite and all added specialized suites.
- Repeat golden, Pine-parity, point-in-time, migration, concurrency, security, performance, and backup/restore tests.
- Review remediation pull requests for unintended changes to scoring behavior.
- Recalculate phase scorecards.
- Document accepted residual risks, owners, expiration dates, and monitoring.
- Produce separate readiness decisions for:
  - core upload and fundamental scoring;
  - IB market-data and technical scoring;
  - combined ranking and contextual overlays;
  - setup lifecycle;
  - winner probability;
  - CERI;
  - administrative and destructive operations.

### Actionable outputs

- Final review report.
- Verified remediation register.
- Residual-risk register.
- Release recommendation per subsystem: **Go**, **Conditional Go**, or **No-Go**.
- Follow-up review schedule.

### Exit criteria

- All S0 findings are closed.
- All S1 findings are closed or explicitly accepted by the accountable owner with compensating controls and a target date.
- The final report links each conclusion to evidence.

---

## 6. Suggested execution order and review slices

The phases should be executed in risk order rather than treated as one enormous linear inspection.

### Wave A: Establish trust

- Phase 0: baseline
- Phase 1: boundaries and requirements
- Phase 3: configuration
- Phase 4: database and migrations
- Phase 14: background jobs and concurrency
- Phase 15: security

### Wave B: Validate core research calculations

- Phase 5: ingestion and exports
- Phase 6: IB market data
- Phase 7: fundamentals
- Phase 8: technical indicators
- Phase 9: combined decisions and ranking

### Wave C: Validate contextual and advanced engines

- Phase 10: market regime and sector rotation
- Phase 11: setup lifecycle
- Phase 12: winner probability
- Phase 13: CERI

### Wave D: Product and operational hardening

- Phase 16: UI and accessibility
- Phase 17: performance
- Phase 18: tests and CI
- Phase 19: operations and recovery
- Phase 20: documentation and governance
- Phase 21: final verification

A subsystem may be temporarily frozen while its review wave is active. Fixes should be delivered as small pull requests with regression tests rather than one giant “review cleanup” branch.

---

## 7. Prioritization model for actionable points

Each finding receives a remediation priority derived from:

- severity;
- probability of occurrence;
- detectability before a user acts;
- breadth of affected runs or subsystems;
- reversibility of damage;
- effort and dependency complexity.

Recommended backlog groups:

### P0: Immediate containment

S0 defects, confirmed leakage, corrupting migrations, unsafe destructive actions, order-boundary violations, or exposed secrets.

### P1: Next remediation milestone

S1 correctness, concurrency, identity, authorization, model, or data-lineage defects.

### P2: Planned hardening

S2 reliability, observability, performance, testability, and maintainability improvements.

### P3: Continuous improvement

S3 cleanup, documentation polish, minor usability, and low-impact refactoring.

---

## 8. Minimum evidence datasets

The review should build a versioned, non-sensitive fixture pack containing:

- small, medium, and large CSV inputs;
- duplicate and ambiguous ticker cases;
- missing and malformed fundamentals;
- several years of frozen OHLCV for multiple market behaviors;
- benchmark and sector series;
- split, gap, zero-volume, holiday, and shortened-session cases;
- earnings dates around gate boundaries;
- Pine reference outputs;
- scoring boundary cases;
- market-regime and sector-rotation snapshots;
- setup-lifecycle transition sequences;
- winner-probability training and outcome fixtures;
- CERI corrections, conflicts, late arrivals, and purge scenarios;
- background-job crash and retry scenarios.

Fixture provenance, license, cutoff time, and expected use must be documented.

---

## 9. Review tooling recommendations

Use tools only where they add evidence. Suggested categories include:

- `ruff` for linting;
- `pytest`, `pytest-cov`, and controlled clocks for automated tests;
- Hypothesis for property-based tests;
- Testcontainers or an equivalent clean PostgreSQL harness;
- Alembic upgrade checks from multiple schema points;
- mypy or pyright after type-checking scope is agreed;
- Bandit, Semgrep, and dependency vulnerability scanning;
- pip-audit or an equivalent lockfile-aware audit;
- Playwright for browser workflows and accessibility smoke tests;
- axe-core for automated accessibility checks;
- profiling with `py-spy`, `cProfile`, and database query instrumentation;
- `EXPLAIN (ANALYZE, BUFFERS)` for database evidence;
- mutation testing selectively around scoring, gates, and state transitions.

Tool output is not automatically a finding. Results must be triaged for actual relevance and impact.

---

## 10. Definition of review completion

The deep review is complete only when:

- all phases have an exit report;
- every S0/S1 finding has an owner and disposition;
- critical calculations have independent reproducibility evidence;
- point-in-time and no-look-ahead guarantees are automated;
- database migration and concurrency behavior has been tested against PostgreSQL;
- state-changing administrative paths have verified authorization and audit controls;
- CI enforces the agreed quality gates;
- backup and recovery have been tested;
- residual risks are documented with expiration dates;
- the final subsystem-specific Go/Conditional Go/No-Go decision has been approved.

---

## 11. First review sprint

The first implementation sprint should not attempt to review everything. It should deliver the following concrete artifacts:

1. freeze the review commit and runtime matrix;
2. run a clean baseline build, schema creation, lint, and test suite;
3. inventory all feature flags and state-changing endpoints;
4. create the architecture and data-flow maps;
5. identify all score-producing and time-sensitive modules;
6. add repository-wide tests for the no-order boundary;
7. design the point-in-time leakage test matrix;
8. add clean PostgreSQL migration smoke tests;
9. add a two-worker lease/idempotency integration test plan;
10. open separate P0/P1 GitHub issues for confirmed blockers.

The sprint exit is an evidence-backed review backlog, not a broad statement that the code “looks good.”
