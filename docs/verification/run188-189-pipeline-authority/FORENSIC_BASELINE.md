# Runs 188 and 189 forensic baseline

Captured on 2026-09-29 between 14:56 and 15:15 Europe/Zurich, before any production-code change.

`FORENSIC STATE CAPTURED: YES`

## Repository and runtime identity

- Baseline branch: `main`
- Baseline SHA: `e5217fdee706fa1fe7f1e72ddcee0546f56ebc2c`
- Baseline worktree: clean (`## main...origin/main`)
- Remediation branch created after capture: `codex/pipeline-authority-remediation`
- Deployed web, worker, and supervisor SHA from lifecycle and SQL telemetry: `e5217fdee706fa1fe7f1e72ddcee0546f56ebc2c`
- Deployment identity: `local-development`, application version `0.1.0`
- PostgreSQL: `PostgreSQL 18.3 on x86_64-windows, compiled by msvc-19.44.35223, 64-bit`
- Database: `swinglens`, user `postgres`
- Migration current/head: `0086_ceri_feature_source_manifest`
- PostgreSQL lock/statement/idle-in-transaction timeouts at capture: all `0` (unbounded); `deadlock_timeout=1s`
- Runtime worker: `local-worker-1`, generation `120`, instance `369d47c0064c45269c9ceb64f48b5a8c`, process `8548`
- Runtime supervisor process: `19976`

No production row, process, transaction, or log was changed while making this capture. Run 189 was not terminated because the complete live blocking chain remained available for inspection.

## Durable incident rows

### Run 188 / pipeline 177 / root job 43640

- Upload run 188: `COMPLETED`, 155 rows.
- Pipeline 177: `RUNNING`, current step `VALIDATING_RUN`, message `Pipeline cancellation requested.`, no completion time.
- Validation step 1647: `PENDING`, message `Waiting for automatic SEC preparation.`, no completion time.
- Remaining twelve pipeline steps: `PENDING`.
- Root job 43640: `CANCELLED`, `requested_cancel=true`, completed at `2026-09-29T14:04:45.104285+02:00`, no worker/lease/execution token remains.
- Root job progress stopped at sequence 2, stage `VALIDATING_RUN`, last progress `2026-09-29T13:49:01.187462+02:00`.
- No durable `SEC_READINESS_REPAIR` child or continuation exists for run 188/pipeline 177.
- Enqueue history contains only root-job attempt 1184.
- This is the preserved invariant violation: a terminal root job with an indefinitely active pipeline.

### Run 189 / pipeline 178 / root job 43642

- Upload run 189: `COMPLETED`, 111 rows.
- Pipeline 178, as visible to other sessions: `RUNNING`, current step `VALIDATING_RUN`, message `Full pipeline is running.`, no completion time.
- Validation step 1660: `PENDING`, message `Waiting for automatic SEC preparation.`, no completion time.
- Remaining twelve pipeline steps: `PENDING`.
- Root job 43642: `RUNNING`, `requested_cancel=false`, worker `local-worker-1`, worker instance `369d47c0064c45269c9ceb64f48b5a8c`, execution lease last durably heartbeated at `2026-09-29T14:12:43.669471+02:00`, lease expiry `2026-09-29T14:27:43.669471+02:00`.
- Root job progress stopped at sequence 2, stage `VALIDATING_RUN`, last progress `2026-09-29T14:12:43.476427+02:00`.
- No `SEC_READINESS_REPAIR` child or continuation is durably visible.
- Enqueue history contains only root-job attempt 1186.
- Sequence state proves an uncommitted enqueue exists in the blocked transaction: `background_jobs_id_seq=43643` while durable `max(id)=43642`; `background_job_enqueue_attempts_id_seq=1187` while durable `max(id)=1186`.

## Live PostgreSQL lock graph

Captured at `2026-09-29T14:57:02.197204+02:00`:

1. PID 14364, application `swinglens-worker`, transaction 4717508, transaction start `14:12:43.733375+02:00`, was `idle in transaction`. Its last statement was the pipeline-178 update issued by `schedule_sec_readiness_repair`:

   `UPDATE pipeline_runs SET status=?, message=?, result_json=? WHERE pipeline_runs.id=?`

   This transaction had already inserted the uncommitted child job 43643 and enqueue attempt 1187, updated the child lineage, and updated pipeline 178. The child foreign-key lineage retained a key-share lock on root job 43642.

2. PID 3516, application `swinglens-worker`, transaction start `14:12:43.819357+02:00`, waited on transaction 4717508 held by PID 14364. Its exact statement was:

   `SELECT background_jobs.status, background_jobs.execution_token, background_jobs.job_type, background_jobs.related_run_id, background_jobs.payload_json FROM background_jobs WHERE background_jobs.id = $1::BIGINT FOR UPDATE`

   Application SQL telemetry identifies this statement as `domain_write_fence.assert_current_execution_ownership`, line 218. It ran during commit of the separate heartbeat/control session after that session had successfully updated root-job heartbeat/lease fields and worker heartbeat fields.

