# Backup and Restore Runbook

SwingLens treats PostgreSQL as the durable evidence store. Create and validate a backup before
destructive migrations, purge work, release candidates, or incident rollback.

## Canonical Local Database

The checked-in local setup uses PostgreSQL on `127.0.0.1:5432`:

```powershell
docker compose up -d postgres
Copy-Item .env.example .env
uv run alembic upgrade head
```

If another PostgreSQL server already owns port `5432`, either stop it before using Compose or
override the Compose host port and update `.env` consistently.

## Create A Backup

Requirements:

- PostgreSQL client tools on `PATH` (`pg_dump`, `pg_restore`, and `psql`).
- `DATABASE_URL` points at the source SwingLens database.
- A secure destination for backup artifacts.

```powershell
$env:DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/swinglens"
.\scripts\ops\backup_postgres.ps1 -BackupDir backups
```

The script writes:

- `backups/swinglens_<backup_id>.dump`
- `backups/swinglens_<backup_id>.metadata.json`

Use the custom dump format by default. Plain SQL is available only for diagnostics:

```powershell
.\scripts\ops\backup_postgres.ps1 -BackupDir backups -PlainSql
```

## Restore Into A Clean Database

Create a disposable database, then restore and validate:

```powershell
createdb swinglens_restore_check
$env:RESTORE_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/swinglens_restore_check"
.\scripts\ops\restore_postgres.ps1 `
  -BackupPath backups\swinglens_YYYYMMDD_HHMMSS.dump `
  -ValidationReport backups\restore_validation_YYYYMMDD_HHMMSS.json
```

The restore target must be clean or disposable. Do not point `RESTORE_DATABASE_URL` at the active
production/research database.

## Validation Gate

The restore validator checks:

- Restored Alembic revision matches repository head.
- Critical evidence tables exist.
- Critical table row counts are captured in the report.
- Reflected single-column foreign keys have no orphaned child rows.
- Evidence/hash columns are not blank or null.

Run it directly when needed:

```powershell
uv run python scripts\ops\validate_restore.py `
  --database-url $env:RESTORE_DATABASE_URL `
  --report backups\restore_validation.json
```

The command exits `0` only when validation passes. Treat a non-zero exit code as a release blocker
until the report explains and the issue is fixed.

## Retention And Protection

- Keep at least one validated pre-migration backup for every schema or lifecycle change.
- Keep daily local backups for 7 days and weekly backups for 4 weeks when actively using the app.
- Encrypt backups before moving them off the local machine.
- Do not commit backup dumps or validation reports containing operational database details.
- Store validation reports with release or incident records, not in normal source control.

## Rollback Use

For destructive migration or purge incidents:

1. Stop app and worker writes.
2. Create a fresh backup of the current broken state for forensics.
3. Restore the latest validated pre-change backup into a clean database.
4. Run `validate_restore.py` and confirm the report passes.
5. Repoint the app to the restored database or promote the restored database according to the local
   PostgreSQL procedure.
6. Reopen writes only after `/ready` is healthy.
