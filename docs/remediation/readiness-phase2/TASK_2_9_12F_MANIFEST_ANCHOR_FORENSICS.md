# Task 2.9.12f Decision Manifest Anchor Forensics

## Executive verdict

**TASK 2.9.12F MANIFEST ANCHOR REMEDIATION: CLOSED.**

The one authorized focused PostgreSQL certification passed. The legitimate durable
continuation now reaches the Setup pre-mutation boundary without invalidating its
decision anchor, while a deliberate immutable raw-source mutation still fails closed
before any Setup publication.

This is a task-local closure only. No Chromium-100, browser/E2E, broad pipeline, CERI,
Setup, or repository suite was run. The full release remains **UNCERTIFIED**.

## Retained incident

Task 2.9.12e retained this terminal failure from runtime
`20261014T173821Z-65dd66de`:

```text
DECISION_MANIFEST_MISMATCH: run-start anchor no longer matches at decision handoff
```

The initial `FULL_PIPELINE` job completed its asynchronous handoff, CERI completed,
continuation job 32 completed `FREEZING_DECISION_HANDOFF_MANIFEST`, and Setup then
failed at `CAPTURING_SETUP_SIGNALS` before processing ticker 1 of 100. Worker
generation, retry/recovery counters, lease validity, and CERI completion were clean.
That evidence made a pre-fix rerun unnecessary, so no baseline was started.

## Identity chain and exact comparison

The production identity chain is:

1. Certification UI admission creates a `TransitionPreflightPlan`.
2. `run_start_anchor_json` and `run_start_anchor_fingerprint` persist its admission
   identity.
3. The durable CERI graph and `FULL_PIPELINE` continuation advance lifecycle state.
4. `freeze_transition_decision_handoff_manifest` verifies the anchor and persists the
   `TransitionDecisionHandoffManifest`.
5. Setup reconstructs the same decision manifests.
6. `verify_transition_decision_manifests_before_mutation` rebuilds and compares the
   anchor immediately before the first Setup write.

The rejected comparison was the structural equality/fingerprint check in
`verify_transition_decision_manifests_before_mutation`, category
`run_start_anchor`. The over-broad v1 payload included these exact paths:

- `run.status`
- `run.processed_at`

Those are mutable lifecycle bookkeeping. They describe when and how the import run
was finalized; they do not identify the financial inputs consumed by Setup. A durable
continuation is allowed to advance them without changing the admitted raw rows,
ticker scope, cutoff, eligible bars, pointers, or producer configuration.

The 2.9.12e cleanup correctly deleted its disposable database. Its structured logger
retained the rejection event but omitted the interpolated expected/actual fingerprint
arguments. Consequently the two literal retained SHA-256 strings cannot be recovered
and are recorded as unavailable, not guessed, in
`TASK_2_9_12F_ANCHOR_COMPARISON.json`. The exact differing JSON paths, comparison
function, category, and state transition are identified and exercised directly by
the focused PostgreSQL proof.

Classification: **A — over-broad mutable field in an immutable manifest**, exposed
by **C — an expected durable-continuation state transition**. It was not a cutoff,
timezone, canonicalization, source-lineage, identity-map, or detached-object defect.

## Remediation

`build_run_start_anchor_manifest` now emits
`transition-run-start-anchor-v2`. The v2 run envelope omits only `status` and
`processed_at`.

The following guards remain in the fingerprint:

- stable run identity, filename, upload instant, row count, and engine versions;
- raw-row count and canonical raw-row fingerprint;
- exact ticker population;
- frozen market context and all source cutoffs;
- per-ticker selection key, raw-row ID, expected pointer, eligible price-bar
  identities, and Setup producer signatures.

No formula, scoring rule, market cutoff, configuration selection, ticker filter,
transaction boundary, retry policy, or release threshold changed. The 2.9.12a
Technical and 2.9.12d Setup transaction-lifetime remediations are untouched.

## Focused PostgreSQL certification

Exactly one final targeted invocation was run:

```text
.\.venv\Scripts\python.exe -m pytest \
  'tests/integration/test_transition_preflight_plan_postgresql.py::test_two_phase_manifest_accepts_committed_run_local_artifacts_and_is_idempotent' \
  -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12F_CERTIFICATION.xml
```

Result: **PASS — 1 passed, 2 warnings in 8.75 seconds**. JUnit records one test,
zero failures, zero errors, zero skips, and 8.736 seconds.

The single node contains both mandatory proofs:

- Positive: after handoff persistence, `UploadRun.status` and
  `UploadRun.processed_at` are changed to simulate the legitimate continuation.
  The v2 anchor fingerprint is unchanged, the same handoff is reused, and the exact
  pre-mutation production verifier passes.
- Negative: `RawCompanyRow.raw_json` is deliberately changed. That changes the
  protected `run.raw_rows_fingerprint`; the same verifier raises
  `DECISION_MANIFEST_MISMATCH`, no `SetupSignalSnapshot` exists, and the persisted
  handoff fingerprint remains unchanged.

The disposable database was created, migrated, used, and removed by the existing
PostgreSQL test fixture. No normal application database, live provider, or historical
run was used.

## T14 source review

Because production source changed, the current Phase 2.9 pins were regenerated only
through the official generator:

```text
.\.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins --write
```

The affected production source hash is:

```text
app/services/transition_preflight_plan_service.py
66070c71cacd57e09c023ac7bb21dd3cb219e16cc928c7eff3d8307fc77e6592
```

Final official validation returned:

```json
{"stale": []}
```

## Scope, timing, and retained limitation

- Task clock start: `2026-10-14T20:12:09.7177885+02:00`.
- Completion checkpoint: `2026-10-14T20:18:12.2778303+02:00`.
- Total elapsed: `362.560` seconds (6 minutes 2.560 seconds), within the
  30-minute budget.
- Focused JUnit timestamp: `2026-10-14T20:16:39.687650+02:00`.
- Authorized final PostgreSQL invocations: 1; used: 1.
- Pre-fix focused baselines: 0.
- Chromium/browser/E2E invocations: 0.
- Broad suites: 0.
- Repair/retest loops: 0.

The retained pre-pipeline fixture transaction observation of **131.806556 seconds**
remains documented and unresolved. It was explicitly outside this task and was not
remediated, suppressed, or reclassified.

## Release statement

**TASK 2.9.12F MANIFEST ANCHOR REMEDIATION: CLOSED.**

This focused PASS removes the manifest-anchor blocker only. It does not certify
Chromium-100 and does not close the full Phase 2.9 release gate.
