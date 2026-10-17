# Run 40 forensic investigation

Investigated 17 October 2026. Production inspection was read-only. Run 40 was not retried, repaired, or edited. No application source was changed. A separate disposable PostgreSQL database was used for the concurrency reproduction and was dropped afterward.

**Finding:** Run 40 failed because the supervisor's progress watchdog retained a row lock on background job **1384** for approximately **17 seconds**. The worker's independent heartbeat transactions waited for that supervisor transaction and exceeded their **3-second transaction-local lock timeout**. This happened immediately after signal-change detection returned, at the next lifecycle progress callback. The database's recorded blocking PID graph identifies the supervisor as the blocker; this was not an inference from the error message alone.

There are two additional recovery defects: the failed evaluation left **937 persisted signal-change events**, and the worker queued a retry after the pipeline had already become terminal `FAILED`. The queue's execution-authority predicate excludes that retry. Replaying into a new evaluation also has an event-identity conflict that needs explicit handling.

## 1. Scope and runtime identity

The upload, pipeline, jobs, CERI dependency, lifecycle evaluations, persisted outputs, worker/supervisor application logs, SQL telemetry, PostgreSQL health samples, server errors, and the corresponding execution paths were examined.

- Upload/pipeline: **40**, input universe **88 rows**.
- Original pipeline job: **1353**; resumed continuation: **1384**.
- CERI workflow dependency: **55**, `COMPLETED`; final CERI child **1383**, `COMPLETED`.
- Decision handoff manifest: **27**; CERI completion: `CERTIFIED`.
- Snapshot capture evaluation: **56**, `COMPLETED`, captured **88/88**.
- Lifecycle evaluation: **57**, `FAILED`.
- Worker OS process: **15132**; supervisor OS process: **14432**.
- Runtime Git SHA recorded in logs: **96204fb6fa15aed858fa06ba52d4b70c86d1d8b5**, `git_dirty=true`.

The runtime did not identify itself as the subsequently pushed `origin/main` commit `d3b66c32767257d1187719206fa948656ffe3045`. Reproduction used the current checkout; hashes of reviewed source files are in [reviewed-code-sha256.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/reviewed-code-sha256.json). Because the runtime was dirty, its Git SHA alone is not an immutable record of every loaded source file. SQL caller locations and the actual statements corroborate the relevant paths.

All times below are **Europe/Zurich, CEST, UTC+02:00**. JSON telemetry timestamps are UTC.

## 2. Reconstructed timeline

| Local time | Evidence |
|---|---|
| 11:25:57 | Pipeline starts. |
| 11:33:55–12:52:05 | CERI provider ingestion completes and certifies. |
| 12:52:06–12:54:41 | Decision handoff manifest freezes successfully. |
| 12:57:11–13:11:39 | Setup capture stage completes; evaluation 56 captures 88 snapshots. |
| 13:11:40.097 | `EVALUATING_SETUP_LIFECYCLES` starts. |
| 13:18:26.191 | Last persisted job progress: `EVIDENCE_AUTHORITY_VALIDATION_COMPLETED:canonical-setup-candidates`, processed **88/88**, sequence **246**. |
| 13:18:26.200–13:39:24.703 | Change detection performs 33,454 recorded SQL operations and inserts 937 change events. Autonomous heartbeats continue successfully. |
| **13:39:23.903** | Supervisor watchdog successfully executes `SELECT ... FROM background_jobs ... FOR UPDATE SKIP LOCKED`. |
| 13:39:24.702 | Worker releases the detector's enclosing savepoint: the detector has returned successfully. |
| 13:39:24.704, approximately | The next progress callback starts its detached heartbeat update. |
| **13:39:25.549** | Database monitor: worker heartbeat backend **39524** is waiting on supervisor backend **43312**. |
| **13:39:27.792** | Main-thread heartbeat fails after **3,088 ms** with PostgreSQL `LockNotAvailable`; application raises `ControlPlaneLockTimeout`. |
| 13:39:27.815 | Evaluation 57 is marked `FAILED` with that exception. |
| 13:39:27.839, approximately | Business session tries its mandatory commit-time ownership `FOR UPDATE`; it also waits. |
| **13:39:29.612** | Autonomous heartbeat backend **12136** is waiting on **43312**. Supervisor **43312** is `idle in transaction`, waiting for its client, not executing a slow SQL query. |
| **13:39:30.746** | Autonomous heartbeat fails after **3,019 ms**, for the same lock timeout. |
| 13:39:31–40 | Business backend **7184** waits directly on **43312**. |
| **13:39:40.808–40.820** | Supervisor emits `HEALTHY_WITHIN_PROGRESS_TIMEOUT`, flushes watchdog metadata and ends the transaction; business ownership locking query completes after **12,981 ms**. |
| 13:39:41.234–41.264 | Stage failure is logged; parent pipeline becomes `FAILED`. |
| 13:39:43.620 | Worker classifies the exception as retryable and queues job 1384 with retry count 1. |
| 13:40:43.620 | Scheduled retry time; parent pipeline is terminal and the claim predicate does not authorize the job. |

