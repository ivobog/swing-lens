# Phase 19 - Observability, Operations, Backup, and Recovery Review

Review date: 2026-08-02

## Scope

Phase 19 reviews whether SwingLens can be operated safely after deployment: structured logs,
metrics and alerts, readiness semantics, interrupted-job recovery, backup/restore procedures,
cleanup and retention, incident response, and rollback expectations for code, config, schema, and
model artifacts.

## Executive Summary

Phase 19 is amber/red.

The application has useful operational foundations: `/ready` verifies the database and local
directories, the FastAPI lifespan starts and stops the background worker when enabled, background
jobs have leases, execution tokens, stale recovery, retries, cancellation, and capped lease-event
history, and CERI has the strongest observability surface with structured events, redaction,
in-memory metrics, freshness/quarantine/conflict/stale views, and preview-first purge controls.
The focused operational test slice passed.

The release is not operations-ready under the Phase 19 exit criteria because PostgreSQL
backup/restore is not documented or tested, restore integrity checks are absent, readiness does not
cover migrations/worker health/optional dependencies/disk capacity, metrics are not exported to a
real monitoring system, and incident/rollback procedures are not available as operator runbooks.

## Evidence Reviewed

| Area | Evidence |
| --- | --- |
| Health/readiness | `app/routers/health_routes.py:13-57`, `app/models/schemas.py:12-17`, `tests/test_hardening.py:182-190` |
| App worker lifecycle | `app/main.py:58-89`, `tests/test_app_lifespan_worker.py:9-43` |
| Background job recovery | `app/services/background_worker.py:73-111`, `app/services/background_job_service.py:136-249`, `app/services/background_job_service.py:319-363`, `tests/test_background_job_service.py:148-291` |
| CERI logging/metrics/redaction | `app/services/ceri/observability.py:11-129`, `app/services/ceri/export_policy.py:10-121`, `tests/ceri/test_ceri_performance.py:27-60`, `tests/ceri/test_ceri_acceptance_fixture.py:21-59` |
| CERI operations | `app/services/ceri/query_service.py:399-450`, `app/templates/ceri_operations.html:12-172`, `tests/ceri/test_ceri_routes_ui.py:327-377` |
| Winner probability operations | `app/services/winner_probability/operations_service.py:10-64`, `app/templates/winner_probability_operations.html:27-84`, `tests/winner_probability/test_routes_ui.py:179-189` |
| Setup lifecycle operations | `app/services/setup_lifecycle/query_service.py:224-280`, `app/templates/setup_lifecycle_operations.html:21`, `tests/setup_lifecycle/test_routes.py:263-312` |
| PostgreSQL deployment baseline | `docker-compose.yml:2-16`, `.env.example:9` |
| Retention/purge policy | `config/ceri.yaml:192-201`, `config/setup_lifecycle.yaml:408-413`, `config/winner_probability.yaml:136-152`, `docs/ceri.md:10-31` |

## Verification Run

Command:

```powershell
uv run pytest tests/test_background_worker.py tests/test_background_job_service.py tests/test_app_lifespan_worker.py tests/test_hardening.py tests/ceri/test_ceri_performance.py tests/ceri/test_ceri_acceptance_fixture.py tests/ceri/test_ceri_routes_ui.py tests/ceri/test_ceri_routes_admin.py tests/winner_probability/test_routes_ui.py tests/setup_lifecycle/test_routes.py -q
```

Result: 75 passed, 1 warning in 9.64s.

Warning: Starlette TestClient deprecation warning from `fastapi/testclient.py`.

## Findings

### PH19-001 - No PostgreSQL backup/restore procedure or restore validation test

Severity: P1

Observed behavior: The repo defines a local PostgreSQL 16 container and named volume in
`docker-compose.yml:2-16`, but repository searches did not find an operator runbook using
`pg_dump`, `pg_restore`, restore-to-clean-environment steps, backup cadence, retention, encryption,
or restore validation. Existing docs mention migration/postgres gates from the Phase 18 review, but
not disaster-recovery execution.

