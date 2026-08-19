# Winner cohort job 30812 incident: pre-mutation forensic snapshot

Captured on 2026-08-19 between 15:14:56 and 15:16 Europe/Zurich. All database reads in this document preceded the cancellation request. Secret execution-token material is intentionally not copied; only presence and the repository's existing redacted hash/suffix audit fields are recorded.

## Executive finding before mutation

The live execution is a stale, pre-remediation worker process. `local-worker-1` started at 2026-08-18 19:49:30+02:00. Job 30812 was first claimed two seconds later and its second attempt was claimed at 2026-08-19 12:42:04+02:00. Commit `2438e6e17979dbfec4b41189fcba2e7a14570b87` (`Harden Winner job reliability`) was committed at 2026-08-19 12:42:20+02:00, 16 seconds after attempt 2 began. Therefore the live Python process could not have imported that remediation.

The database migration version is already `0049_winner_jobs_reliability`, but there are zero rows in both `winner_cohort_refresh_state` and `winner_cohort_generations`. Both processing attempts have the legacy shape: null `attempt_no`, null `cohort_generation_id`, empty counts/checkpoint, and status `RUNNING`. PostgreSQL showed the worker backend repeatedly selecting `winner_prediction_snapshots`. The registered Python PID had consumed 55,527.58 CPU seconds and held 866,684,928 bytes working set / 915,996,672 bytes private memory.

## Background job 30812

```json
{
  "id": 30812,
  "job_type": "WINNER_COHORT_REFRESH",
  "status": "RUNNING",
  "retry_count": 1,
  "max_retries": 3,
  "requested_cancel": false,
  "request_key": "winner:cohort-refresh:T2_5_S2_0_H5_NEXT_OPEN",
  "workflow_key": null,
  "priority": 100,
  "created_at": "2026-08-18 00:08:04.376390+02:00",
  "started_at": "2026-08-18 19:49:32.103957+02:00",
  "run_after": "2026-08-19 12:40:09.923502+02:00",
  "worker_id": "local-worker-1",
  "lease_owner": "local-worker-1",
  "heartbeat_at": "2026-08-19 15:14:56.889482+02:00",
  "lease_expires_at": "2026-08-19 15:29:56.889482+02:00",
  "locked_at": "2026-08-19 12:42:04.171953+02:00",
  "completed_at": null,
  "payload_json": {
    "outcome_definition_id": "T2_5_S2_0_H5_NEXT_OPEN",
    "training_cutoff_at": "2026-08-17T22:08:04.604525+00:00"
  },
  "result_json": null,
  "error_message": null,
  "execution_token_present": true,
  "operational_metadata_json": {
    "attempt_count": 2,
    "current_attempt": {
      "attempt_number": 2,
      "original_queue_delay_ms": 131639795.563,
      "queue_delay_ms": 114248.451,
      "retry_count_at_start": 1,
      "started_at": "2026-08-19T10:42:04.171953+00:00"
    },
    "last_attempt": {
      "attempt_number": 1,
      "execution_duration_ms": 60577819.545,
      "finished_at": "2026-08-19T10:39:09.923502+00:00",
      "original_queue_delay_ms": 70887727.567,
      "queue_delay_ms": 70887462.431,
      "retry_count_at_start": 0,
      "started_at": "2026-08-18T17:49:32.103957+00:00",
      "status": "RETRYING"
    },
    "lease_events": [
      {
        "event_type": "CLAIMED",
        "execution_token_hash": "390cc4636e1097b6",
        "execution_token_suffix": "1eb4d1",
        "occurred_at": "2026-08-18T17:49:32.103957+00:00",
        "worker_id": "local-worker-1"
      },
      {
        "event_type": "CLAIMED",
        "execution_token_hash": "10af99b66bbfc9ba",
        "execution_token_suffix": "6dd129",
        "occurred_at": "2026-08-19T10:42:04.171953+00:00",
        "worker_id": "local-worker-1"
      }
    ]
  }
}
```

## Winner processing attempts

```json
[
  {
    "id": 120,
    "background_job_id": 30812,
    "attempt_no": null,
    "process_type": "WINNER_COHORT_REFRESH",
    "status": "RUNNING",
    "started_at": "2026-08-18 19:49:32.218953+02:00",
    "completed_at": null,
    "cohort_generation_id": null,
    "counts_json": {},
    "checkpoint_json": {},
    "terminal_reason_code": null,
    "error_message": null,
    "metadata_json": {
      "background_job_type": "WINNER_COHORT_REFRESH",
      "execution_token_hash": "390cc4636e1097b6",
      "execution_token_suffix": "1eb4d1",
      "lease_owner": "local-worker-1"
    }
  },
  {
    "id": 124,
    "background_job_id": 30812,
    "attempt_no": null,
    "process_type": "WINNER_COHORT_REFRESH",
    "status": "RUNNING",
    "started_at": "2026-08-19 12:42:04.225946+02:00",
    "completed_at": null,
    "cohort_generation_id": null,
    "counts_json": {},
    "checkpoint_json": {},
    "terminal_reason_code": null,
    "error_message": null,
    "metadata_json": {
      "background_job_type": "WINNER_COHORT_REFRESH",
      "execution_token_hash": "10af99b66bbfc9ba",
      "execution_token_suffix": "6dd129",
      "lease_owner": "local-worker-1"
    }
  }
]
```