The stage's persisted interval is approximately **27m48s**. The final stage-finished event includes failure cleanup, approximately **28m01s**. Those durations are not the 3-second timeout: that limit applies only to acquiring locks in each control transaction.

## 3. Direct proof of the blocker

The critical database health records are preserved in [blocking-graph.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/blocking-graph.json).

| UTC sample | Waiting backend | Application / operation | `pg_blocking_pids` |
|---|---:|---|---|
| 11:39:25.549 | 39524 | `swinglens-worker`, heartbeat `UPDATE background_jobs` | **[43312]** |
| 11:39:29.612 | 12136 | `swinglens-worker`, autonomous heartbeat update | **[43312]** |
| 11:39:31.638 | 7184 | `swinglens-worker`, ownership `SELECT ... FOR UPDATE` | **[43312]** |
| 11:39:29.612–40.771 | 43312 | **`swinglens-supervisor`, `idle in transaction`, `ClientRead`** | [] |

Backend PIDs in this table are PostgreSQL connection processes, distinct from the Python OS process IDs above.

Matching supervisor SQL telemetry identifies `background_job_service.py:1040`, `fence_stalled_jobs`, as the locking caller at 11:39:23.903 UTC. Its following metadata flush is not recorded until 11:39:40.812. The monitor also records a **16,916 ms transaction**. See [concurrent-job-locks.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/concurrent-job-locks.json), original supervisor SQL log lines **30454–30458**.

PostgreSQL independently records two errors at local 13:39:27 and 13:39:30:

```text
ERROR: canceling statement due to lock timeout
CONTEXT: while locking tuple (430,2) in relation "background_jobs"
STATEMENT: UPDATE background_jobs SET heartbeat_at=..., lease_expires_at=...
           WHERE id=... AND status IN (...) AND execution_token=...
```

See [postgresql-errors.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/postgresql-errors.json), original server log lines **180–185**. The worker telemetry supplies job 1384's execution context and measured durations; the server log by itself does not print the bound job ID.

**This is cross-session contention with the supervisor.** During the detector interval, the business thread has **zero** recorded `background_jobs ... FOR UPDATE` statements, while **245** autonomous heartbeat updates succeed. The existing nested-savepoint fence guard is therefore not the mechanism that held the offending lock. The actual blocking graph rules out diagnosing this incident as a business-session heartbeat self-lock.

## 4. Code path that permits the failure

1. [fence_stalled_jobs](C:/Users/Ivica/Documents/SwingLens/app/services/background_job_service.py:996) selects all matching `RUNNING` jobs with a progress timestamp, then obtains `FOR UPDATE SKIP LOCKED` locks **before** deciding whether they are stalled. `SKIP LOCKED` skips someone else's existing lock; it does not release this watchdog's newly acquired locks.
2. The healthy branch still assigns `operational_metadata_json["progress_watchdog"]`, logs a decision, and eventually flushes. It retains the row lock through that Python/ORM/logging work and until its caller commits.
3. [_fence_no_progress](C:/Users/Ivica/Documents/SwingLens/app/worker_supervisor.py:1043) commits only after `fence_stalled_jobs` returns. This is the transaction found holding job 1384's lock for about 17 seconds.
4. [The evaluation service](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/evaluation_service.py:264) calls the change detector, then reports `CHANGE_DETECTION_COMPLETED`.
5. [The checkpoint callback](C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_executor.py:3193) calls `should_cancel()` **before** publishing that progress. [The FULL_PIPELINE callback](C:/Users/Ivica/Documents/SwingLens/app/services/background_worker.py:1460) implements that check with a detached control session that first renews the job heartbeat.
6. [control_plane_transaction](C:/Users/Ivica/Documents/SwingLens/app/services/domain_write_fence.py:324) applies a transaction-local 3-second `lock_timeout`. PostgreSQL SQLSTATE `55P03` is translated into `ControlPlaneLockTimeout`.