Expected behavior: Operators should have a tested PostgreSQL backup and restore procedure, plus
a validation script that proves restored evidence integrity for upload runs, pipeline runs,
background jobs, CERI source evidence, winner-probability snapshots/outcomes, setup lifecycle
episodes/events, and export artifacts.

Impact: The Phase 19 exit criterion "backup can be restored and validated" is not met. A disk
failure, bad migration, or corrupted local database can become an unrecoverable evidence-loss event.

Recommended remediation:

- Add `docs/operations/backup_restore.md` with dump, restore, validation, retention, encryption, and
  ownership instructions.
- Add `scripts/ops/backup_postgres.ps1` and `scripts/ops/restore_postgres.ps1` or equivalent
  cross-platform scripts.
- Add a restore validation command that checks row counts, schema head, critical foreign-key
  relationships, evidence hashes, purge audit preservation, model artifact references, and sample
  point-in-time queries.
- Add a CI/manual release gate that restores the latest backup into a clean database before release.

Acceptance criteria:

- A clean PostgreSQL instance can be restored from a backup without manual DB archaeology.
- Validation returns a machine-readable pass/fail report with evidence integrity checks.
- Restore steps are documented for both local Docker and configured production-like PostgreSQL.

### PH19-002 - Readiness endpoint is too shallow for operational readiness

Severity: P1

Observed behavior: `/ready` checks `select 1` and attempts to create upload/export/cache
directories (`app/routers/health_routes.py:24-57`). The response schema only exposes
`database_ok`, `local_dirs_ok`, and a string `checks` map (`app/models/schemas.py:12-17`).
It does not check Alembic migration head, worker liveness, stale running jobs, queue depth, disk
capacity, optional IB/CERI/provider dependencies, required config hashes, or export/cache growth.
Database and directory exception strings are returned directly.

Expected behavior: Readiness should distinguish web process liveness from pipeline readiness and
degrade when core dependencies are unusable or stale. Sensitive exceptions should be redacted.

Impact: A deploy can report ready while migrations are missing, the worker is not running, all jobs
are stale, provider dependencies are down, or disk is nearly full.

Recommended remediation:

- Split `/health` liveness from `/ready` operational readiness.
- Extend readiness checks with `database`, `migrations`, `local_dirs`, `disk`, `worker`,
  `background_jobs`, `ib_optional`, `ceri_optional`, `exports`, and `config` keys.
- Return structured statuses: `ok`, `degraded`, `failed`, `optional_unavailable`.
- Redact exception text before returning it.
- Add tests for degraded migration state, stale jobs, worker disabled/enabled semantics, disk failure,
  and redacted DB errors.

Acceptance criteria:

- Operators can tell whether the web app, DB, migrations, worker, queues, and storage are healthy
  from `/ready` without querying tables manually.

### PH19-003 - Metrics are CERI-local and not exported/alerted

Severity: P1

Observed behavior: CERI defines required metric families and an in-memory `CeriMetricRegistry`
(`app/services/ceri/observability.py:13-90`), and tests verify the registry and sample events
(`tests/ceri/test_ceri_performance.py:27-36`). Metric producers exist for CERI source records and
purge paths (`app/services/ceri/source_record_service.py:58-213`,
`app/services/ceri/purge_service.py:81-225`). Searches did not find a Prometheus/OpenTelemetry
export endpoint, durable metrics sink, alert rules, or non-CERI metrics for full pipeline jobs,
stale jobs, export failures, IB/provider health, disk growth, or backup status.

Expected behavior: Phase 19 requires metrics and alerts for pipeline failures, stale jobs, data
freshness, provider health, scoring coverage, export failures, and disk growth.

Impact: Operators can inspect some local HTML/JSON pages, but they cannot reliably alert on
failures before a user notices stale or missing research output.

Recommended remediation:

