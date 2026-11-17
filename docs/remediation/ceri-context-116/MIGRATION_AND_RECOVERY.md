# Migration and conditional normal-runtime recovery

This document is a recommendation, not production recovery authorization.
No production migration, deployment, restart, pipeline creation, or historical
pipeline/job recovery is performed by the implementation utilities.

## Migration 0096

Parent: `0095_ib_bar_admission_receipts`. PostgreSQL 15 or newer is required for
the native NULLS NOT DISTINCT index. Models and migration use the same partial
indexes; the controlled-replay unique constraint is unchanged.

Run `preflight.sql` read-only and retain its complete output. Verify the
database identity, server version, Alembic parent, actual index definitions,
native/legacy/replay distributions, nullable upload population, and duplicate
queries. Native duplicate groups require review; the migration never deletes
or repairs financial rows to make the indexes succeed.

The migration takes SHARE ROW EXCLUSIVE on the score table, checks duplicate
native groups, creates both replacement indexes, and only then drops the old
four-column constraint. Alembic transactional DDL makes the replacement atomic.
The operator must quiesce score producers and use a bounded `lock_timeout` for
the maintenance window. This is intentionally a transactional migration, not
a promise of online index replacement under an active writer workload.

Run `postflight.sql` and compare retained score, evidence, disposition, and
manifest fingerprints. Check index validity/readiness, absence of native
duplicates, and independent context 113/116 membership. Application tests must
also prove native capture, immutable sealing, tail processing, continuation,
handoff membership, same-context retries, concurrent retries, replay and legacy
NULL behavior. SQL insertion alone is not certification.

## Rollback

Before any second generation occupies an old key, downgrade reinstates the
original unique constraint before removing the replacement indexes. The
PostgreSQL test exercises upgrade, downgrade and upgrade again.

After independent generations or overlapping controlled replays exist,
restoring the old constraint can be impossible. Downgrade fails transactionally
and leaves the new indexes intact. This failure is deliberate: never delete,
rewrite, reseal, or reactivate history to make downgrade succeed. The preflight
downgrade query identifies the conflicting old keys. Retain the new schema
while rolling back only to a compatible application release, or use an
explicitly reviewed restore plan with its data-preservation consequences.

## Conditional normal-runtime procedure

1. Finish disposable certification with genuine frozen job-2546 configuration,
   all prerequisite records, real constraints, and actual execution authority.
   The mandatory result is 447 new context-116 scores and exactly those IDs in
   a verified nonempty handoff. Retain all prior score/evidence/disposition
   fingerprints, including manifest 41, before and after. Resolve regressions
   and retain the complete implementation source inventory and test evidence.
2. Obtain separate approval for release publication and production recovery.
   Review the final uncommitted patch against `6d6952f`; retain `7e113f7` and
   `6d6952f`. Build a new isolated release with valid source qualification and
   configuration-delivery receipts. Do not copy the primary dirty checkout.
3. In an approved maintenance window, use the normal lifecycle stop/quiesce
   operation, verify no CERI writer owns a live lease, and take a verified
   restorable database backup. Record production preflight and financial
   fingerprints. Apply only migration 0096 through the normal guarded Alembic
   path, with the intended authoritative database identity explicitly verified.
4. Run postflight and source-qualification checks before activating the reviewed
   release through the normal lifecycle entry point. A missing receipt,
   conflicting index distribution, or fingerprint difference stops recovery.
5. Keep production pipeline 66, job 2546, context-113 exclusions and old score
   envelopes terminal and immutable. A future independent pipeline/capture
   requires its own explicit authorization and frozen context. This work does
   not create or authorize pipeline 67. Do not rebind context 116 to another
   production pipeline or set the dedicated context's unique pipeline FK.
6. For that separately authorized execution, verify the current population,
   owner and anchor, evidence seals, source cutoff, exact IDs through change,
   alert, barrier, dependency, and manifest, and all original historical hashes.
   Stop on empty/mixed membership or a failed dependency. Use the rollback
   analysis above before contemplating any application/schema reversal.

## Disposable reenactment boundary

### Exact normal lifecycle entry points for a separately approved window

The baseline's `docs/operations/lifecycle.md` and `swinglens.ps1` define these
commands. Run the first pair from the currently registered runtime checkout:

```powershell
pwsh -NoProfile -File .\swinglens.ps1 status -RuntimeMode NORMAL -Json
pwsh -NoProfile -File .\swinglens.ps1 stop -RuntimeMode NORMAL -Json
```

`ACTIVE_LEASE_BLOCKS_STOP`, an ambiguous process identity or a surviving worker
stops the procedure. Do not kill normal runtime PIDs, force job states, or launch
another supervisor to get around it. Retain the stopped-state evidence, verified
backup, read-only SQL preflight and separately approved clean release before
proceeding. The reviewed release must have only the expected Alembic head 0096.

Before normal workers are admitted, retain the following read-only queue check
as part of the separately approved window:

```sql
BEGIN READ ONLY;
SELECT id, job_type, status, related_run_id,
       payload_json->>'pipeline_run_id' AS pipeline_run_id,
       worker_id, lease_expires_at
FROM background_jobs
WHERE status IN ('QUEUED', 'RETRY', 'RUNNING', 'RECOVERING', 'STALLED')
  AND (job_type = 'FULL_PIPELINE' OR job_type LIKE 'CERI_%')
ORDER BY id;
ROLLBACK;
```

Normal startup admits workers, so an existing queued job or stale claim can
cause execution without a new enqueue command. Stop if that retained work is
outside the separately authorized recovery scope. Resolve it through the typed
normal-runtime control procedure; do not edit job statuses or financial rows
directly. In particular, this implementation authorizes no pipeline 67.

From that approved clean release, use the canonical guarded entry point:

```powershell
pwsh -NoProfile -File .\swinglens.ps1 start -RuntimeMode NORMAL -Json
pwsh -NoProfile -File .\swinglens.ps1 status -RuntimeMode NORMAL -Json
pwsh -NoProfile -File .\swinglens.ps1 diagnose -RuntimeMode NORMAL
```

Normal `start` verifies AUTHORITATIVE_LOCAL database identity before allowing
Alembic under the lifecycle and PostgreSQL advisory locks. Use that admission
path rather than an unguarded production `alembic upgrade`. Admit the maintenance
migration only after preflight verifies the parent is 0095 and the clean release
head is 0096; retain postflight and financial fingerprints before admitting any
new pipeline. `/ready/core` and the generation's database/schema/process receipts
must pass. These commands do not authorize reopening pipeline 66 or enqueueing a
new pipeline. No command in this section was executed during this task.

The disposable harness is a certification utility, not a normal-runtime recovery
command. Its operator resume must never be aimed at production.

### Genuine disposable authority

The certification utility targets only the named `swinglens_qa_ceri_...`
database, verifies it differs from production, and refuses an incomplete
constraint restore. It uses the retained configuration anchor and real
semantic authority of job 2546. It exercises an operator-resume transition
only on the disposable parent copy with existing ACTIVE authority, a new
independent workflow, a new dependency, and real worker claims. It does not
change the original failed job. A revoked or unavailable execution authority
is BLOCKED; the harness never manufactures ACTIVE authority.

The historical fixture copy is allowlisted and uses read-only production
connections. Every inserted prerequisite is a genuine source row; existing
target rows are never updated or deleted. Successful bounded batches are
committed with their audit hashes. Missing dependencies outside the authorized
scope stop subsequent copying; the fixture is uncertified until every required
constraint validates. No synthetic financial evidence
or default local configuration can replace a missing prerequisite.