The failed step thus does not mean native change detection threw an error on a particular ticker. All 937 inserts and the detector savepoint release precede the failing heartbeat. The completion progress record never persists because its preceding heartbeat fails.

The watchdog's own decision is also significant: **`HEALTHY_WITHIN_PROGRESS_TIMEOUT`**, useful-progress age **1,257s**, configured threshold **1,800s**, lease heartbeat age **1s**. It did not find a genuine progress stall or expired lease. See [watchdog-log-window.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/watchdog-log-window.json), supervisor application log lines **265363–265364**.

**Limit of the historical proof:** the logs establish where the supervisor acquired the lock, its idle transaction, the blocked connections, and when it resumed. They do not contain a Python stack sample explaining the entire approximately 17-second client-side gap. Scheduling, runtime pauses, ORM processing or logging delays cannot be distinguished retrospectively. It would be unsupported to label that gap a slow database query, deadlock, CPU spike, or specific logging defect. The code defect demonstrated here is retaining the shared lease row lock across this unbounded client-side work, including healthy observations.

## 5. Independent PostgreSQL reproduction

[reproduce_watchdog_lock.py](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/reproduce_watchdog_lock.py) invokes the real production `fence_stalled_jobs`, `heartbeat_job`, `control_plane_transaction`, and queue execution-authority predicate.

It creates a randomly named disposable database with the relevant model tables, omitting unrelated foreign-key dependencies. It does not clone production data or replay Run 40. The controlled condition is an uncommitted healthy watchdog transaction; the witness does not claim to reproduce the cause of the historical Python pause.

Observed results in [reproduction-results.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/reproduction-results.json):

- Watchdog reports **no fenced jobs**: `[]`.
- A separate observer sees the heartbeat waiting on the watchdog backend, `Lock / transactionid`.
- Heartbeat raises the same **`ControlPlaneLockTimeout` after 3.014s**.
- Commit the watchdog transaction; the identical heartbeat **succeeds**.
- A queued pipeline job is authorized while its parent is active, and **unauthorized** after changing only the parent status to `FAILED`.
- All assertions pass; the disposable database is dropped.

From the repository root:

```powershell
.venv\Scripts\python.exe docs/forensics/run40-lifecycle-failure/reproduce_watchdog_lock.py
.venv\Scripts\python.exe docs/forensics/run40-lifecycle-failure/analyze_logs.py
```

The first command creates and removes a disposable database using the repository's test database safety check. The second rebuilds bounded evidence from existing log files.

## 6. Persisted impact and recovery defects

Read-only database evidence is in [persistence-impact.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/persistence-impact.json).

| Object | Observed state |
|---|---|
| Setup snapshots for Run 40 | **88** |
| Current selections attributed to evaluation 57 | **88** |
| Evaluation 57 canonical audit events | **88**, all `CANONICAL_REVISION` |
| Evaluation 57 signal-change events | **937**, IDs **10913–11849**, across **80 tickers** |
| Change events attached to an episode | **0** |
| Evaluation 57 alerts | **0** |
| Evaluation 57 actual lifecycle transition events | **0**; the 88 canonical revision records above are not the subsequent lifecycle evaluation |
| Winner prediction snapshots for Run 40 | **0**; winner capture remains pending |
| Evaluation 57 summary | `FAILED`, counts JSON only `{"failed":1}` |
| Job 1384 | `QUEUED`, retry count 1, last attempt `RETRYING` |
| Parent pipeline | `FAILED` |
| Claimable pipeline jobs for Run 40 | **[]** |

