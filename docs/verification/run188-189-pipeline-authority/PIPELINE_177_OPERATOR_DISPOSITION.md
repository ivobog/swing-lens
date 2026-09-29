# Pipeline 177 operator disposition

This document is the append-only operator record for resolving the historical
PipelineRun 177 inconsistency. The certified remediation baseline remains
unchanged. The evidence in **Before disposition** was captured and committed
before any reconciliation transaction was executed.

## Before disposition

Captured on 2026-09-29 in `AUTHORITATIVE_LOCAL` database safety context.

- Certified code SHA: `131a2de199802536bc2095886dd4592c9659d647`
- Branch: `codex/pipeline-authority-remediation`
- Worktree: clean
- Schema: `0087_pipeline_dependencies (head)`
- Canonical runtime state: stopped; recorded SHA matched the certified code
  SHA; database reachable; schema at head; web, core, and worker stopped
- Active background-job blockers: none
- Background-job catalog: 43,134 rows; maximum ID 43,703
- Pipeline 178 control fingerprint:
  `b00c55346fe8aa3a6e2bc14952530a55d19f30d69aa4ad46bb797aa57fdd8303`

### PipelineRun 177 complete row

```json
{
  "id": 177,
  "upload_run_id": 188,
  "status": "RUNNING",
  "current_step": "VALIDATING_RUN",
  "requested_by": null,
  "started_at": "2026-09-29T13:49:01.107429+02:00",
  "completed_at": null,
  "created_at": "2026-09-29T13:48:57.513373+02:00",
  "message": "Pipeline cancellation requested.",
  "error_message": null,
  "result_json": {
    "background_job_id": 43640,
    "bar_readiness_version": "daily-close-plus-15m-v1",
    "ib_host": "127.0.0.1",
    "ib_port": 4002,
    "ib_preflight_checked_at": "2026-09-29T11:48:57.490917+00:00",
    "ib_preflight_status": "IB_API_READY",
    "input_as_of_session": "2026-09-28",
    "market_calculation_context_id": 40,
    "market_calendar_version": "swinglens-us-equities-v1",
    "market_cutoff_at": "2026-09-29T11:48:57.991969Z",
    "market_data_policy": "REQUIRE_IB",
    "transition_evidence_fingerprint": null,
    "transition_preflight_plan_id": null
  },
  "scope_id": "bb7786e21a73109827ff26b3e4fbb5df743285cc1128073b17e030797f876361",
  "refresh_cycle_id": "210b79053b49166c8779938b441517ec8fb8a35ad26b96cb9a6987d4bef35d00",
  "acquisition_plan_id": "80508eec84d4fb2bc9c89207734ebb680817974c003dba441bde1fa6928c678f"
}
```

Fingerprint:
`7b956b3e279c1540165ff116ed9971f028d1118604e72138d960d0e93cad95e5`

### Root BackgroundJob 43640 complete row