- Export metrics through a standard endpoint or OpenTelemetry collector.
- Add app-wide counters/gauges/histograms for background jobs, pipeline runs, IB fetch runs,
  winner-probability processing, setup lifecycle evaluations, exports, disk, backup freshness, and
  restore-validation status.
- Add alert rule definitions and thresholds in `docs/operations/alerts.md`.
- Add tests that assert metric emission for success/failure/stale/retry/export paths.

Acceptance criteria:

- Operators can configure alerts without scraping HTML pages or directly querying PostgreSQL.

### PH19-004 - Structured logging and correlation are inconsistent outside CERI

Severity: P2

Observed behavior: CERI structured log payloads include event, job/run IDs, provider, dataset,
ticker, calculation version, config hash, request key, execution token, and redaction
(`app/services/ceri/observability.py:92-122`). Background worker logs only `job_id` and `job_type`
for most lifecycle events (`app/services/background_worker.py:75-111`). There is no request
correlation ID middleware, and many services persist or return raw `error_message` strings. Winner
probability processing runs include useful `run_id`, `background_job_id`, `config_hash`, counts,
checkpoint, and error fields in the operations service (`app/services/winner_probability/operations_service.py:45-63`),
but those are not consistently mirrored in logs.

Expected behavior: Logs should consistently include correlation IDs, run IDs, job IDs, provider IDs,
model versions, config hashes, request keys, and redacted error context across normal and exception
paths.

Impact: Incidents require stitching together database rows, UI pages, and partial logs. Failed or
stale research runs can be visible but not quickly traceable across services.

Recommended remediation:

- Add request correlation middleware and propagate correlation IDs into background job payloads and
  processing-run metadata.
- Standardize event names and fields for `pipeline.*`, `job.*`, `ib.*`, `export.*`,
  `winner_probability.*`, `setup_lifecycle.*`, and `ceri.*`.
- Include `run_id`, `pipeline_run_id`, `background_job_id`, `job_type`, `model_version`,
  `config_hash`, `provider`, `dataset`, `request_key`, and redacted `error_code`.
- Add log assertion tests for normal, retry, cancel, stale-recovery, and exception paths.

Acceptance criteria:

- A failed run can be traced from one correlation ID through request, job, processing run, provider,
  export, and final UI/API status.

### PH19-005 - Redaction is strong in CERI but incomplete in shared exception paths

Severity: P2

Observed behavior: CERI redaction handles sensitive field fragments, bearer tokens, local paths, and
SQL-shaped text (`app/services/ceri/export_policy.py:10-121`), with tests covering nested secrets,
local paths, auth tokens, and SQL details (`tests/ceri/test_ceri_acceptance_fixture.py:21-59`,
`tests/ceri/test_ceri_performance.py:38-60`). Shared background job error sanitization only removes
newlines and truncates to 500 characters (`app/services/background_job_service.py:362-363`), and
the readiness route returns raw SQLAlchemy/OSError strings (`app/routers/health_routes.py:36-47`).

Expected behavior: Normal and exception paths should use one shared redaction policy for secrets,
tokens, credentials, local paths, SQL details, provider payloads, and user-upload paths.

Impact: A provider/API exception or DB connection error containing credentials, tokens, SQL details,
or local paths can be persisted to `background_jobs.error_message` or surfaced from `/ready`.

Recommended remediation:

- Promote CERI redaction or a generalized variant to `app/services/redaction.py`.
- Use it in background jobs, pipeline errors, upload errors, IB errors, readiness errors, exports,
  and operation views.
- Add targeted tests that inject `Bearer`, `password=`, `api_key`, local paths, and SQL strings into
  each exception path.

Acceptance criteria:

- No operator surface or persisted operational error includes raw secrets, local paths, or SQL detail.

### PH19-006 - Cleanup, retention, and purge are declared but not fully executable

Severity: P2