**Failure reporting commits domain work.** The evaluation's exception handler records `FAILED` and rethrows. [_pipeline_step's generic exception handler](C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_executor.py:2598) marks the step failed and calls [_save_progress](C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_executor.py:3119), which flushes and **commits the same business session**. The detector's successfully released savepoint is still part of that session. Consequently its 937 changes become durable together with failure reporting. The later outer rollback cannot undo that commit. Canonical selection work is also durable; this is not an atomic rollback of the whole lifecycle evaluation.

The current-market signal-change query uses the current snapshot pointer, without requiring the change event's evaluation to be completed. A read-only count using that actual predicate matches **all 937 Run 40 events**. Thus there is a concrete incomplete-publication concern, not merely unused failed-run rows. This does not establish that their financial values are wrong; their downstream lifecycle/alert processing is incomplete.

**Automatic retry is stranded.** The outer pipeline exception handler makes the pipeline terminal `FAILED`; [the worker](C:/Users/Ivica/Documents/SwingLens/app/services/background_worker.py:1104) separately queues a retry for `ControlPlaneLockTimeout`. [The execution-authority predicate](C:/Users/Ivica/Documents/SwingLens/app/services/pipeline_execution_authority.py:73), used by queue claims, excludes terminal failed parents. Direct execution of the read-only claimability helper returns no jobs. Waiting longer or starting another worker does not reconcile these states.

**A new evaluation can conflict with existing change events.** `evaluate_run` creates a new evaluation record on invocation. [signal_change_key](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/repository.py:2275) excludes the evaluation ID, but [signal_change_body](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/change_authority.py:32) includes it. [add_signal_change_event](C:/Users/Ivica/Documents/SwingLens/app/services/setup_lifecycle/repository.py:1754) rejects an existing same-key event if that body differs, with `MUTATION_CHANGE_ALTERED_RETRY`.

A non-persisting witness copied real event **11319** and changed only its evaluation ID. Its source key stayed equal; its comparison body differed only in `evaluation_run_id`. A fresh evaluation reaching that same-key insertion can therefore reject the retry. This is code-and-data proof of a recovery hazard, not a claim that a production replay was attempted. Even when an existing event is accepted, the detector skips it from the returned `event_ids`; recovery must also account for existing events whose alert processing never ran.

All 937 `created_at` values are identical, 13:16:22.652689 CEST, although SQL telemetry proves inserts continued until 13:39:24. This is consistent with the model's database `now()` timestamp representing transaction time. Those row timestamps must not be used as individual execution timestamps.

## 7. Contributing performance and observability findings

The approximately **20m58.5s** detector interval has:

- **937** signal-change inserts and **33,454** SQL records.
- **13,625** SQL records attributed to `_projection_pin`.
- **4,757** attributed to `validate_setup_projection`.
- **6,633** attributed to execution ownership validation.
- Only one bulk history-loader query, so replacing history loading alone will not remove the dominant repetition.

The code discovers source manifests per current snapshot, then `validate_signal_change` revalidates the manifest's projections and reruns native detection for each event. This explains repeated work proportional to changes multiplied by their historical source set. The detector has no item-level progress/cancellation callback; progress resumes only after the entire detector returns.

Recorded cursor execution timers sum to **87.5s**, much less than 1,258.5s wall time. These timers exclude Python calculation, ORM result materialization, proof serialization, instrumentation and scheduling. The evidence proves repeated work and long transaction exposure; a CPU profile is still required to allocate the remaining time. See [detector-sql-statistics.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/detector-sql-statistics.json).

Run 39 is not a comparable scale test: its lifecycle evaluation processed **one snapshot**, reported 13 changes and completed in approximately 35s. Run 40 processed 88 snapshots. Its prior success does not establish the full-universe lifecycle transaction's reliability.

The application formatter currently stores only the final line from `traceback.format_exception`, losing traceback frames and the chained driver exception. The PostgreSQL server configuration has `log_lock_waits=off` and a timestamp-only log prefix. The separate database health sampler fortunately preserved the actual blocking graph. Its `duration_ms` is transaction age when a transaction exists, not necessarily the duration of the displayed last SQL statement; treating those health samples as executed SQL records would produce incorrect performance totals.