```json
{
  "id": 43640,
  "job_type": "FULL_PIPELINE",
  "related_run_id": 188,
  "status": "CANCELLED",
  "priority": 100,
  "payload_json": {
    "acquisition_plan_id": "80508eec84d4fb2bc9c89207734ebb680817974c003dba441bde1fa6928c678f",
    "bar_readiness_version": "daily-close-plus-15m-v1",
    "effective_configuration_anchor": {
      "anchor_id": "a5545b070d3667a18fc08d558a774b6879614167d3af0298cecb9c51a2a9df70",
      "fingerprint": "d6afbb089cecf6908bcb386cf5ef74f2f4b8f6119f994891ff625693e7d68a63"
    },
    "input_as_of_session": "2026-09-28",
    "market_calculation_context_id": 40,
    "market_calendar_version": "swinglens-us-equities-v1",
    "market_cutoff_at": "2026-09-29T11:48:57.991969Z",
    "pipeline_run_id": 177,
    "refresh_cycle_id": "210b79053b49166c8779938b441517ec8fb8a35ad26b96cb9a6987d4bef35d00",
    "scope_id": "bb7786e21a73109827ff26b3e4fbb5df743285cc1128073b17e030797f876361",
    "transition_evidence_fingerprint": null,
    "transition_preflight_plan_id": null
  },
  "result_json": null,
  "error_message": null,
  "retry_count": 0,
  "max_retries": 3,
  "requested_cancel": true,
  "worker_id": null,
  "locked_at": null,
  "run_after": "2026-09-29T13:48:59.441856+02:00",
  "created_at": "2026-09-29T13:48:57.513373+02:00",
  "started_at": "2026-09-29T13:48:59.675957+02:00",
  "completed_at": "2026-09-29T14:04:45.104285+02:00",
  "lease_owner": null,
  "execution_token": null,
  "heartbeat_at": null,
  "lease_expires_at": null,
  "operational_metadata_json": {
    "attempt_count": 1,
    "last_attempt": {
      "attempt_number": 1,
      "execution_duration_ms": 945428.328,
      "finished_at": "2026-09-29T12:04:45.104285+00:00",
      "original_queue_delay_ms": 2162.584,
      "queue_delay_ms": 234.101,
      "retry_count_at_start": 0,
      "started_at": "2026-09-29T11:48:59.675957+00:00",
      "status": "CANCELLED"
    },
    "lease_events": [{
      "event_type": "CLAIMED",
      "execution_token_hash": "<restricted:execution_token_hash>",
      "execution_token_suffix": null,
      "occurred_at": "2026-09-29T11:48:59.675957+00:00",
      "worker_id": "local-worker-1"
    }],
    "progress_watchdog": {
      "observed_at": "2026-09-29T11:48:59.675957+00:00",
      "progress_sequence": 1,
      "unchanged_since": "2026-09-29T11:48:59.675957+00:00"
    }
  },
  "request_key": "full-pipeline:run:188:policy:REQUIRE_IB:steps:VALIDATING_RUN,SCORING_FUNDAMENTALS,FETCHING_MARKET_DATA,SCORING_TECHNICALS,MARKET_REGIME_SNAPSHOT,COMBINING_RESULTS,RANKING_PROFILES,SECTOR_ROTATION_SNAPSHOT,CERI_PROVIDER_INGEST,FREEZING_DECISION_HANDOFF_MANIFEST,CAPTURING_SETUP_SIGNALS,EVALUATING_SETUP_LIFECYCLES,CAPTURING_WINNER_PREDICTIONS",
  "workflow_key": null,
  "last_progress_at": "2026-09-29T13:49:01.187462+02:00",
  "progress_sequence": 2,
  "progress_stage": "VALIDATING_RUN",
  "progress_current_item": null,
  "progress_last_completed_item": null,
  "progress_processed": 0,
  "progress_total": null,
  "checkpoint_version": "job-progress-v1",
  "stall_detected_at": null,
  "recovery_count": 0,
  "worker_instance_id": null,
  "root_job_id": null,
  "parent_job_id": null,
  "continuation_depth": null,
  "trigger_source": null,
  "root_correlation_id": "root-3cca1706f09c49249faabdf85597e3b6",
  "causation_id": "cause-643ffb4b3b1e49b7b5fc0f9ab23af44a",
  "trigger_kind": "HTTP",
  "trigger_name": "POST /runs/188/pipeline",
  "triggered_by_request_id": "request-2e6a1069c04d4e3e8aeac288125de696",
  "fanout_group_id": null,
  "coalesced_into_job_id": null,
  "triggered_by_job_id": null,
  "scope_id": "bb7786e21a73109827ff26b3e4fbb5df743285cc1128073b17e030797f876361",
  "refresh_cycle_id": "210b79053b49166c8779938b441517ec8fb8a35ad26b96cb9a6987d4bef35d00",
  "acquisition_plan_id": "80508eec84d4fb2bc9c89207734ebb680817974c003dba441bde1fa6928c678f",
  "required_for_parent_completion": false,
  "pipeline_dependency_id": null
}
```

Fingerprint:
`c0c091fc68ac81081184976db3b255b9886897454590410ffcaea104bff877a8`

### Pipeline steps 1647-1659

All 13 rows had `pipeline_run_id=177`, `status=PENDING`, `completed_at=null`,
`error_message=null`, `retry_count=0`, and `result_json=null`.

| ID | Order | Step | Started at | Message |
|---:|---:|---|---|---|
| 1647 | 1 | `VALIDATING_RUN` | `2026-09-29T13:49:01.179462+02:00` | `Waiting for automatic SEC preparation.` |
| 1648 | 2 | `SCORING_FUNDAMENTALS` | null | null |
| 1649 | 3 | `FETCHING_MARKET_DATA` | null | null |
| 1650 | 4 | `SCORING_TECHNICALS` | null | null |
| 1651 | 5 | `MARKET_REGIME_SNAPSHOT` | null | null |
| 1652 | 6 | `COMBINING_RESULTS` | null | null |
| 1653 | 7 | `RANKING_PROFILES` | null | null |
| 1654 | 8 | `SECTOR_ROTATION_SNAPSHOT` | null | null |
| 1655 | 9 | `CERI_PROVIDER_INGEST` | null | null |
| 1656 | 10 | `FREEZING_DECISION_HANDOFF_MANIFEST` | null | null |
| 1657 | 11 | `CAPTURING_SETUP_SIGNALS` | null | null |
| 1658 | 12 | `EVALUATING_SETUP_LIFECYCLES` | null | null |
| 1659 | 13 | `CAPTURING_WINNER_PREDICTIONS` | null | null |

