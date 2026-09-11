# Final Live Dual-Mode Canary After Operational Remediation — 2026-09-10

## A. Executive verdict

```text
FINAL LIVE DUAL-MODE CANARY: FAIL
SAFE TO MERGE COMPLETE TEMPORAL/PREFLIGHT/LIFECYCLE REMEDIATION TO MAIN: NO
```

Primary failure classification: `CERTIFICATION_GATE_DEFECT`.

The first and only canonical certification-runtime startup did not become ready within its
90-second window. The web process started in `CERTIFICATION`, but the supervisor's production
child-launch path explicitly overwrote `JOB_WORKER_ENABLED=false`. The certification settings
validator requires `JOB_WORKER_ENABLED=true`, so every durable-worker child exited before
registration. The supervisor repeatedly attempted replacement children; `/ready` remained failed
for missing worker registration/heartbeat/control-loop readiness. The hard-stop policy was applied
before IB certification, candidate discovery, plan creation, Run creation, or enqueue. No retry,
code patch, replacement plan, second canary, historical repair, or merge occurred.

## B. Baseline

| Control | Result |
|---|---|
| Branch / execution HEAD | `codex/canary-runtime-isolation-ib-readiness` / `deae9b09b42bcb6aa7e9624732514f30641ccf50` |
| Worktree | clean |
| Required ancestry | `deae9b0`, `d5accbf`, and `c706821` are ancestors: YES |
| Code / production Alembic | `0072_ceri_artifact_context_lineage` / `0072_ceri_artifact_context_lineage` |
| Production database | `swinglens`, PostgreSQL 18.3, user `postgres`, `127.0.0.1:5432` |
| App / worker / supervisor | stopped / stopped / stopped |
| Port 8000 | no listener |
| Jobs | active 0; queued 0; retrying 0; recovering 0; scheduled-due 0 |
| Latest IDs | Run 154 / Pipeline 145 / Job 42981 |
| Control counts | plans 3; contexts 7; pointers 14,647; selection events 2 |
| Existing reservation | Plan 3 persisted `RESERVED` but time-expired; Context 7 unowned |
| Clock | approximately `2026-09-10T19:34:59+02:00` Zurich / `17:34:59Z` / `13:34:59-04:00` New York |
| Market boundary | latest completed session `2026-09-09`; daily bar ready `2026-09-09T20:15:00Z` |
| Versions | `swinglens-us-equities-v1` / `daily-close-plus-15m-v1` |

The actual Run-153 systemic-remediation report/summary, Run-154 failed-canary report/summary, and
runtime-isolation/IB-readiness certification report/summary were read before startup.

## C. Backup

| Item | Evidence |
|---|---|
| Method | PostgreSQL 18 `pg_dump`, custom format, quiescent source |
| Path | `backups/swinglens_final_live_after_ops_20260910_193729.dump` |
| Metadata completed | `2026-09-10T17:50:42.007819Z` |
| Size | 791,837,736 bytes |
| SHA-256 | `038B3778B4B78C55F7231D9FD2CF0D2D4EB589D3019DB814B7A2F01E2B8B0C06` |
| Database / schema | `swinglens` / `0072_ceri_artifact_context_lineage` |
| Verification | PASS: `pg_restore --list` exit 0; deterministic evidence manifest captured |

The dump, metadata, and manifest remain under `backups/` and were not committed.

## D. Certification runtime isolation

The canonical `swinglens.ps1 start` command was invoked once with:

```text
RUNTIME_MODE=CERTIFICATION
JOB_WORKER_ENABLED=true
USE_DURABLE_PIPELINE=true
WINNER_PROBABILITY_AUTO_MATURATION_ENABLED=false
WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED=false
MARKET_DATA_PREWARM_ENABLED=false
```

PostgreSQL provenance and schema head passed. Web PID 24168 and supervisor PID 12036 started.
However, `app.worker_supervisor._start_worker` replaced the inherited value with
`JOB_WORKER_ENABLED=false`. Each `app.worker` import constructed `Settings` and raised:

```text
Value error, CERTIFICATION runtime requires JOB_WORKER_ENABLED=true
```

The supervisor emitted repeated `worker.supervisor.started` events with
`reason=no_usable_registered_worker`, while the web readiness endpoint reported failed checks for
worker registration, heartbeat, and worker readiness. Canonical startup ended:

```text
SwingLens core did not become ready within 90 seconds.
```

Therefore the required one-web/one-durable-worker topology was never established. This is a
production process-topology gap missed by the offline certification tests and a
`CERTIFICATION_GATE_DEFECT`. Zero unrelated business jobs were created or executed, including
Winner/CERI/prewarm/retry/continuation work, but isolation cannot be certified because the required
authorized worker was absent.

## E. IB readiness evidence

Initial authoritative IB state: `NOT_REACHED_RUNTIME_STARTUP_FAILURE`.

Final pre-enqueue IB state: `NOT_REACHED_RUNTIME_STARTUP_FAILURE`.

No startup or pre-enqueue IB result was reused or fabricated. The runbook orders IB certification
after runtime and worker isolation; the worker gate failed first, so no IB decision was taken.

## F. Fresh candidate discovery

`NOT_EXECUTED_FAIL_FAST_RUNTIME_STARTUP_FAILURE`. No candidate was selected and prior ADAM/DRS
choices were not reused.

## G. Fresh preflight plan/context

`NOT_CREATED`. Plan count remained 3 and context count remained 7. Plan 3 and Context 7 were not
reused, altered, expired manually, or repaired.

## H. Pre-enqueue operational gate

`NOT_REACHED`. The prerequisite certification runtime and one-worker gates failed before any
preflight or enqueue action. No Pipeline or root Job was created.

## I. Decision Manifest equivalence