Observed behavior: Config declares CERI retention/purge controls (`config/ceri.yaml:192-201`),
setup lifecycle purge defaults (`config/setup_lifecycle.yaml:408-413`), and winner probability
retention classes (`config/winner_probability.yaml:136-152`). CERI operations displays retention
and export policy (`app/templates/ceri_operations.html:112-126`). Previous phase findings already
show CERI provider-license purge execution is audit-only and ordinary cleanup jobs are missing.

Expected behavior: Retention, purge, archive, and cleanup behavior should be explicit, scheduled or
operator-triggerable, and validated with tests.

Impact: Disk and table growth can continue indefinitely, and an operator may believe a purge has
removed or invalidated licensed data when it has only recorded an audit.

Recommended remediation:

- Define which artifacts are immutable evidence, rebuildable cache, export artifact, provider
  licensed data, and operational log.
- Implement cleanup jobs for rebuildable caches, expired exports, old operational logs, and upload
  artifacts according to policy.
- Resolve CERI purge semantics: delete, tombstone, redact, quarantine, or rename audit-only behavior.
- Add dry-run and execution tests for every cleanup policy.

Acceptance criteria:

- Operators can preview, execute, audit, and verify cleanup without damaging immutable evidence.

### PH19-007 - Incident and rollback runbooks are missing

Severity: P2

Observed behavior: Repository searches did not find concrete incident playbooks or rollback
procedures for corrupted upload, bad migration, IB outage, provider outage, duplicate jobs,
incorrect model release, leaked secret, schema rollback, config rollback, or model artifact rollback.
Some domain docs discuss rollback gates conceptually, but no operator checklist exists.

Expected behavior: Phase 19 explicitly requires incident playbooks and code/config/schema/model
rollback expectations.

Impact: Recovery depends on developer judgment during an incident, which increases mean time to
recovery and the chance of evidence loss.

Recommended remediation:

- Add `docs/operations/incidents.md` and `docs/operations/rollback.md`.
- Attach each playbook to log fields, metrics, DB queries, admin controls, backup/restore steps,
  and validation commands.
- Add release checklist gates requiring a current backup and restore validation before schema/model
  promotion.

Acceptance criteria:

- Operators can follow documented steps for the named incidents without needing codebase knowledge.

## Operations Readiness Checklist

| Check | Status | Evidence | Required before release |
| --- | --- | --- | --- |
| Web liveness endpoint | Ready | `/health` returns app/status and configured DB/IB fields. | Keep as liveness-only endpoint. |
| Operational readiness endpoint | Not ready | `/ready` checks DB and local dirs only. | Add migrations, worker, jobs, disk, optional deps, config, redaction. |
| Background worker startup/shutdown | Partial | Lifespan starts/stops worker when enabled; unit tests pass. | Add end-to-end interrupted-job restart test with PostgreSQL. |
| Stale job recovery | Partial | Lease expiry recovery, retries, stale terminal status, execution-token fencing tested. | Expose stale counts in readiness/metrics/alerts. |
| Pipeline failure visibility | Partial | Background jobs and some operations pages show failures. | Add unified job/pipeline failure dashboard and metrics. |
| CERI provider/freshness visibility | Partial | CERI operations page and JSON status expose freshness/quarantine/conflict/stale. | Add alert rules and provider health metrics. |
| Winner probability operations | Partial | Pending, overdue, failed run counts and recent runs visible. | Add stale run alerts, model-release rollback hooks. |
| Setup lifecycle operations | Partial | Latest status, pending jobs, stale lease warning visible. | Add metric export and readiness integration. |
| Structured logs | Partial | Strong CERI schema; narrow shared job logs. | Add app-wide schema and correlation ID. |
| Redaction | Partial | Strong CERI redaction; shared errors not redacted enough. | Use shared redaction everywhere. |
| Backup procedure | Missing | No `pg_dump`/`pg_restore` runbook found. | Add backup script and docs. |
| Restore validation | Missing | No clean-restore integrity test found. | Add restore validation report and release gate. |
| Cleanup/retention execution | Partial | Policies exist; execution is incomplete/inconsistent. | Add scheduled/operator cleanup jobs and tests. |
| Incident runbooks | Missing | No concrete runbooks found. | Add playbooks for Phase 19 incident set. |
| Rollback procedure | Missing | No operator rollback procedure found. | Add code/config/schema/model rollback procedure. |

