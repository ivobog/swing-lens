# SwingLens Pipeline Performance Optimization — Software Requirements Specification

**Project:** SwingLens  
**Document type:** Software Requirements Specification (SRS)  
**Version:** 1.0  
**Date:** 2026-08-05  
**Status:** Proposed  
**Baseline upload run:** 78  
**Baseline pipeline run:** 70  
**Companion design:** [Pipeline Performance SDD](pipeline_performance_sdd.md)  

---

## 1. Purpose

This document defines the requirements for changes expected to reduce the foreground wall-clock duration of a run-78-sized SwingLens pipeline by more than two minutes per improvement.

The requirements preserve scoring correctness, evidence lineage, Interactive Brokers safety, retry behavior, and deterministic output. Improvements measured below the two-minute threshold are explicitly excluded from the required scope.

## 2. Baseline and Problem Statement

Upload run 78 contained 454 tickers. Pipeline run 70 completed all 11 steps without retries in 1,862.445 seconds, or 31 minutes 2.445 seconds.

| Step | Baseline duration | Share of total |
| --- | ---: | ---: |
| Fetching Market Data | 717.821 s | 38.5% |
| Scoring Technicals | 513.689 s | 27.6% |
| Capturing Setup Signals | 283.865 s | 15.2% |
| Evaluating Setup Lifecycles | 281.705 s | 15.1% |
| Capturing Winner Predictions | 36.253 s | 1.9% |
| Market Regime Snapshot | 21.405 s | 1.1% |
| All other work and step gaps | 7.707 s | 0.4% |

The four slowest steps consumed 96.5% of the end-to-end runtime.

### 2.1 Measured workload facts

- The setup-lifecycle source loader materialized 685,148 daily price-bar rows for the run universe.
- The loader repeatedly scanned the complete price-bar collection for each of 454 tickers, producing approximately 311 million ticker comparisons per load.
- Setup-lifecycle source loading occurred twice: once during snapshot capture and again during lifecycle evaluation.
- Technical scoring processed 454 tickers sequentially.
- A representative read-only technical profile attributed approximately 80.8% of per-ticker work to `calculate_technical_features`, 8.1% to relative-strength work, 5.4% to frame loading, 4.7% to higher-timeframe work, and 1.1% to final scoring.
- The IB fetch executed 224 successful requests: 64 full backfills and 160 recent top-ups. The configured three-second minimum request interval created a 672-second pacing floor.
- The fetch plan predicted 162 requests because unresolved contracts were counted as zero requests, but 224 requests were ultimately executed.

## 3. Scope

### 3.1 Required improvement families

The following improvement families are in scope because each can save more than two minutes on a run-78-sized workload:

| ID | Improvement family | Expected foreground saving | Confidence |
| --- | --- | ---: | --- |
| IMP-01 | Replace setup-lifecycle all-history loading and repeated full scans with latest-bar projection and linear-time context construction | 500–550 s | High |
| IMP-02 | Execute cold-path technical feature computation in bounded parallel worker processes | 240–360 s | Medium-high |
| IMP-03 | Cache reusable ticker-local technical feature artifacts by immutable input signature | 300–420 s on warm runs | Medium-high |
| IMP-04 | Overlap per-ticker technical computation with IB fetching | At least 120 s; potentially up to the technical-step duration | Medium |
| IMP-05 | Prewarm market data and contracts before the foreground pipeline | At least 192 s on the run-78 workload | High for foreground latency; does not reduce total background work |
| IMP-06 | Conditionally reduce dual-stream IB requests only after exact parity validation | Up to 336 s of pacing time | Conditional |

Savings from IMP-02, IMP-03, and IMP-04 overlap and must not be added arithmetically when forecasting total runtime.

### 3.2 Supporting changes

Supporting refactors that save less than two minutes may be implemented when required to safely deliver an in-scope improvement. Examples include passing captured setup snapshot IDs into evaluation, batch persistence, accurate fetch estimates, and phase-level timing.

### 3.3 Out of scope as standalone performance work

The following measured opportunities do not independently meet the two-minute threshold and are not release drivers for this specification:

- IB fetch bookkeeping and commit batching: estimated 60–90 seconds.
- Winner-prediction bulk persistence: estimated 20–30 seconds.
- Market-regime query projection: estimated 10–20 seconds.
- PostgreSQL index-only changes for technical scoring: frame loading measured only 5.4% of sampled technical time.
- Vacuuming or enabling `pg_stat_statements`: operationally useful, but no demonstrated two-minute per-run saving.

## 4. Definitions

