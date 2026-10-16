# Task 2.9.9 Winner Publication Integrity

## Verdict

**PASS.** The retained regression was a stale temporal test fixture, not a production publication defect.

`TASK 2.9.9 WINNER PUBLICATION: CLOSED`

## Timing

- Start: `2026-10-14T13:18:52.3201166+02:00`
- Finish: `2026-10-14T13:22:29.9865101+02:00`
- Elapsed: 3 minutes 37.666 seconds
- Budget: 20 minutes maximum
- Fresh baseline runs: 0
- Final certifications: 1 of 1 permitted

## Root cause and correction

The retained failure was `AttributeError: 'NoneType' object has no attribute 'id'` at the assertion that the journal reader returned the new clean latest-rescore estimate. The fixture queried `_serving_winner_estimate` with the fixed cutoff `2026-10-06T12:00:30Z`, while the clean candidate received its `created_at` from PostgreSQL when the test ran on 2026-10-14. The journal query correctly requires `created_at <= cutoff`. The candidate was therefore not yet visible at that cutoff, and the old rows were correctly excluded because publication had marked them `SUPERSEDED`.

The fixture now makes the temporal contract explicit. A cutoff one microsecond before the clean estimate's durable creation time returns no serving row, proving that an old superseded row is not resurrected. A cutoff one microsecond after creation returns the exact clean estimate. No production code, lifecycle rule, query predicate, or stored timestamp was changed.

## Publication contract and evidence

The authoritative transition is defined by the reviewed transition manifest and binds the exact target and previous generation IDs/keys, target root-manifest hash, candidate-manifest hash, original/candidate estimate IDs, prediction/outcome/kind lineage, frozen creation configuration, and durable publication request key.

Directly tested guarantees:

- Four injected failures—`generation_switch`, `estimate_switch`, `pointer_switch`, and `request_recorded`—are rolled back. After each, the previous generation remains `PUBLISHED`, the target remains `READY`, the original remains `PUBLISHED`, the replacement remains `CANDIDATE`, and there is no publication-request row.
- The successful transition publishes two reviewed candidates and supersedes exactly two originals in one caller transaction.
- Replaying the same request key and identical manifest returns the exact prior result and leaves one `WinnerEstimatePublicationRequest`.
- The serving API selects the exact clean decision and latest-rescore estimates.
- A cutoff before the clean estimate's birth returns no serving estimate; a cutoff after birth returns the exact clean latest-rescore row. No old row is resurrected.
- Original rows remain durable with `SUPERSEDED` lifecycle state.
- A duplicate replacement-lineage insert is rejected by PostgreSQL with `IntegrityError`.

Code-inspected guarantees retained unchanged:

- Publication locks both generations, both refresh states, and every exact original/candidate estimate.
- The service validates frozen configuration and retained generation/estimate authority before mutation.
- Candidate lineage must match the original prediction, outcome definition, estimate kind, target generation, supersession pointer, reconstruction category, and reviewed manifest.
- Publication does not delete estimate rows or evidence-member rows and does not substitute a latest model, generation, cohort, or run.

## Files

- `tests/integration/test_winner_jobs_reliability_postgresql.py` — corrected the journal cutoff, added the explicit pre-birth non-resurrection assertion, and bounded connection, statement, lock, and migration waits.
- `docs/remediation/readiness-phase2/TASK_2_9_9_CERTIFICATION.xml` — exact-node JUnit result.
- `docs/remediation/readiness-phase2/TASK_2_9_9_WINNER_PUBLICATION.md` — this report.

## Certification

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/integration/test_winner_jobs_reliability_postgresql.py::test_clean_estimate_publication_is_atomic_idempotent_and_never_resurrects_old_rows' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_9_CERTIFICATION.xml
```

Result: **1 passed, 1 warning in 14.41 seconds**. JUnit records 1 test, 0 failures, 0 errors, and 0 skips. The warning is the pre-existing Starlette/httpx deprecation.

## Safety and source pins

The exact test used a UUID-named disposable PostgreSQL database guarded by `DISPOSABLE_TEST`; the fixture force-dropped only that database. All identities were synthetic. No normal database, historical Run 22–33, live provider, worker, browser, or E2E process was accessed. The migration subprocess has a 120-second deadline, and the target uses finite connection, statement, and lock limits. No credentials were written to JUnit.

No production source or registered mutation boundary changed. The official validator returned:

```json
{"stale": []}
```