`last_checkpoint_at`, `attempt_correlation_id`, and `superseded_by_processing_run_id` were also null for both rows.

## Cohort generations and refresh state

Exact results:

```json
{
  "winner_cohort_generations": [],
  "winner_cohort_refresh_state": []
}
```

There is no `BUILDING`, `READY`, or `PUBLISHED` generation. Generation creation did not occur. Consequently there is no generation checkpoint, planned/completed group counter, evidence-row counter, or generation watermark to report, and no published generation pointer exists to move.

## Published-state preservation baseline

Because the stale worker is running the legacy all-history implementation, this installation has legacy cohort statistics rather than a generation publication pointer. The exact pre-cancellation baseline was:

```json
{
  "winner_cohort_definitions": 360,
  "winner_cohort_statistics": 1356,
  "winner_cohort_generations": 0,
  "winner_cohort_refresh_states": 0,
  "winner_evidence_manifests": 415,
  "decision_time_estimates": 11079,
  "latest_rescore_estimates": 18295,
  "statistics_by_level": {
    "L0": 646,
    "L1": 360,
    "L2": 170,
    "L3": 122,
    "L4": 47,
    "L5": 11
  }
}
```

The newest legacy statistic timestamps at capture were L0 `2026-08-19 12:33:59.658219+02:00`, L1 `2026-08-19 10:49:13.278672+02:00`, L2 `2026-08-19 12:35:35.739619+02:00`, L3 `2026-08-19 10:17:05.595448+02:00`, L4 `2026-08-18 19:56:19.327112+02:00`, and L5 `2026-08-18 19:49:59.108544+02:00`.

## Worker deployment identity

```json
{
  "worker_id": "local-worker-1",
  "hostname": "NewLaptop",
  "process_id": 1772,
  "wrapper_process_id": 21136,
  "queues": ["interactive", "broker", "background"],
  "started_at": "2026-08-18 19:49:30.772899+02:00",
  "heartbeat_at": "2026-08-19 15:15:05.217940+02:00",
  "stopping_at": null,
  "command": "python -m app.worker --worker-id local-worker-1 --queues interactive,broker,background",
  "process_cpu_seconds": 55527.578125,
  "working_set_bytes": 866684928,
  "private_memory_bytes": 915996672,
  "repository_head": "52e7e80f98bae5cb957b368e042e7cc3727ddb22",
  "repository_head_committed_at": "2026-08-19 14:02:34+02:00",
  "winner_reliability_commit": "2438e6e17979dbfec4b41189fcba2e7a14570b87",
  "winner_reliability_commit_time": "2026-08-19 12:42:20+02:00",
  "database_alembic_version": "0049_winner_jobs_reliability",
  "expected_alembic_head": "0049_winner_jobs_reliability"
}
```

The live worker did not expose a deployment SHA in its registration record. Its start/claim timestamps prove it was loaded before the remediation commit. Current repository HEAD includes the reliability commit, but the already-running Python interpreter does not hot-reload worker modules.

## Live database activity

There was no PostgreSQL blocking wait. The long-lived worker backend (PostgreSQL PID 9904, backend start 2026-08-18 19:49:30+02:00) was `idle in transaction` after a full-row `SELECT` from `winner_prediction_snapshots`, with transaction start `2026-08-19 15:14:57.113141+02:00` and query start `2026-08-19 15:15:03.129822+02:00`. A separate worker heartbeat connection committed at 15:15:05. This is consistent with repeated application-side ORM work rather than lock contention.

## Blocked interactive work baseline

Job 30993 was `QUEUED`, had never started, had no worker/lease/token, and contained `{"pipeline_run_id": 110}`. Pipeline 110 (upload Run 118) was `PENDING`, `current_step="VALIDATING_RUN"`, `started_at=null`, with all 12 normal pipeline steps still `PENDING`. No Run 118 or pipeline-step fields were mutated during capture.

## Rollout setting observed

The checked-in defaults and `.env.example` disable automatic/v2 cohort refresh, but the active local `.env` had both `WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED=true` and `WINNER_COHORT_REFRESH_V2_ENABLED=true`. This conflicts with the stated pending-operator-rollout posture and must be corrected before restarting the worker.