Steps fingerprint:
`5cc07d1e29e4501228f1ca7c1f42edcc159a1cd951778da0b0115584ac86448c`

### Dependency, child, and continuation evidence

- `pipeline_dependencies` rows for Pipeline 177: none
- Other `background_jobs` rows with Pipeline 177 payload, root 43640, or parent
  43640: none
- Dependencies fingerprint:
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
- Related-jobs fingerprint (the root row only):
  `c0c091fc68ac81081184976db3b255b9886897454590410ffcaea104bff877a8`
- Complete Pipeline 177 evidence fingerprint:
  `33deed8d545a6484c0692980b5a500170d727f5f36c3c6060e0f16420a2e1f94`

There is no general pipeline transition-history table. Historical execution
evidence is retained on the root job in timestamps and
`operational_metadata_json`; the disposition transaction must not modify it.

### Read-only invariant report

Counts before disposition:

- `SAFE_AUTO_RECONCILE`: 0
- `REQUIRES_OPERATOR_REVIEW`: 17
- `FATAL_STARTUP_INVARIANT`: 0

Pipeline 177 finding:

```text
ACTIVE_PIPELINE_WITH_TERMINAL_ROOT_JOB
pipeline_id=177 job_id=43640
detail=pipeline=RUNNING;root=CANCELLED
disposition=REQUIRES_OPERATOR_REVIEW
```

The other 16 findings predate and are unrelated to this disposition:

- Pipelines 71, 72, 73, 74, 96, and 151: one
  `ACTIVE_PIPELINE_WITH_TERMINAL_ROOT_JOB` finding each.
- Pipelines 158, 166, 167, 168, and 169: one
  `ACTIVE_PIPELINE_WITH_TERMINAL_ROOT_JOB` plus one
  `WAITING_PIPELINE_DEPENDENCY_CARDINALITY_INVALID` finding each.

They are preserved as explicit remaining operator-review items and are outside
the authorized Pipeline 177 mutation scope.

## Operator decision

The authoritative root job is terminal `CANCELLED`, has
`requested_cancel=true`, records a cancelled attempt, has no live dependency
handoff, and Pipeline 177 still records `Pipeline cancellation requested.`.
The only state consistent with that retained evidence is terminal
`CANCELLED`.

The authorized mechanism is the production orchestrator entry point
`reconcile_pipeline_job(session, 43640)`. Its existing terminal-root branch
locks Pipeline 177, calls the pipeline state machine as actor
`pipeline_orchestrator`, and cancels its incomplete steps. It does not execute
pipeline steps, enqueue children or continuations, or invoke providers.

## After disposition

The before-evidence above was committed as
`ed5d2907eb3edb6b867c67af5f0fae9201fc4a5b` before this transaction.

The operator invoked
`app.services.pipeline_dependency_service.reconcile_pipeline_job(session,
43640)` with the canonical runtime stopped. Preconditions asserted in the same
transaction were: Pipeline 177 `RUNNING`; root job 43640 `CANCELLED` with
`requested_cancel=true`; payload `pipeline_run_id=177`; zero Pipeline 177
dependencies; and zero active background jobs.

### PipelineRun 177 complete row

```json
{
  "id": 177,
  "upload_run_id": 188,
  "status": "CANCELLED",
  "current_step": "VALIDATING_RUN",
  "requested_by": null,
  "started_at": "2026-09-29T13:49:01.107429+02:00",
  "completed_at": "2026-09-29T20:12:09.446157+02:00",
  "created_at": "2026-09-29T13:48:57.513373+02:00",
  "message": "Pipeline cancellation completed.",
  "error_message": null,
  "result_json": {
    "background_job_id": 43640,
    "bar_readiness_version": "daily-close-plus-15m-v1",
    "ib_host": "127.0.0.1",
    "ib_port": 4002,
    "ib_preflight_checked_at": "2026-09-29T11:48:57.490917+00:00",
    "ib_preflight_status": "IB_API_READY",
    "input_as_of_session": "2026-09-28",
    "market_calculation_context_id": 40,
    "market_calendar_version": "swinglens-us-equities-v1",
    "market_cutoff_at": "2026-09-29T11:48:57.991969Z",
    "market_data_policy": "REQUIRE_IB",
    "transition_evidence_fingerprint": null,
    "transition_preflight_plan_id": null
  },
  "scope_id": "bb7786e21a73109827ff26b3e4fbb5df743285cc1128073b17e030797f876361",
  "refresh_cycle_id": "210b79053b49166c8779938b441517ec8fb8a35ad26b96cb9a6987d4bef35d00",
  "acquisition_plan_id": "80508eec84d4fb2bc9c89207734ebb680817974c003dba441bde1fa6928c678f"
}
```

