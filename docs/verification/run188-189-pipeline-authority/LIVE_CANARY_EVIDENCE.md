# Live pipeline-authority canary evidence

## Scope

These canaries used the canonical PostgreSQL runtime and real worker/provider paths after the
disposable PostgreSQL certification lane. `UploadRun` IDs below are new live-canary rows; they are
not the original historical Upload Runs 188 and 189 described in `FORENSIC_BASELINE.md`.

## SEC dependency canaries A-C

| Canary | Upload / pipeline / root | Durable lineage | Observed result |
|---|---|---|---|
| A, preliminary | 190 / 179 / 43646 (`DELL`) | dependency 2, child 43647, continuation 43648 | Same pipeline resumed, bounded cancellation converged, zero observed blockers. The first sampling interval missed the transient wait state, so this was not used alone as proof. |
| A, formal | 191 / 180 / 43649 (`IBM`) | dependency 3, child 43650, continuation 43651 | Observer captured `WAITING_DEPENDENCY`, `PENDING_ENQUEUE`, child execution, and same-pipeline continuation. Cancellation returned in 0.063 s and converged to `CANCELLED`; zero blockers. |
| B | 192 / 181 / 43652 (`GOOGL`) | dependency 4, child 43653, no continuation | Cancellation during `WAITING_DEPENDENCY` returned in 0.047 s. Pipeline and relevant jobs became terminal; no late continuation and zero blockers. |
| C1 | 193 / 182 / 43654 (`NFLX`) | dependency 5, child 43655, continuation 43657 | Ran simultaneously with C2, resumed the same pipeline, then cancelled cleanly; zero blockers. |
| C2 | 194 / 183 / 43656 (`INTC`) | dependency 6, child 43658, continuation 43659 | Ran simultaneously with C1, resumed the same pipeline, then cancelled cleanly; zero blockers. |

The formal A observation proves the changed SEC branch was exercised rather than bypassed. C1/C2
are the two-pipeline concurrency analogue of the historical 188/189 incident.

## Larger canary progression

The larger canary was intentionally repeated as each newly reached authority boundary exposed a
distinct defect. These failed attempts are retained as evidence, not omitted from certification.

| Attempt | Upload / pipeline / root | Result and remediation |
|---|---|---|
| D1 | 195 / 184 / 43660 | Reached market-data items and exposed an unbound per-item Session, producing lease-loss/recovery churn. Cancelled safely. `_bounded_item_session` now binds the same execution attempt explicitly; PostgreSQL scenario 21 covers it. |
| D2 | 196 / 185 / 43661 | Reached fundamentals and reproduced a second-session self-block caused by an early root ownership lock. Main backend 18088 held the row; heartbeat backend 20304 waited. After exact verification, `SELECT pg_cancel_backend(20304)` cancelled that waiting query (the backend was not terminated). The pipeline/root then converged through normal failure/cancellation handling. `deferred_execution_ownership_lock` now acquires `FOR UPDATE` only at authoritative commit; scenarios 21-22 cover the boundary. |
| D3 | 197 / 186 / 43662 | Crossed the repaired transaction boundaries with zero blockers/retries. Ended `FAILED` because nine Interactive Brokers `ADJUSTED_LAST` requests returned `UNCLASSIFIED`; this provider outcome was unrelated to authority or locking. |
| D4 | 198 / 187 / 43663 | Crossed all upstream stages and scheduled the real CERI DAG with zero blockers/retries. It exposed that CERI was still an unmodeled async handoff: the root completed while the pipeline waited, so the invariant correctly failed the pipeline before children could finish. `CERI_WORKFLOW` is now a durable dependency whose barrier owns completion and continuation binding. |
| D5 | 199 / 188 / 43674 | Proved the durable CERI handoff and continuation, then exposed the same implicit-Session authority defect in winner capture. Terminal evidence is recorded below. |
| D6 | 200 / 189 / 43689 | Final 10-symbol run after the generic bounded-domain-Session repair. It exposed and then certified the last generic durable-handler ownership lock. Terminal evidence is recorded below. |

## D5 durable CERI evidence

Universe: `LLY WST NVDA AME LECO JNJ KN NVT VRTX CRM`.

- Validation through CERI scheduling completed with every recorded pipeline-step retry count equal
  to zero.
