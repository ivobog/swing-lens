# T09B Domain-Write Fencing

## 1. Baselines

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T09A remediation base | `2e0487a271d425b30fb61b17a33717562e0abe3b` |
| T09B starting HEAD | `2e0487a271d425b30fb61b17a33717562e0abe3b` |
| Branch | `codex/t09b-domain-write-fencing` |
| Final HEAD | T09B remediation commit recorded in the final certification response |
| Commit | This focused remediation commit |
| Migration head | `0074_ceri_evidence_quarantine` |
| Python | `3.12.2` from `.venv` |

There was no tracked implementation drift from the T09A remediation base. Pre-existing
untracked architecture/audit documents and IB historical probe files were preserved.

## 2. Finding scope

- `XINT-009`: implementation **CLOSED** in the T09B working tree. Every synchronous
  SQLAlchemy `Session.commit()` executed by a durable handler is now fenced by the exact
  parent job execution token at the database commit boundary. The IBMI feature rebuild is
  also checkpointed and committed per ticker.
- `PIPE-009`: implementation **CLOSED** for every known registered durable-handler path
  inspected below. Safe outer transactions remain safe; previously partial internal-commit
  paths inherit the non-bypassable worker commit fence.
- Winner publication monotonicity/idempotency is not part of this verdict and remains T09C.

## 3. Ownership model

`claim_next_job` selects a ready `BackgroundJob`, marks it `RUNNING`, assigns its worker and
worker-instance identity, and creates a fresh random `execution_token`. The worker captures
that token before any handler commit. Heartbeat, progress, finalization, failure, deferral,
and cancellation updates already condition on job ID, `RUNNING` state, and that exact token.

Lease expiry makes a job eligible for recovery. Recovery clears the old token/worker lease
and requeues the job; the next claim receives a new token. Under the repository's actual
authority model, ownership loss is linearized by the job-row status/token replacement, not
by cancellation state, content identity, or mere row existence.

T09B adds `assert_current_execution_ownership`. Immediately before an ORM commit, it issues
`SELECT status, execution_token ... FOR UPDATE` for the parent job and requires `RUNNING`
plus the captured token. The row lock remains held through the same transaction's domain
commit or rollback. Reclaim updates the same row, so the two operations serialize:

- reclaim commits first: the old attempt reads the replacement token and raises
  `JobLeaseLost` before flush/commit;
- the old attempt locks first while still current: its domain commit is ordered before the
  later reclaim.

This removes the check-then-commit race. No new ownership state or schema was introduced.

## 4. Internal-commit inventory

| Job/handler | Domain artifact | Internal commits? | Pre-commit ownership fence before T09B | Same transaction before T09B? | Before T09B status | After T09B status |
|---|---|---:|---|---:|---|---|
| `FULL_PIPELINE` | pipeline/core calculation rows | Yes | heartbeat/lease guard and token progress | Yes at guarded saves | `SAFE_FENCED_INTERNAL_COMMIT` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| IBMI historical refresh | historical metric current/revisions, run/request/checkpoint | Yes, range/ticker commits | checkpoint heartbeat on normal batches; final/error commits varied | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| IBMI live refresh | live snapshots, feature inputs, run/request/checkpoint | Yes, ticker commits | checkpoint heartbeat on normal batches; final/error commits varied | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| IBMI scanner | scanner runs/candidates/request state | Yes, preset commits | checkpoint heartbeat | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| IBMI histogram | histogram snapshots/bins/request state | Yes, ticker commits | checkpoint heartbeat | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| IBMI flex | imported executions/episodes/research and run state | Yes, polling/final/error commits | checkpoint heartbeat on some commits | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| IBMI feature rebuild | calculated liquidity/volatility/short-pressure/options features | Yes; formerly one handler commit, now one bounded commit per ticker plus final run commit | cancellation only at the relevant path; content hash was duplicate protection | No | `CONFIRMED_STALE_COMMIT_RISK` (`XINT-009` positive control) | `SAFE_ATOMIC_DOMAIN_FENCE` |
| `IB_FETCH` | `PriceBar`, revisions, fetch run/items, Winner market-data obligations | Yes, including bounded child sessions | main item commits used token-fenced progress; start/failure/final obligation commits were not uniformly fenced | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| `MARKET_DATA_PREWARM` | `PriceBar`, revisions, fetch telemetry/coverage | Yes through fetch executor child sessions | heartbeat/cancellation occurred before work; no fetch progress/token callback was supplied | No for all internal commits | `CONFIRMED_STALE_COMMIT_RISK` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| CERI provider and batched provider | ingestion/source evidence and normalize continuation | SEC incremental commits plus outer transaction/checkpoint commits | parent heartbeat was cooperative; SEC document execution token fenced document state but was not parent-job authority | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| CERI SEC incremental | registered/downloaded document state, source records, extraction state | Yes, multiple per document | document-level token only; parent job fence was not present at every commit | No for parent job | `CONFIRMED_STALE_COMMIT_RISK` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| SEC readiness repair | processor/readiness state | Yes | cooperative heartbeat/cancellation around phases | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| CERI normalize/features/capture/change/backfill/alerts/purge | CERI normalized/features/snapshots/changes/alerts/purge state | Flush/savepoint or heartbeat commits | outer worker transaction or conditional progress/heartbeat commit | Yes where committed | `SAFE_OUTER_TRANSACTION` / `SAFE_FENCED_INTERNAL_COMMIT` | `SAFE_ATOMIC_DOMAIN_FENCE` |
| Setup/Lifecycle handlers | setup snapshots, episodes, alerts | Flush/savepoint; no independent root commit found | outer worker final transaction | Yes | `SAFE_OUTER_TRANSACTION` | `SAFE_OUTER_TRANSACTION` |
| Winner handlers | predictions/outcomes/cohorts/rescores and processing state | Mostly outer; prediction capture and canary use internal/child commits | outer paths safe; child commits were not uniformly passed a progress fence | Partial | `PARTIALLY_FENCED` | `SAFE_ATOMIC_DOMAIN_FENCE` |