| Term | Definition |
| --- | --- |
| Foreground pipeline time | Time from `pipeline_runs.started_at` to `pipeline_runs.completed_at`. |
| Cold technical run | No reusable technical artifact exists for a ticker’s current input signature. |
| Warm technical run | A valid technical artifact exists for a ticker’s current input signature. |
| Input signature | Deterministic identifier covering all price-series versions, benchmark versions, sector-series versions, engine versions, and configuration that affect a reusable technical artifact. |
| Series version | Monotonically increasing version for one `(ticker, timeframe, what_to_show)` price series. |
| Latest eligible bar | Most recent completed daily bar with a non-null close, selected using the existing `TRADES` then `ADJUSTED_LAST` source preference. |
| Prewarm | A background job that resolves contracts and refreshes price data before a user starts the foreground pipeline. |
| Output parity | Equality of persisted scores, classifications, confidence, flags, missing-data semantics, and lineage, excluding intentionally variable timestamps and surrogate IDs. |

## 5. Functional Requirements

### PERF-FR-001: Latest-bar setup context

**Priority:** MUST

The setup-lifecycle source loader shall retrieve only data actually required by the snapshot builder. For the current builder, it shall retrieve at most one latest eligible price bar per ticker rather than complete price history.

Acceptance conditions:

- A 454-ticker run shall materialize no more than 454 latest price-bar ORM rows for setup-lifecycle context construction.
- Context cutoff calculation shall be O(T), where T is the number of tickers, after query execution.
- The implementation shall not scan the complete run price-bar collection once per ticker.
- Source preference, cutoff date, missing-bar handling, and selected `price_bar_id` shall match the current implementation.

### PERF-FR-002: Single setup capture per pipeline

**Priority:** MUST

The pipeline shall build setup snapshots once and reuse the resulting snapshot IDs during lifecycle evaluation.

The lifecycle evaluator shall accept an existing capture result or an explicit immutable set of snapshot IDs. It shall not recapture or reassign the same run’s snapshots when a valid capture result is supplied.

Standalone lifecycle evaluation outside the full pipeline shall retain the ability to capture snapshots when no valid capture result is supplied.

### PERF-FR-003: Pure technical computation boundary

**Priority:** MUST

Per-ticker technical feature calculation shall be refactored into a deterministic function that does not access a SQLAlchemy session and does not mutate the database.

The work item shall contain only serializable data and configuration required to produce the ticker’s base features, higher-timeframe features, relative-strength features, debug evidence, warnings, and missing-data result.

### PERF-FR-004: Bounded process parallelism

**Priority:** MUST

Cold technical work shall execute in a configurable process pool because the measured bottleneck is CPU-bound pandas calculation.

Requirements:

- Default worker count shall be bounded by both configuration and available logical CPUs.
- SQLAlchemy sessions and ORM objects shall never cross process boundaries.
- Worker exceptions shall produce the same ticker-level unavailable score semantics as the sequential implementation.
- Result ordering and final universe leadership ranking shall be deterministic regardless of worker completion order.
- A configuration flag shall allow immediate fallback to sequential computation.

### PERF-FR-005: Batch technical input loading

**Priority:** SHOULD

The coordinator should load required OHLCV columns in batches and create plain DataFrames or arrays before dispatch. It shall avoid opening a database session inside worker processes.

Batch loading is a support requirement for safe parallelism, not the primary source of expected savings.

### PERF-FR-006: Technical artifact cache

**Priority:** MUST

The system shall persist reusable ticker-local technical artifacts and reuse them when the local input signature is unchanged.

The required local artifact shall include ticker-local technical features and higher-timeframe features. It shall not be invalidated merely because SPY, QQQ, or a sector benchmark changed.

Relative-strength features may use a separate cache keyed by both ticker and benchmark series versions, or may be recomputed for every run. Universe-relative leadership ranks, market-regime inputs, and final run-specific score rows shall always be recomputed for every run.

### PERF-FR-007: Exact cache invalidation

**Priority:** MUST

The local technical artifact cache shall miss whenever any local input capable of changing the artifact changes, including:

- adjusted-price series version;
- trades series version;
- technical engine version;
- scoring configuration hash;
- indicator configuration hash; or
- artifact schema version.

If a relative-strength artifact cache is implemented, its independent signature shall additionally include the SPY or configured benchmark series version, configured sector benchmark version, and relative-strength configuration hash. QQQ market-proxy features remain run-level inputs and shall not invalidate ticker-local artifacts.

Cache reuse shall never be based only on latest bar date or row count.

### PERF-FR-008: Series-version maintenance

**Priority:** MUST

Every successful insert, update, or revision of a price series shall atomically advance the corresponding series version. Unchanged bars shall not advance it.

Full backfills, top-ups, forced refreshes, and revision handling shall all use the same version-maintenance path.