3. PID 19944, application `swinglens-worker`, waited behind PID 3516 while updating `background_workers` resource telemetry.

4. PID 9224, application `swinglens-supervisor`, waited behind PID 19944 while updating the same worker heartbeat row.

Thus Run 189 remained actively blocked at capture. The chain was 14364 -> 3516 -> 19944 -> 9224.

## Exact observed SQL and application path

The worker SQL telemetry file `logs/db-monitor/worker/sql-2026-09-29-p8548.jsonl` records:

- 12:12:43.515-43.649 UTC: processor lifecycle and SEC readiness queries.
- 12:12:43.728 UTC: validation step persisted as waiting.
- 12:12:43.730 UTC: the main session completed its normal commit-time job ownership fence.
- 12:12:43.734 UTC: the next pipeline transaction began.
- 12:12:43.738-43.759 UTC: child-job idempotency, lineage, and configuration queries.
- 12:12:43.770 UTC: pipeline result update.
- 12:12:43.773 UTC: enqueue savepoint.
- 12:12:43.778 UTC: `SEC_READINESS_REPAIR` child insert (allocated job 43643).
- 12:12:43.779 UTC: enqueue savepoint released.
- 12:12:43.781-43.809 UTC: fanout/lineage and enqueue-attempt writes (allocated attempt 1187).
- 12:12:43.801 UTC: child semantic-authority update.
- 12:12:43.816 UTC: pipeline 178 updated to the intended preparation state, still uncommitted.
- 12:12:43.820 UTC: separate heartbeat session successfully updated root job 43642 using a non-key update.
- 12:12:43.839 UTC: separate heartbeat session flushed its worker update.
- The next commit-hook query never completed and therefore has no completed SQL-telemetry record; `pg_stat_activity` shows it waiting as the `FOR UPDATE` above.

Observed call graph:

`run_worker_once`

-> `execute_job` establishes `fence_domain_commits(job_id=43642, token=...)`

-> `_execute_full_pipeline_job`

-> `execute_full_pipeline`

-> `_pipeline_step(VALIDATING_RUN)` detects `CeriBootstrapRequiredError`

-> `_schedule_sec_prerequisite_repair`

-> `schedule_sec_readiness_repair`

-> `enqueue_job` inserts child 43643 and its root/parent/trigger lineage inside the main pipeline transaction

-> `_save_progress`

-> `lease_guard`

-> worker `heartbeat()` opens a second session

-> `heartbeat_job` + `heartbeat_worker`

-> second-session `commit()`

-> global `Session.before_commit` listener `_fence_active_domain_commit`

-> inherited job ownership context forces `assert_current_execution_ownership(... FOR UPDATE)`

-> waits on the main session's root-job key-share lock.

## SEC readiness evidence

The active/deployed processor signature was `sec-guidance:eed017654682a0c9`; it was certified and active.

Run 188 readiness was 148/155. Blockers:

- `IDT`, `IOSP`, `KLAC`, `MAR`, `PEB`: `UNRESOLVED_MAPPING`
- `IPAR`: `CIK_MISSING` (company row 344 exists, required SEC applicability, null CIK)
- `JBL`: `SIGNATURE_MISMATCH`; company row 593 maps to CIK `0000898293`, whose persisted guidance sync is only for retired signature `sec-guidance:948beb114caa8da9` (118 documents: 80 no-record and 38 with-record completed extractions)

Run 189 readiness was 107/111. Blockers:

- `ARM`, `FIVN`, `KLAC`: `UNRESOLVED_MAPPING`
- `JBL`: the same `SIGNATURE_MISMATCH`

This proves validation was correct and the SEC service was not the blocking component. The failure occurred during durable handoff after readiness diagnosis.

## Runtime and supervisor evidence

- Worker lifecycle log records `pipeline.stage_finished` with status `PREPARING` for pipeline 177 at `11:49:01.296488Z` and pipeline 178 at `12:12:43.734209Z`.
- Supervisor log immediately began reporting `LOCKED_CANDIDATE_SKIPPED` for root job 43640 and later root job 43642.
- For Run 189, supervisor considered the worker heartbeat lost at `12:13:17.906228Z`, even though the worker process remained alive and self-blocked.
- Worker and supervisor database telemetry subsequently formed the secondary wait chain described above.
- Logs were not edited, rotated, deleted, or rewritten.

## Last known successful comparison

Run 170 / pipeline 159 at deployed SHA `6e1f842754a939306201b3d01bff1333f291e8f4` durably created root job 43416, repair job 43417, and continuation job 43418. SEC repair completed 112/112 and explicitly resumed the same pipeline. The later pipeline failure occurred downstream and is unrelated to the SEC handoff.

## Disposition at the forensic gate

- Run 188 remains unchanged: root `CANCELLED`, pipeline `RUNNING`.
- Run 189 remains unchanged and actively blocked.
- No reconciliation has been performed.
- No provider call or new pipeline run has been made.
- No incident process or PostgreSQL backend has been terminated.
- Production-code implementation is not authorized to begin until the real-PostgreSQL reproducer fails for this same structural reason and the regression range is bisected.