## Logging and Metrics Schema

### Required Structured Log Envelope

| Field | Required | Notes |
| --- | --- | --- |
| `event` | Yes | Dot-family event name such as `job.claimed` or `ceri.ingestion_completed`. |
| `timestamp` | Yes | UTC ISO timestamp, supplied by logger or payload. |
| `severity` | Yes | `info`, `warning`, `error`, `critical`. |
| `correlation_id` | Yes | Request-scoped ID propagated to background jobs. |
| `request_id` | When HTTP-originated | Incoming request ID or generated UUID. |
| `background_job_id` | When job-originated | Background job table ID. |
| `job_type` | When job-originated | `FULL_PIPELINE`, CERI job type, winner-probability job type, etc. |
| `execution_token` | Internal/restricted | Store only if needed for fencing diagnostics; redact in exported/support logs. |
| `run_id` | When applicable | Upload/run ID. |
| `pipeline_run_id` | When applicable | Pipeline run table ID. |
| `processing_run_id` | When applicable | Domain processing run ID. |
| `provider` | When applicable | `ib`, `manual`, CERI provider ID, etc. |
| `dataset` | When applicable | Provider dataset or export dataset. |
| `model_version` | When applicable | Active model/calculation version. |
| `config_hash` | When applicable | Domain config hash. |
| `request_key` | When applicable | Deterministic idempotency/request key. |
| `status` | When applicable | `queued`, `running`, `completed`, `partial`, `failed`, `stale`, etc. |
| `counts` | When applicable | Sanitized count map. |
| `checkpoint` | When applicable | Sanitized checkpoint/phase map. |
| `error_code` | On failures | Stable, non-sensitive code. |
| `error_message` | On failures | Redacted and bounded. |

### Required Event Families

| Family | Event examples |
| --- | --- |
| `request.*` | `request.started`, `request.completed`, `request.failed` |
| `job.*` | `job.enqueued`, `job.claimed`, `job.heartbeat`, `job.completed`, `job.partial`, `job.cancelled`, `job.failed`, `job.stale_recovered`, `job.lease_lost` |
| `pipeline.*` | `pipeline.started`, `pipeline.step_completed`, `pipeline.step_failed`, `pipeline.completed` |
| `ib.*` | `ib.connected`, `ib.contract_failed`, `ib.fetch_failed`, `ib.rate_limited`, `ib.outage_detected` |
| `export.*` | `export.started`, `export.completed`, `export.failed`, `export.refused_too_large` |
| `winner_probability.*` | `winner_probability.capture_completed`, `winner_probability.outcome_overdue`, `winner_probability.model_promoted`, `winner_probability.model_rolled_back` |
| `setup_lifecycle.*` | `setup_lifecycle.evaluation_completed`, `setup_lifecycle.stale_system_warning`, `setup_lifecycle.alert_emitted` |
| `ceri.*` | Existing CERI structured events plus provider/outage events. |
| `backup.*` | `backup.started`, `backup.completed`, `backup.failed`, `restore.validation_completed` |

### Required Metrics