Static review found no raw-connection or engine-level commit in the registered handler trees.
The fencing context follows synchronous child ORM sessions through a `ContextVar`; no
registered path was found to move a commit into an unpropagated native thread.

## 5. Implementation

### Generic durable worker

The worker now passes its captured, immutable attempt token into `execute_job`. A scoped
domain-write ownership context surrounds the handler. A SQLAlchemy `before_commit` listener
applies the row-lock/token assertion to every ORM session commit while that context is
active, including child sessions created by IB fetch and Winner capture.

On ownership loss, `JobLeaseLost` propagates through the existing worker outcome. The worker
rolls back, does not call success finalization, and does not commit pending descendants.
The new owner is unaffected because it executes under its own token.

### IBMI feature rebuild

Old behavior prepared all requested ticker features and committed them with the run only at
the end. It checked cancellation but did not establish parent job ownership at that commit.

New behavior checks cancellation before each ticker, prepares exactly one ticker, records a
checkpoint for that ticker, and commits the feature plus checkpoint as a bounded unit. The
generic listener locks and validates the parent job at that commit. Progress is therefore
never advanced past a rolled-back ticker. The final run completion commit is fenced too.

### Other handlers

No handler-specific lease system was added. Existing content hashes/natural keys remain
duplicate protection only. Existing SEC document tokens remain document-work ownership only.
Parent durable-job authorization now comes from the generic execution-token fence.

## 6. IBMI positive control

The regression executes the real `execute_feature_rebuild` loop with persistence isolated
to a real SQLite transaction. Ticker `AAA` commits under token A. Ticker `BBB` is prepared,
then another worker thread durably replaces the job token with token B before the commit.
The before-commit fence observes token B, raises `JobLeaseLost`, and the worker transaction
rolls back. `AAA` remains, `BBB` is absent, and `CCC` was never called. A new execution using
token B then commits `BBB` and `CCC` successfully.

## 7. Tests

| Regression | Evidence |
|---|---|
| Valid owner commits | `test_valid_execution_owner_commits_domain_mutation` persists the expected domain row. |
| Ownership lost after prepare | `test_ownership_lost_after_prepare_rejects_commit_and_stops` replaces the token after ORM preparation and proves rollback/no continuation. |
| Reclaim between batches | `test_ibmi_reclaim_between_tickers_fences_old_owner_and_new_owner_continues` preserves ticker 1 and rejects ticker 2. |
| New owner succeeds | The same IBMI positive control commits the remaining tickers under token B. |
| Child sessions are covered | `test_child_session_commit_is_fenced_by_parent_job_ownership` proves a child ORM session cannot bypass the parent fence. |
| No stale continuation | `test_stale_owner_cannot_commit_continuation` proves pending descendant state is absent. |
| No stale success finalization | `test_worker_does_not_finalize_success_after_fenced_handler_commit` proves the worker never invokes completion; existing `test_old_worker_cannot_commit_after_lease_is_replaced` proves the final CAS guard. |
| Idempotency is not authority | `test_idempotency_does_not_authorize_stale_commit` uses an idempotent SQLite insert and still receives `JobLeaseLost`. |
| Cancellation differs from ownership | `test_not_cancelled_does_not_override_stale_execution_token` has `requested_cancel=false` and still rejects token A. |

## 8. Test exclusions

Repository markers are `unit`, `integration`, `e2e`, `slow`, `destructive`, `external`,
`security`, and `performance`. There is no `postgres` marker.

- Live external/provider: `tests/ib_market_intelligence/test_external_smoke.py` was ignored;
  `tests/integration/test_background_worker_external.py` was excluded with the integration
  tree; marker expression excluded `external`.
- Browser/E2E: `tests/e2e/` was ignored; `tests/integration/test_webapp_fix_flows.py` was
  excluded with the integration tree; marker expression excluded `e2e` and `slow`.
