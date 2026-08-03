# Incident Runbooks

Use these local runbooks when SwingLens reports degraded readiness, stale jobs, failed exports, or
data-recovery risk. Keep notes with timestamps, command output, backup IDs, and validation reports.

## Readiness Degraded

1. Open `http://127.0.0.1:8000/ready`.
2. Inspect the failing `checks` entry.
3. If `database` fails, verify `.env` `DATABASE_URL`, `docker compose ps`, and PostgreSQL logs.
4. If `migrations` fails, stop writes and run `uv run alembic current` and `uv run alembic heads`.
5. If `storage` fails, verify permissions on `UPLOAD_DIR`, `EXPORT_DIR`, and `CACHE_DIR`.
6. If `worker` fails, start the embedded worker or disable durable pipeline for local diagnostics.
7. If `jobs` fails, follow the stale-job runbook below.

## Stale Or Failed Jobs

1. Check `/metrics` for job failure/retry counters.
2. Inspect active jobs in the database:

   ```sql
   select id, job_type, status, retry_count, worker_id, lease_expires_at, error_message
   from background_jobs
   order by created_at desc
   limit 50;
   ```

3. Restart the app/worker process if the embedded worker stopped.
4. Let normal stale-job recovery requeue expired leases.
5. For repeated failures, preserve the job row, redacted `error_message`, and related run ID before
   retrying manually.

## Backup Or Restore Failure

1. Do not run destructive changes while backup/restore is failing.
2. Confirm PostgreSQL client tools are on `PATH`: `pg_dump`, `pg_restore`, and `psql`.
3. Re-run `backup_postgres.ps1` or `restore_postgres.ps1` with a fresh `BackupId`.
4. If validation fails, inspect the JSON report for schema head, missing tables, FK violations, and
   blank evidence hashes.
5. Keep both the failed report and source backup metadata with the incident notes.

## Export Failure

1. Check the export endpoint response and local app logs.
2. Confirm `/ready` storage is healthy.
3. Check `/metrics` for `swinglens_exports_generated_total` and `swinglens_export_rows_total`.
4. If the export includes sensitive operational data, verify fields are redacted before sharing.

## Provider Or IB Failure

1. Confirm SwingLens remains read-only and no trading/order route was used.
2. Check `/ib/status` for IB Gateway availability.
3. Check CERI provider health at `/api/ceri/providers/health` when CERI is enabled.
4. Retry after provider recovery; preserve failed job IDs and redacted provider errors.

## Rollback

1. Stop app and worker writes.
2. Create a fresh forensic backup of the current state.
3. Restore the latest validated pre-change backup into a clean database.
4. Run `scripts/ops/validate_restore.py` and confirm the report passes.
5. Repoint the app to the restored database or promote it using the local PostgreSQL procedure.
6. Run `/ready`, then reopen writes.