### PERF-FR-009: Fetch/technical overlap

**Priority:** MUST

The foreground pipeline shall be able to submit a ticker for technical artifact computation as soon as all market-data actions required for that ticker are committed or confirmed skipped.

Requirements:

- IB access and pacing shall remain serialized through the existing IB client boundary.
- CPU workers may run while the fetch executor waits for IB pacing.
- Final universe ranking and `technical_scores` persistence shall occur only after all ticker artifact results are available or classified as failed.
- No worker shall read uncommitted price bars.
- Cancellation, job lease loss, and fatal fetch failure shall cancel or drain outstanding work without leaving a run marked complete.

### PERF-FR-010: Market-data prewarm job

**Priority:** MUST

The system shall provide an idempotent background job that resolves missing contracts and refreshes required daily market data before the foreground pipeline.

The prewarm universe shall be explicitly derived from configured watchlists, recent upload runs, or an operator-supplied ticker set. The job shall not infer unlimited external symbols.

The foreground pipeline shall continue to verify freshness and fetch missing data when prewarm is absent, stale, partial, or failed.

### PERF-FR-011: Prewarm visibility

**Priority:** MUST

The application shall expose prewarm status, covered ticker count, successful and failed requests, freshness cutoff, and completion time. A user shall be able to determine whether a foreground run is expected to require full backfills.

### PERF-FR-012: Conditional dual-stream reduction

**Priority:** CONDITIONAL

The current product requirement to fetch both `ADJUSTED_LAST` and `TRADES` remains authoritative. The system may reduce to one IB request stream, or synthesize one stream from another, only if an automated parity program proves output equivalence.

The optimization shall remain disabled by default until the following gate passes:

- technical output parity across the run-78 fixture;
- coverage of split, dividend, missing-volume, stale-data, and revised-bar cases;
- equality of setup-lifecycle latest-bar choice and source lineage;
- no reduction in volume, adjustment, or revision evidence; and
- explicit approval of the resulting product-requirement change.

If parity fails, both streams shall remain mandatory.

### PERF-FR-013: Accurate request estimate

**Priority:** SHOULD

The fetch plan shall report unresolved contract requests separately and shall provide both a lower-bound and post-resolution expected request count. Contract resolution shall not silently convert zero estimated requests into unreported historical requests.

### PERF-FR-014: Output parity verification

**Priority:** MUST

Every optimized path shall be tested against the current sequential implementation using frozen price bars and configuration.

Parity shall cover:

- one technical row for every input ticker;
- numeric scores at persisted precision;
- classification and action bias;
- confidence and insufficient-data status;
- feature, warning, and sub-tag sets;
- missing-data and explainability payload semantics;
- setup snapshot promoted fields, signals, warnings, hashes, and latest-bar lineage;
- lifecycle canonicalization, change events, transitions, and alerts; and
- unchanged failure isolation behavior.

### PERF-FR-015: Safe fallback

**Priority:** MUST

Operators shall be able to disable process parallelism, artifact caching, fetch/score overlap, and prewarm independently through configuration without a schema rollback.

## 6. Non-Functional Requirements

### PERF-NFR-001: Setup performance

On a PostgreSQL fixture equivalent to run 78, the combined foreground duration of setup capture and lifecycle evaluation shall be no more than 45 seconds at p95 on the reference machine.

The current baseline is 565.570 seconds.

### PERF-NFR-002: Cold technical performance

For 454 tickers with approximately 756 daily bars in both data streams and no technical artifact cache hits, technical processing shall complete in no more than 270 seconds at p95 on the reference machine with the default worker configuration.

The current baseline is 513.689 seconds.

### PERF-NFR-003: Warm technical performance

For the same workload with at least 75% valid local-artifact cache hits, technical processing shall complete in no more than 120 seconds at p95, including relative-strength calculation, final leadership ranking, and persistence.

### PERF-NFR-004: Fetch/score critical path

When IB fetching and technical computation both occur, their combined elapsed span shall not exceed the slower component by more than 60 seconds at p95, excluding documented retry backoff.

The optimized combined span shall save at least 120 seconds relative to sequential execution on the run-78 fixture.

### PERF-NFR-005: Prewarm effectiveness

For the frozen run-78 universe, a successful current prewarm shall reduce foreground full-backfill requests from 64 to zero. This removes at least 192 seconds from the foreground IB pacing floor.

### PERF-NFR-006: End-to-end targets

Using the run-78 fixture and the existing conservative IB pacing configuration:

- A cold foreground run without prewarm shall complete in no more than 18 minutes at p95.
- A recurrent run with current prewarm and at least 75% valid local-artifact cache hits shall complete in no more than 10 minutes at p95.
- These targets exclude deliberate IB retry backoff caused by external failures.