`NOT_EXERCISED_NO_PLAN_OR_PIPELINE`. Decision Manifest mismatches and preflight/execution context
mismatches are not claimed as live zero metrics; there was no selected manifest to compare.

## J. SAME_SESSION_REPLACEMENT evidence

`NOT_EXERCISED`. Same-session replacements: 0. Same-session ledger events: 0.

## K. NEW_SESSION_CANONICAL_INITIALIZATION evidence

`NOT_EXERCISED`. New-session canonical initializations: 0. New-session initialization events: 0.

## L. Derived current-state evidence

No canary-derived current state was produced. All 14,647 pre-existing pointer rows retained their
aggregate hash `8f9529b658b441d4565edfb2cb8de5573257a85a9b50f238216f58dce6529cec`.
Historical derived lookups were not modified.

## M. Pointer/ledger atomicity

Pointer count remained 14,647. The two pre-existing selection events retained aggregate hash
`2f76e51f2e3b13bfc6eaf66a1b271e4a5393f6af3d21866906e03f3d261c62f6`.
No pointer or ledger transaction occurred; duplicates created: 0.

## N. CERI lineage evidence

No pipeline-owned CERI artifact was created. Protected snapshots 10929/10930 and price-response
rows 8684/8685 retained their exact pre-start hashes. No live CERI lineage certification can be
claimed because no pipeline ran.

## O. CERI PIT evidence

No CERI source or price bar was consumed by a canary. Protected sources 867459–867465 retained
their exact hashes and receipt timestamps. Post-cutoff-source, post-cutoff-bar, and missing-
provenance live metrics are `NOT_EXERCISED`, not inferred zeros.

## P. Price-bar immutable evidence

| Bar | Immutable hash before/after | Full-row diagnostic before/after | `last_seen_at` changed |
|---:|---|---|---:|
| 2263479 | `c9049dcc13baaefabb3b99ea24dfb8ce927485e55749b99fca05d0c236285ab5` | `ee1db44fca17de8da90b60a029d09217d0ccfb128a86a4a5449dfc4b02360eda` | no |
| 2263504 | `36127195b9fcce23befdef9d9db6cc926ba2fdd4b973fc870eab056860f013fa` | `5caf3751ceebe82c480fa6bcc4cde8b0c3eb1945e85b0e449a85c477409beff9` | no |

Immutable price-bar evidence changes: 0. Operational price-bar metadata changes: none.

## Q. Historical immutability

All 26,541 pre-existing lifecycle snapshots through ID 36951 retained aggregate immutable hash
`cdbdf8683a4a178f30000bf16e4481744779bb604323acd8b5b5ed6bb5d932a6`.
Upload-run rows and lifecycle snapshot sets for each Run 148–154 matched their pre-start hashes.
The named CERI controls, protected sources, pointers, ledger rows, and bars also matched.

```text
historical_snapshot_rewrites = 0
run_148_immutable_hash_changes = 0
run_149_immutable_hash_changes = 0
run_150_immutable_hash_changes = 0
run_151_immutable_hash_changes = 0
run_152_immutable_hash_changes = 0
run_153_immutable_hash_changes = 0
run_154_immutable_hash_changes = 0
```

## R. Runtime isolation during execution

Canary execution never began. Background Job maximum remained 42981 and no row exists above
42981. Unrelated background jobs created: 0; unrelated background jobs executed: 0. Supervisor
registration/heartbeat metadata advanced during the failed startup; this is control-plane evidence,
not a business workflow.

## S. Database delta

| Area | Delta | Classification |
|---|---:|---|
| Fresh Run / Pipeline / root Job | 0 / 0 / 0 | none created |
| Fresh plan / context | 0 / 0 | none created |
| Background business Jobs | 0 | no unexpected workflow |
| Technical/lifecycle/CERI/IBMI/Winner artifacts | 0 | no pipeline execution |
| Pointers / ledger | 0 / 0 | unchanged |
| Price bars | 0 | unchanged |
| Supervisor registration/heartbeat | updated to generation 30 for `local-worker-1` | expected failed-startup control plane |
| Durable worker registration | no new usable registration | startup defect evidence |

Unexpected business mutations: 0. Production code changes: 0. Historical repair: 0.

## T. Pipeline/PARTIAL classification

Pipeline status: `NOT_CREATED_FAIL_FAST_CERTIFICATION_RUNTIME_STARTUP_FAILURE`.

There is no PARTIAL pipeline. Failure classification: `CERTIFICATION_GATE_DEFECT`.

## U. Post-canary regression

`SKIPPED_FAIL_FAST_CERTIFICATION_RUNTIME_STARTUP_FAILURE`. The runbook permits post-canary suites
only if live certification has not failed and forbids patch-and-continue. No code defect was fixed
in this task.

## V. Final runtime state

The canonical stop path completed after evidence capture. No observability containers were running
before cleanup, so none were displaced.

```text
all canary jobs terminal = YES (none created)
app = STOPPED
worker = STOPPED
supervisor = STOPPED
active jobs = 0
queued jobs = 0
retrying jobs = 0
recovering jobs = 0
scheduled-due jobs = 0
port 8000 listeners = 0
production Alembic = 0072_ceri_artifact_context_lineage
```

The `local-worker-1` supervisor row is stale control-plane evidence with no corresponding OS
process; it was not manually repaired. The worktree contained only this report and its machine
summary before the evidence commit.

## W. Final recommendation

Do not merge and do not run another canary. Analyze and separately remediate the certification
process-topology contract: a durable worker process must be allowed to run in certification mode
without enabling an embedded supervisor, while the runtime must still prove exactly one external
worker. Reproduce the canonical web → supervisor → worker environment handoff in disposable tests
before requesting another live certification. This task performed no production-code patch,
historical repair, second canary, or merge.