- The orchestrator persisted dependency 7 (`CERI_WORKFLOW`, `RUNNING`) and pipeline 188 entered
  `WAITING_FOR_CERI_COMPLETION` before root 43674 became `COMPLETED`.
- Jobs 43675-43682 (provider ingest/normalization), 43683 (feature batch), and 43684 (run finalize)
  completed at retry 0. Capture child 43685 was created by the finalized workflow.
- The initial 1,200-second observer timed out during the still-live capture child with
  `maximum_blocked_sessions: 0`; observation continued rather than misclassifying timeout as a
  pipeline result.
- Capture 43685, change detection 43686, and alert rebuild 43687 completed at retry 0. The barrier
  atomically completed dependency 7, bound child 43687 and exactly one continuation 43688, and the
  same pipeline resumed at `FREEZING_DECISION_HANDOFF_MANIFEST`.
- Continuation 43688 completed the manifest, setup-signal, and lifecycle stages, then winner capture
  created an unbound per-ticker Session. The commit fence rejected it with `JOB_LEASE_LOST`, exactly
  as designed; retry 1 then failed closed on `DECISION_MANIFEST_MISMATCH`. Pipeline 188 and job
  43688 ended `FAILED`, not orphaned or running.
- A concurrent disposable-database lock-timeout test briefly appeared in the observer's original
  server-wide blocker query. The harness was corrected to filter `pg_stat_activity.datname` to
  `current_database()`; a fresh application-database observation completed with
  `maximum_blocked_sessions: 0`.
- `bounded_domain_session` now provides one reusable, explicit attempt-bound transaction boundary
  for every per-item domain Session. Both IB fetch and winner capture use it. The exact compatibility
  regression, focused lane, PostgreSQL authority lane, and winner recovery lane passed before D6.

## D6 final evidence

Universe: `LLY WST NVDA AME LECO JNJ KN NVT VRTX CRM`.

- Launched as UploadRun 200, PipelineRun 189, root job 43689 on worker generation 126.
- The certification-only generation did not claim the ordinary queued job. It was quiesced with
  zero active jobs; normal generation 127 then claimed the unchanged root. No row was edited to
  force execution.
- Validation through CERI scheduling completed at retry 0. Dependency 8 (`CERI_WORKFLOW`) kept
  pipeline 189 in `WAITING_FOR_CERI_COMPLETION` while root 43689 completed. Provider/normalize jobs
  43690-43697, feature 43698, finalizer 43699, and capture 43700 all completed at retry 0.
- Change detection 43701 exposed an unconditional `decision_authority` `FOR UPDATE` on its own job
  row. Its detached heartbeat waited on that business transaction. The first edge was waiter 20676
  behind owner 12428; after normal retry, the second edge was waiter 4484 behind owner 20676. Only
  the two waiting queries were cancelled with `pg_cancel_backend`; no backend was terminated. The
  worker rolled the job back and durably requeued it, reaching retry count 2.
- The runtime was safely quiesced with active count zero. `831ace9` moved deferred ownership locking
  to the generic durable-handler boundary and replaced decision authority's unconditional row lock
  with the shared execution fence. PostgreSQL scenario 24 recreated the open business transaction
  plus detached heartbeat under a 500 ms lock timeout and passed.
- Worker generation 128 claimed the same queued job without state editing. Change detection 43701
  completed, alert 43702 completed at retry 0, and the barrier atomically completed dependency 8,
  bound child 43702, and created exactly one continuation 43703.
- Continuation 43703 completed `FREEZING_DECISION_HANDOFF_MANIFEST`,
  `CAPTURING_SETUP_SIGNALS`, `EVALUATING_SETUP_LIFECYCLES`, and
  `CAPTURING_WINNER_PREDICTIONS`, all at retry 0. This exercised both bounded per-item Session paths.
- Pipeline 189 ended `PARTIAL`, a valid business outcome: 10 combined rows, 5 incomplete rows, zero
  IB failures; winner evidence reported 5 inserted, 5 excluded, zero failed. Root 43689 and
  continuation 43703 are `COMPLETED`; dependency 8 is `COMPLETED`.
- The post-fix application-database observation stream contained no blocker edge. The final observer
  reported `continued_pipeline_ids: [189]`, no cancellations, and
  `maximum_blocked_sessions: 0`.
