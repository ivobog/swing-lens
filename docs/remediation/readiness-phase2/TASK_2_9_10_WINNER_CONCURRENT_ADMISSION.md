# Task 2.9.10 Winner Concurrent Admission

## Verdict

**PASS.** The retained regression was a stale authorization fixture, not a production single-flight defect.

`TASK 2.9.10 WINNER CONCURRENT ADMISSION: CLOSED`

## Timing

- Start: `2026-10-14T13:20:35.2208677+02:00`
- Finish: `2026-10-14T13:23:01.0786971+02:00`
- Elapsed: 2 minutes 25.858 seconds
- Budget: 20 minutes maximum
- Fresh baseline runs: 0
- Final certifications: 1 of 1 permitted

The Task 2.9.10 clock overlaps the shared retained-evidence and compatibility inspection required before either final certification.

## Root cause and correction

The retained test expected HTTP 200 from two local clients but received HTTP 403. The maturation route intentionally requires both loopback local-admin authority and a valid `x-csrf-token` at its centralized `unsafe_route` guard and its route-level guard. The two clients were local but sent no CSRF token, so both were correctly rejected before workflow admission.

The fixture now reads the per-app token from `app.state.local_admin_csrf_token` and supplies it independently from each `TestClient`. Authorization was not monkeypatched or weakened. The existing barrier remains immediately before the real admission function, now with a 30-second deadline, so the clients are genuinely concurrent at the production boundary.

## Concurrent admission contract and evidence

Both clients submit the same authorized `POST /api/winner-probability/outcomes/process?limit=500` logical scope through separate clients and independent SQLAlchemy Sessions. The first admission creates the global primary-H5 maturation workflow; the second coalesces to it.

Directly tested guarantees:

- Both authorized clients receive HTTP 200.
- Both responses return the same durable job ID and `winner:h5-next-open:maturation` workflow key.
- Exactly one response reports `coalesced=false` and one reports `coalesced=true`.
- Both report `QUEUED`, with `root_job_id == job_id`.
- PostgreSQL contains exactly one maturation job: it is its own root, has no parent, has continuation depth zero, and records `MANUAL` trigger source.
- There are zero `WinnerProcessingRun`, `WinnerForwardOutcome`, and `WinnerProbabilityEstimate` rows, proving admission did not execute or duplicate downstream publication.
- The test uses a finite 30-second concurrency barrier, 10-second connection timeout, 10-second lock timeout, 60-second statement timeout, and 120-second migration timeout.

Code-inspected guarantees retained unchanged:

- PostgreSQL transaction advisory lock `15150008` serializes semantic maturation admission across independent Sessions/processes before operation cutoff, configuration identity, and cycle scope are admitted.
- The first job persists the frozen operation cutoff and configuration authority in its payload/binding; a coalesced caller receives that authoritative job rather than constructing a second scope.
- Partial unique index `uq_background_jobs_active_winner_maturation_workflow` durably permits at most one active maturation workflow key and is the database backstop across retries and restarts.
- The nested insertion boundary resolves a uniqueness race to the already admitted job; a losing request does not retain a partial job.
- The constraint is scoped to active `WINNER_OUTCOME_MATURATION` rows, so it does not globally suppress unrelated workflow families or terminal-history admission.
- Worker generation, execution-token, recovery, root/continuation, and cancellation rules were not modified.

## Files

- `tests/integration/test_winner_jobs_reliability_postgresql.py` — supplied the real CSRF token to each independent client, bounded concurrency/database/migration waits, and added exact response, root-lineage, job-count, and zero-publication assertions.
- `docs/remediation/readiness-phase2/TASK_2_9_10_CERTIFICATION.xml` — exact-node JUnit result.
- `docs/remediation/readiness-phase2/TASK_2_9_10_WINNER_CONCURRENT_ADMISSION.md` — this report.

## Certification

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/integration/test_winner_jobs_reliability_postgresql.py::test_two_concurrent_api_clients_receive_one_maturation_workflow' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_10_CERTIFICATION.xml
```

Result: **1 passed, 1 warning in 10.60 seconds**. JUnit records 1 test, 0 failures, 0 errors, and 0 skips. The warning is the pre-existing Starlette/httpx deprecation.

## Safety and source pins

The test used a UUID-named disposable PostgreSQL database guarded by `DISPOSABLE_TEST` and dropped during fixture cleanup. The background worker was disabled; admission created only a synthetic queued job and invoked no live provider or normal pipeline. The normal database and historical Runs 22–33 were not accessed. No credential appeared in JUnit.

No production source or registered mutation boundary changed. The official validator returned:

```json
{"stale": []}
```