Post-disposition fingerprint:
`ebeb277d501fc5b6c69dad677e3e2e4162f3840017b53ec046ea2db92a53919c`

Only `status`, `completed_at`, and `message` changed on this row. The start and
creation timestamps and every retained result/context field were preserved.

### Root job, steps, dependencies, and continuations

Root job 43640 remained field-for-field identical to the complete before row.
Its fingerprint remained
`c0c091fc68ac81081184976db3b255b9886897454590410ffcaea104bff877a8`.
Its execution timestamps, cancelled attempt evidence, lease history, progress,
payload, and cancellation fields were not rewritten.

Every Pipeline 177 step transitioned from `PENDING` to `CANCELLED`, received
the message `Pipeline cancelled.`, and received the following completion time.
All other step fields were preserved, including step 1647's original
`started_at` and all null/non-null `result_json`, `error_message`, and retry
fields.

| ID | Completion time |
|---:|---|
| 1647 | `2026-09-29T20:12:09.529308+02:00` |
| 1648 | `2026-09-29T20:12:09.558523+02:00` |
| 1649 | `2026-09-29T20:12:09.561038+02:00` |
| 1650 | `2026-09-29T20:12:09.562561+02:00` |
| 1651 | `2026-09-29T20:12:09.564562+02:00` |
| 1652 | `2026-09-29T20:12:09.565561+02:00` |
| 1653 | `2026-09-29T20:12:09.566562+02:00` |
| 1654 | `2026-09-29T20:12:09.567561+02:00` |
| 1655 | `2026-09-29T20:12:09.570565+02:00` |
| 1656 | `2026-09-29T20:12:09.572562+02:00` |
| 1657 | `2026-09-29T20:12:09.573563+02:00` |
| 1658 | `2026-09-29T20:12:09.575563+02:00` |
| 1659 | `2026-09-29T20:12:09.576562+02:00` |

Post-disposition steps fingerprint:
`333c240c79d2a6f220a91881bf8452c6a11f72b9dc65256ef904101577255282`.

- Dependencies remained empty; fingerprint unchanged at
  `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`.
- Related jobs remained exactly `[43640]`; there is no child or continuation.
- Related-jobs fingerprint remained
  `c0c091fc68ac81081184976db3b255b9886897454590410ffcaea104bff877a8`.
- The background-job catalog remained 43,134 rows with maximum ID 43,703.
  No background job was created or modified.
- Pipeline 178's control fingerprint remained
  `b00c55346fe8aa3a6e2bc14952530a55d19f30d69aa4ad46bb797aa57fdd8303`.
- Complete post-disposition Pipeline 177 evidence fingerprint:
  `dca209acb6f19b21c468d7aa5c1b238e3dcc5a915adae6a95888a713e9678e23`.

### Mutation boundary

SQLAlchemy's `before_cursor_execute` event captured every DML statement in the
operator transaction. It recorded exactly:

- One `UPDATE pipeline_runs ... WHERE pipeline_runs.id = 177`, setting only
  `status`, `completed_at`, and `message`.
- Thirteen `UPDATE pipeline_steps ... WHERE pipeline_steps.id = ...`, for IDs
  1647 through 1659, setting only `status`, `completed_at`, and `message`.
- No `INSERT`, no `DELETE`, no `background_jobs` update, no dependency update,
  and no unrelated-table write.

Because the runtime was stopped, the transaction had no worker race and could
not execute a pipeline step or call a provider. The unchanged job catalog,
empty dependency set, and DML capture independently confirm that it did not
enqueue work.

### Post-disposition invariants

Counts after disposition:

- `SAFE_AUTO_RECONCILE`: 0
- `REQUIRES_OPERATOR_REVIEW`: 16
- `FATAL_STARTUP_INVARIANT`: 0

Pipeline 177 has no finding. The count decreased by exactly one: the removed
finding is the before-recorded Pipeline 177
`ACTIVE_PIPELINE_WITH_TERMINAL_ROOT_JOB`. The 16 unrelated historical findings
listed above are unchanged and remain outside this disposition's scope. Active
background-job blockers remain empty.

### Focused verification

- `alembic current`: `0087_pipeline_dependencies (head)`
- Unit cancellation convergence: 2 passed
- Disposable PostgreSQL terminal-pipeline/root reconciliation: 2 passed
- No production source was changed for this disposition; the existing
  orchestrator/state-machine mechanism behaved as designed.