- PostgreSQL-dependent: `tests/integration/` was ignored. Inspection showed this tree uses
  the disposable PostgreSQL fixtures or environment/process integration, including the
  `*_postgresql.py`, IBMI persistence/runtime, CERI batched workflow, SLSE vertical/corpus,
  restore, and external-worker files.

These exclusions are intentional project policy for T09B, not test failures.

## 9. Verification results

### Focused fencing

```text
.venv\Scripts\python.exe -m pytest \
  tests/test_domain_write_fence.py \
  tests/test_background_worker.py \
  tests/test_background_job_service.py -q
```

Result: **58 passed, 0 failed, 0 skipped, 0 deselected, 1 warning**.

### Affected subsystem

```text
.venv\Scripts\python.exe -m pytest \
  tests/test_domain_write_fence.py tests/test_background_worker.py \
  tests/test_background_job_service.py tests/test_background_queue.py \
  tests/test_ib_fetch_executor.py tests/test_ib_fetch_job_service.py \
  tests/test_market_data_prewarm.py tests/ib_market_intelligence tests/ceri \
  --ignore=tests/ib_market_intelligence/test_external_smoke.py \
  -m "not external and not e2e and not slow" -q
```

Result: **606 passed, 0 failed, 0 skipped, 7 deselected, 1 warning**.

### Broader clean lane

```text
.venv\Scripts\python.exe -m pytest tests \
  --ignore=tests/e2e \
  --ignore=tests/integration \
  --ignore=tests/ib_market_intelligence/test_external_smoke.py \
  -m "not external and not e2e and not slow" -q
```

Result: **2401 passed, 0 failed, 7 skipped, 33 deselected, 22 warnings**.

Static verification: Ruff passed for all changed Python files and `git diff --check` passed.

## 10. Certification Unblock

Before the fixture correction, the following exact test was run in both environments:

```text
.venv\Scripts\python.exe -m pytest \
  tests/test_observability_review2.py::test_metrics_off_worker_and_supervisor_start_no_prometheus_components \
  -q
```

| Environment | Result | Traceback cause |
|---|---|---|
| Detached clean worktree at T09A base `2e0487a271d425b30fb61b17a33717562e0abe3b` | 1 failed | `AttributeError: 'types.SimpleNamespace' object has no attribute 'runtime_mode'` at `app/worker_supervisor.py:97` |
| T09B working tree before the fixture correction | 1 failed | The identical missing-`runtime_mode` traceback at the identical production line |

Classification: **PRE_EXISTING_TEST_DEFECT**. The real `Settings` contract declares
`runtime_mode: RuntimeMode = RuntimeMode.NORMAL`, and the supervisor legitimately reads its
`.value`. The test's hand-written settings double predated that required contract.

The only correction was to import `RuntimeMode` in `tests/test_observability_review2.py` and
add `runtime_mode=RuntimeMode.NORMAL` to that test's `SimpleNamespace`. No production
supervisor, observability, metrics, or lifecycle code was changed.

Post-correction verification:

- failing test alone: **1 passed, 0 failed, 0 skipped, 0 deselected, 1 warning**;
- nearby `tests/test_observability_review2.py` module: **44 passed, 0 failed, 0 skipped,
  0 deselected, 1 warning**;
- final broader clean lane: **2401 passed, 0 failed, 7 skipped, 33 deselected,
  22 warnings**.

## 11. PostgreSQL/external/E2E certification status

```text
Domain fencing implementation certification: PASS
Full PostgreSQL runtime concurrency certification: DEFERRED BY PROJECT TEST POLICY
External-provider certification: DEFERRED
Browser/E2E certification: DEFERRED
```

The implementation has the required PostgreSQL row-lock structure, but the intentionally
excluded PostgreSQL concurrency lane was not run and no production database was accessed.

## 12. Residual risks

- Full PostgreSQL reclaim-versus-commit concurrency certification remains deferred.
- Live provider and browser/E2E behavior remains deferred by policy.
- A future durable handler that deliberately commits on a new native thread without copying
  the execution context, or bypasses SQLAlchemy `Session.commit()` via a raw connection,
  must explicitly propagate/use the fence. No such registered path was found.
- T09C Winner publication monotonicity/idempotency and Phase 1+ redesign remain deferred.

## 13. Database impact

```text
migration required: NO
production data rewrite required: NO
```

No production/runtime database, provider, pipeline, queue, Winner generation, or evidence
state was mutated.

## 14. Finding verdict

```text
XINT-009: CLOSED (implementation and focused certification)
PIPE-009: CLOSED for all known registered durable-handler commit paths
```

The generic commit boundary, child-session regression, and static inventory support these
finding verdicts. They do not upgrade the deferred PostgreSQL runtime certification.

## 15. T09B verdict

```text
T09B DOMAIN-WRITE FENCING: PASS
```

The fencing implementation, certification-critical races, affected subsystem lane, and
broader clean unit/service lane all pass. The only previous blocker was independently proven
to be a stale test double and was corrected without changing production lifecycle behavior.