### PERF-NFR-007: Determinism

Repeated optimized executions against identical database state and configuration shall produce parity-equivalent outputs regardless of process scheduling or cache hit distribution.

### PERF-NFR-008: Resource bounds

- Technical worker count shall default to at most four and shall be configurable.
- The coordinator shall bound in-flight work items and result buffering.
- Setup lifecycle shall not materialize full price history.
- Peak resident memory for a run-78-sized optimized pipeline shall be measured and shall not exceed the sequential baseline by more than 50% without explicit approval.

### PERF-NFR-009: Broker safety

No optimization shall weaken IB pacing, retry, read-only connection, contract validation, or no-order-placement guarantees.

### PERF-NFR-010: Transaction safety

Price-series version advancement and associated bar mutations shall commit atomically. Technical artifacts shall not become visible until fully written. A failed pipeline shall not publish a partially ranked set of technical scores as complete.

### PERF-NFR-011: Observability

Each pipeline run shall record durations and counts for at least:

- setup latest-bar query;
- setup context construction;
- setup build and persistence;
- technical batch loading;
- technical cache hits and misses;
- technical worker CPU span;
- leadership ranking and persistence;
- IB pacing wait;
- IB network time;
- bar-cache write time;
- number of tickers scored while fetching;
- prewarm age and coverage; and
- optimized-path fallback count.

### PERF-NFR-012: Compatibility

Existing routes, persisted result contracts, exports, run history, and failure semantics shall remain backward compatible unless separately versioned.

## 7. Acceptance Test Matrix

| Test ID | Requirement coverage | Test |
| --- | --- | --- |
| PAT-01 | PERF-FR-001, PERF-NFR-001 | Run setup loading for 454 tickers and assert at most 454 bar rows are materialized and both lifecycle steps finish within budget. |
| PAT-02 | PERF-FR-002 | Execute a full pipeline and assert one capture operation, one snapshot identity per ticker, and no capture reassignment between evaluation runs. |
| PAT-03 | PERF-FR-003/004/014 | Compare sequential and parallel cold technical outputs for all run-78 tickers. |
| PAT-04 | PERF-FR-006/007/008 | Reuse cache with unchanged series; revise one historical bar and assert only dependent artifacts miss. |
| PAT-05 | PERF-FR-009, PERF-NFR-004 | Use a paced fake IB client and prove CPU tasks overlap pacing waits while final results remain deterministic. |
| PAT-06 | PERF-FR-010/011, PERF-NFR-005 | Prewarm the frozen run-78 universe and assert zero foreground full-backfill requests. |
| PAT-07 | PERF-FR-012 | Run dual-stream parity corpus; confirm optimization cannot be enabled when any parity assertion fails. |
| PAT-08 | PERF-FR-015 | Disable each optimization independently and verify the pipeline completes through the established fallback path. |
| PAT-09 | PERF-NFR-006 | Execute cold and recurrent end-to-end PostgreSQL benchmarks and enforce the 18-minute and 10-minute budgets. |
| PAT-10 | PERF-NFR-008/009/010 | Verify worker, memory, IB pacing, transaction, cancellation, and job-lease bounds under failure injection. |

## 8. Release Gates

The optimized pipeline shall not become the default until:

1. PAT-01 through PAT-06 and PAT-08 through PAT-10 pass against PostgreSQL.
2. Output parity passes for run 78 and at least two additional representative runs.
3. A sequential fallback has been exercised successfully.
4. New metrics are visible in the run diagnostics.
5. No IB pacing or order-safety regression is present.
6. Cache invalidation passes insert, update, revision, configuration-change, and benchmark-change tests.
7. Conditional dual-stream reduction remains off unless PAT-07 and product approval both pass.

## 9. Traceability Summary

| Improvement | Requirements | Primary acceptance tests |
| --- | --- | --- |
| IMP-01 Setup latest-bar projection | PERF-FR-001, PERF-FR-002, PERF-NFR-001 | PAT-01, PAT-02 |
| IMP-02 Parallel cold technical work | PERF-FR-003 through PERF-FR-005, PERF-NFR-002 | PAT-03, PAT-09 |
| IMP-03 Technical artifact cache | PERF-FR-006 through PERF-FR-008, PERF-NFR-003 | PAT-04, PAT-09 |
| IMP-04 Fetch/technical overlap | PERF-FR-009, PERF-NFR-004 | PAT-05, PAT-09 |
| IMP-05 Market-data prewarm | PERF-FR-010, PERF-FR-011, PERF-NFR-005 | PAT-06, PAT-09 |
| IMP-06 Conditional request reduction | PERF-FR-012 | PAT-07 |