| Metric | Type | Labels | Alert |
| --- | --- | --- | --- |
| `swinglens_background_jobs_total` | Counter | `job_type`, `status` | Failed/stale growth. |
| `swinglens_background_jobs_running` | Gauge | `job_type` | Running older than lease threshold. |
| `swinglens_background_job_age_seconds` | Gauge/histogram | `job_type`, `status` | Queued/running too long. |
| `swinglens_pipeline_runs_total` | Counter | `status`, `step` | Pipeline failure rate. |
| `swinglens_data_freshness_age_days` | Gauge | `source`, `dataset`, `ticker` | Age exceeds policy. |
| `swinglens_ib_provider_health` | Gauge | `host`, `port` | IB unavailable. |
| `swinglens_ceri_provider_health` | Gauge | `provider`, `dataset` | Provider unavailable/quota exhausted. |
| `swinglens_scoring_coverage_ratio` | Gauge | `run_id`, `domain` | Coverage below threshold. |
| `swinglens_export_failures_total` | Counter | `export_type` | Any repeated export failure. |
| `swinglens_disk_free_bytes` | Gauge | `path_role` | Free bytes below threshold. |
| `swinglens_backup_age_seconds` | Gauge | `database` | Backup older than RPO. |
| `swinglens_restore_validation_success` | Gauge | `database`, `backup_id` | Last restore validation failed. |

## Backup/Restore Test Report

Status: Not executed. No backup/restore scripts or runbook were found in the repository.

Required test procedure:

1. Start a clean PostgreSQL target database.
2. Run migrations to the expected Alembic head.
3. Seed or use a representative SwingLens database containing upload runs, price bars, pipeline
   runs, background jobs, setup lifecycle evidence, winner-probability evidence, CERI evidence, and
   purge audits.
4. Create a backup with a documented command.
5. Restore into a new clean database.
6. Run validation:
   - `alembic current` equals expected head.
   - Critical table row counts match source backup metadata.
   - Foreign-key integrity passes.
   - CERI evidence hashes, purge audits, revisions, and point-in-time queries validate.
   - Winner-probability prediction snapshots and forward outcomes validate.
   - Setup lifecycle episodes/events/snapshots validate.
   - Latest pipeline and background job statuses are visible through operations APIs.
7. Store a validation report with backup ID, source DB, target DB, commit, config hashes, row-count
   summary, evidence-hash summary, and pass/fail.

Minimum command shape to document:

```powershell
pg_dump --format=custom --file backups\swinglens_YYYYMMDD_HHMM.dump $env:DATABASE_URL
createdb swinglens_restore_check
pg_restore --dbname postgresql+psycopg://... backups\swinglens_YYYYMMDD_HHMM.dump
uv run alembic current
uv run python scripts\ops\validate_restore.py --database-url $env:RESTORE_DATABASE_URL --report restore_report.json
```

Exact syntax should be adjusted for the installed PostgreSQL CLI and SQLAlchemy/psycopg URL format.

## Incident Runbooks

### Corrupted Upload

Detection:

- Upload parse errors, pipeline failure, abnormal missing-data coverage, or user report.

Immediate response:

- Stop downstream processing for the affected run if still queued/running.
- Preserve uploaded file, parse error, DB run row, and pipeline job metadata.
- Mark the run as failed/invalid with a redacted error.
- Re-upload corrected file as a new run; do not mutate historical evidence unless a documented
  correction path exists.

Validation:

- Affected run remains inspectable.
- Corrected run completes pipeline.
- No orphan upload artifacts remain outside policy.

### Bad Migration

Detection:

- Alembic upgrade fails, `/ready` migration check fails, app errors after deployment.

Immediate response:

- Stop app/worker writes.
- Preserve current database state and logs.
- Restore from latest validated pre-migration backup if migration changed data destructively.
- If downgrade is safe and documented, run the downgrade only after backup.

Validation:

- `alembic current` matches expected rollback head.
- `/ready` passes all required checks.
- Evidence-hash validation passes.

### IB Outage

Detection:

- IB health metric/route reports unavailable, contract failures spike, fetch jobs fail or stall.

Immediate response:

- Mark IB-dependent features degraded.
- Prevent repeated tight-loop retries; rely on background job backoff.
- Continue non-IB analysis only when source freshness policy allows it.

Validation:

- IB reconnect succeeds.
- Failed/stale fetch jobs are requeued or closed with visible errors.
- Data freshness returns below threshold.

### CERI Provider Outage

Detection:

