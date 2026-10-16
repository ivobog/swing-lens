# Task 2.9.12o Systemic Root-Cause Analysis

## Result

The original `DECISION_MANIFEST_MISMATCH` is reproduced, explained, and remediated without
changing the v1/v2 anchor schema or weakening equality. CERI count reporting and handoff-stage
semantics are reconciled, and the affected 163-test cross-stage selection passes.

The task is nevertheless **not systemically complete**. The latest real Chromium-100 execution,
`20261015T143136Z-f710dce5`, failed the unchanged 120-second PostgreSQL transaction invariant in
Setup capture. The Phase 2.9 release suite was therefore not authorized.

## Preserved baseline

- Branch: `codex/ceri-authority-remediation`
- Baseline HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`
- Alembic head: `0092_phase2_eligibility_ledgers`
- Pre-certification source-pin result: `{"stale":[]}`
- Existing dirty worktree and Tasks 2.9.12a-n artifacts were preserved.
- No reset, stash, commit, push, deployment, live-provider call, or normal-database mutation was
  performed.

## Original decision-anchor failure

### Reproduction

Execution `20261015T115830Z-9cc625da` reproduced the failure at
`SETUP_PRE_MUTATION_REVERIFICATION`:

- Contract: `transition-run-start-anchor-v2`
- Stored fingerprint: `df40df7a64096ffe403a701bc0caba3975057aa3d391d9a10f835e2d65feec65`
- Reconstructed fingerprint: `080dcd29dadcb1663e4de40d5c6220e1a6c0e350965b2fa52bfc230c24b2f69b`
- Stored population: 10
- Reconstructed population: 0
- Source identity: upload run 3, pipeline run 1, preflight plan 1, market context 1
- Frozen cutoff: `2026-10-15T11:59:15.010669Z`

There were exactly 20 differences: the ten
`$.immutable_source_lineage.<ticker>` objects and the ten `$.ticker_population[index]` values were
missing from the reconstruction. There were no changed values, extra fields, ordering changes,
PIT-context changes, configuration changes, or source-row mutations. The exact safe digests and
origins are in `TASK_2_9_12O_ANCHOR_FIELD_DIFF.json`.

### Causal mechanism

This was cause class **G — asynchronous continuation/session-bound consumer inconsistency**.

`SetupLifecycleSourceLoader` loaded `RawCompanyRow` objects in a bounded child SQLAlchemy Session.
The child Session closed before the Setup builder consumed those objects. The builder's
`_source_lineage` projection reads `context.raw_row.run.status`, but `RawCompanyRow.run` had not been
eager loaded. Each of the ten detached relationship reads raised `DetachedInstanceError`.

The old reconstruction path collected those per-ticker build errors and continued with no built
rows. It then compared an empty reconstructed anchor to the valid stored anchor, collapsing the
real reconstruction defect into a generic `DECISION_MANIFEST_MISMATCH`.

The authoritative producer did not mutate evidence and the v2 contract was correct. The violating
consumer was the Setup continuation's detached-object reconstruction path.

### Why focused tests missed it

Earlier focused fixtures used bound ORM objects, mocks, or a Session that remained open while the
builder executed. They did not reproduce the production sequence:

1. load the real graph in a bounded child Session;
2. close that Session;
3. reconstruct the decision graph in the asynchronous continuation;
4. access `RawCompanyRow.run.status` from the detached graph.

The failure also remained opaque because per-ticker reconstruction errors were not raised before
the final equality comparison.

## Coherent remediation completed

1. `SetupLifecycleSourceLoader` now eager-loads `RawCompanyRow.run`, making the returned read graph
   build-ready after the bounded Session closes.
2. Reconstruction failures now terminate with typed
   `DECISION_MANIFEST_RECONSTRUCTION_FAILED` evidence before anchor equality is evaluated.
3. Anchor mismatch diagnostics retain the original classification while adding expected/actual
   fingerprints, exact canonical paths, safe digests, source origins, frozen cutoff, producer,
   consumer, and authority identities.
4. Both v1 and v2 remain independently reconstructed and exactly compared. No stored anchor or
   handoff is rewritten.
5. A `VerifiedTransitionDecisionHandoff` certificate binds the complete durable preflight/handoff
   pair. The publication Session reloads both rows with locks and rejects any authority change
   immediately before mutation.
6. Expensive read-only verification uses a bounded child Session with five-member checkpoints.
7. CERI completion writes the exact certified count to both
   `ceri_certified_capture_count` and `ceri_score_snapshots` for new and coalesced continuations.
8. Decision-handoff status now distinguishes evidence committed/frozen, downstream verification
   pending, and consumer accepted.
9. Pipeline progress reporting no longer dereferences an expired `PipelineRun` merely to recover
   its identity, and Setup caches the scalar upload-run ID before the stage-start commit.

## Downstream impact

The shared anchor verifier is used at preflight/enqueue verification, handoff freeze, and Setup
pre-mutation re-verification. Its failures flow through the background-job deterministic
classifier, pipeline roll-up, JUnit, retained failure evidence, and operator-visible status.
Setup and Winner also consume the frozen plan/handoff lineage. These consumers were included in
the 163-test cross-stage selection, including real PostgreSQL positive, negative mutation,
v1/v2, restart/continuation, Setup, Winner, execution-authority, and CERI-barrier tests.

## Residual Chromium-100 blocker

### Latest evidence

The final gated execution `20261015T143136Z-f710dce5` entered
`CAPTURING_SETUP_SIGNALS` at `2026-10-15T14:47:58Z` and failed at
`2026-10-15T14:49:58.964456Z`:

- code: `POSTGRES_TRANSACTION_AGE_EXCEEDED`
- PID: 33844
- state: `idle in transaction`
- last statement: `RELEASE SAVEPOINT sa_savepoint_91`
- transaction age: `120.326891` seconds
- active background jobs: 1
- no Setup progress checkpoint was emitted between stage start and failure

Native JUnit is retained in `TASK_2_9_12O_CHROMIUM_100_CERTIFICATION.xml`; execution-owned logs,
environment metadata, and the invariant stream are retained under
`test-results/single-run-certification/20261015T143136Z-f710dce5/`.

### Most specific causal localization

Code and runtime chronology identify a remaining parent-Session ownership gap in
`SetupLifecycleSnapshotCaptureService.capture_snapshots_for_run`:

1. The bounded source read commits and closes correctly.
2. The `SOURCE_CONTEXT` progress callback then writes progress through the parent pipeline Session.
3. That callback leaves the root transaction open after its savepoint is released.
4. The service enters the CPU-heavy 100-member `build_run_context_snapshots` call.
5. Although that helper supports a checkpoint callback, the production call does not pass it; no
   transaction closure or progress boundary occurs during the build.
6. The observer rejects the root transaction at 120.326891 seconds.

This is distinct from the earlier detached-row anchor defect and from the later bounded manifest
verification work. It also explains why Chromium-10 passes while the 100-member path fails.
The invariant output proves the transaction lifetime, stage, and last SQL statement; the precise
parent progress-callback ownership is the sole matching code path immediately before the
uninstrumented build. A statement-to-call SQL trace was not captured, so that final attribution is
reported as code-supported localization rather than a completed remediation.

No further patch or retry was made after this failure. Doing so would have produced an uncertified
source state and violated the no-blind-retry gate.

## Independent acceptance statuses

- **SYSTEMIC REMEDIATION: FAIL** — the original anchor, CERI, and handoff defects are corrected,
  but the newly identified Setup transaction-lifetime defect remains.
- **CHROMIUM-100: FAIL** — latest native result: 1 failed in 1126.89 seconds.
- **FULL PHASE 2.9 RELEASE: NOT RUN / BLOCKED** — only authorized after Chromium-100 PASS.