## 8. Recommendations, in implementation order

1. **Remove healthy watchdog observations from the leased job row's exclusive-lock path.** Read candidates without locking; determine eligibility first. For a genuinely overdue candidate, acquire a brief lock and revalidate the execution token, status, progress sequence and timestamps before fencing it. Preserve the ownership serialization guarantee. If durable observation state is required, keep it separately or use a short conditional update, rather than rewriting the shared job metadata on every healthy poll. Commit before logging or other client-side work. `SKIP LOCKED` alone does not solve the holder-side problem. Add concurrency coverage for a paused healthy watchdog and an advancing job, as well as true stall/reclaim cases.

2. **Make infrastructure failure and retry state one orchestration decision.** Give `ControlPlaneLockTimeout` an explicit transient infrastructure classification. Propagate it through the evaluator and pipeline step without treating it as an ordinary terminal business failure. Roll back unfinished business work first; persist attempt failure and a consistent interrupted/retry-pending pipeline state separately. Verify that the queued attempt remains claimable, resumes from the intended boundary, and cannot duplicate a continuation. Keep the terminal-parent claim guard intact.

3. **Define an explicit lifecycle publication and recovery contract before resuming Run 40.** Either publish the stage atomically or use durable, resumable phases with clear completion markers and visibility rules. Failure logging must not accidentally commit the domain transaction. Resolve same-key event ownership across evaluation attempts; retain failed-attempt history and prove that existing valid change events receive missing alert/lifecycle processing. Audit the 88 current selections, 88 canonical revision records and 937 changes together. Use supported reconciliation with an audit trail; do not delete evidence or force statuses merely to make a retry run.

4. **Bound and profile change detection.** Profile the 88-symbol workload and repeated proof/ORM costs. Reuse transaction-scoped validation of the exact pinned source/configuration set, without weakening source authority or reusing witnesses across transactions. Add per-symbol or bounded-batch progress and cancellation checkpoints. If introducing commits, design idempotent phase recovery first; detached heartbeats alone do not make a long publication transaction safe. Add a representative 88-symbol/history regression workload and measure query counts and elapsed time.

5. **Improve lock and exception diagnostics.** Preserve redacted traceback frames and chained SQLSTATE. Include database backend PID, transaction age, query duration as a separate field, job/attempt identity, and blocker identity in lock-timeout diagnostics. Move watchdog decision emission after commit. Add alerts for control transactions approaching their lock-hold budget and for queued retries whose parent makes them unclaimable. Consider server lock-wait logging with backend/application identifiers. Record the deployed source artifact and dirty-source fingerprint so a runtime can be reproduced precisely.

Increasing the 3-second timeout would only mask some occurrences of the demonstrated lock-holder defect. Increasing the 30-minute progress timeout would not address this failure: the watchdog already considered the job healthy. CERI/provider repair is not the immediate remedy; ingestion and the handoff had completed successfully before this incident.

## 9. Preservation and evidence inventory

The full JSONB state of Run 40's pipeline, ordered steps and related jobs was hashed before and after investigation. Both hashes are:

```text
684287f36b176a92c770cf633c22237dc02b471e85dbb178193a5866a0b8f3ec
```

The match confirms that this investigation did not change those production records. The synthetic replay identity witness was never persisted. Both reproduction databases used during development were cleaned up; the final successful witness database is named in its results file.

Additional evidence:

- [database-summary.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/database-summary.json): stage/job/dependency state and baseline hash.
- [failure-log-extract.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/failure-log-extract.json): bounded application failure sequence.
- [sql-failure-window.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/sql-failure-window.json): final detector insert/savepoint release, heartbeat failures and failure cleanup, with original file/line references.
- [job-row-lock-sql.json](C:/Users/Ivica/Documents/SwingLens/docs/forensics/run40-lifecycle-failure/job-row-lock-sql.json): broader worker ownership/control-row SQL context.

No application fix, runtime restart, production replay, commit or push was performed as part of this investigation.