- Provider health unavailable, quota exhausted, ingestion/backfill failures, stale CERI dataset
  freshness.

Immediate response:

- Mark CERI provider degraded.
- Keep core SwingLens workflows available.
- Pause provider ingestion/backfill jobs if outage is persistent.

Validation:

- Provider health returns healthy or manual provider fallback is selected.
- Stale CERI records are visible.
- No restricted provider payloads were exported or logged.

### Duplicate Jobs

Detection:

- Multiple active jobs with the same deterministic request key, duplicate processing run, or lease
  conflict/lease lost event.

Immediate response:

- Identify canonical job by active lease/execution token.
- Cancel queued duplicate jobs.
- Let execution-token fencing reject stale workers.

Validation:

- Only one processing run reaches terminal success for the request key.
- Lease events record duplicate/lease-lost history.

### Incorrect Model Release

Detection:

- Drift metrics, calibration regression, user report, or post-release validation failure.

Immediate response:

- Disable/promote away from the bad model via feature flag/model registry.
- Preserve model artifact, training data hash, approval event, and prediction snapshots.
- Recompute only derived outputs that are explicitly rebuildable.

Validation:

- Active model endpoint/page shows previous approved model.
- New predictions include rollback model version/config hash.
- Historical prediction evidence remains unchanged.

### Leaked Secret

Detection:

- Secret in log, DB error, support export, screenshot, or repository file.

Immediate response:

- Revoke and rotate the secret.
- Identify exposure scope.
- Redact or quarantine logs/exports according to policy.
- Add a regression test to the relevant redaction path.

Validation:

- Secret no longer appears in logs, DB operational errors, exports, or repo search.
- Provider/API auth succeeds with rotated credentials.

## Rollback Procedure

### Code Rollback

1. Confirm current backup is valid before rollback if schema/data behavior changed.
2. Deploy the previous known-good commit.
3. Restart web and worker processes.
4. Verify `/health`, `/ready`, operations pages, and focused smoke tests.
5. Leave immutable evidence untouched.

### Configuration Rollback

1. Restore previous config file or environment setting.
2. Record config hash and effective config in deployment notes.
3. Restart affected processes.
4. Confirm new processing runs use the rollback `config_hash`.
5. Do not rewrite historical outputs unless a rebuild is explicitly requested and auditable.

### Schema Rollback

1. Stop app/worker writes.
2. Create a fresh backup.
3. Prefer restore from validated pre-migration backup for destructive migrations.
4. Use Alembic downgrade only when the downgrade is tested and non-destructive for evidence.
5. Run restore/schema validation before reopening writes.

### Model Artifact Rollback

1. Mark the bad model inactive and reactivate the prior approved model.
2. Preserve bad artifact, release metadata, approval event, and incident ID.
3. Ensure new predictions include rollback model version and config hash.
4. Do not mutate historical decision-time predictions; create corrected/rebuilt derivatives only
   through an auditable rebuild path.

## Exit Criteria Assessment

| Exit criterion | Status | Assessment |
| --- | --- | --- |
| Backup can be restored and validated | Not met | No backup/restore runbook or restore validation test was found. |
| Operators can identify failed/stale research runs without DB archaeology | Partially met | CERI, setup lifecycle, winner probability, and background jobs expose some status, but no unified metrics/alerts/readiness integration exists. |
| Sensitive data redacted | Partially met | CERI redaction is strong and tested; shared background-job and readiness exception paths are not fully redacted. |

## Recommended Release Gate

Do not mark Phase 19 complete until:

- Backup and restore scripts exist.
- A clean restore has been tested and produces a stored validation report.
- `/ready` includes migrations, worker, stale jobs, storage, optional dependencies, and redacted
  errors.
- Metrics are exported and alert rules exist for the Phase 19 alert set.
- Shared redaction covers all exception paths.
- Incident and rollback runbooks are committed under `docs/operations/`.
- An interrupted-job restart test passes against PostgreSQL.
