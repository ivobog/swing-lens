# Tasks 2.9.9 and 2.9.10 Summary

## A. Executive verdict

| Task | Root cause | Fix type | Certification | Verdict |
|---|---|---|---|---|
| 2.9.9 Winner publication | Fixed cutoff preceded the clean estimate's database creation time; the point-in-time reader correctly returned no row | Fixture | 1 passed in 14.41 s | CLOSED |
| 2.9.10 Concurrent maturation | Two local clients omitted the required CSRF token and correctly failed authorization with HTTP 403 | Fixture | 1 passed in 10.60 s | CLOSED |

`TASK 2.9.9 WINNER PUBLICATION: CLOSED`

`TASK 2.9.10 WINNER CONCURRENT ADMISSION: CLOSED`

These verdicts close only Tasks 2.9.9 and 2.9.10. They do not certify the complete Phase 2.9 release.

## B. Timing

| Task | Start | Finish | Elapsed | Budget |
|---|---|---|---:|---:|
| 2.9.9 | `2026-10-14T13:18:52.3201166+02:00` | `2026-10-14T13:22:29.9865101+02:00` | 3m 37.666s | 20m |
| 2.9.10 | `2026-10-14T13:20:35.2208677+02:00` | `2026-10-14T13:23:01.0786971+02:00` | 2m 25.858s | 20m |

The clocks overlap because the mandated initial failure and shared-source inspection covered both tasks. Overall elapsed time was 4 minutes 8.759 seconds against the 40-minute combined budget. Neither task needed a fresh baseline; each used its one permitted final certification.

## C. Task 2.9.9

The retained assertion dereferenced `journal.id`, but the journal result was `None`. The fixture's fixed journal cutoff was earlier than the PostgreSQL-assigned `created_at` of the clean estimate. Production's exact point-in-time predicate and serving lifecycle predicate were both correct.

Only `tests/integration/test_winner_jobs_reliability_postgresql.py` changed: the cutoff is now derived from the persisted clean estimate's creation time, and the test explicitly proves that a pre-birth cutoff returns no row rather than resurrecting a superseded estimate.

Publication authority remains the reviewed manifest plus exact generation, previous generation, candidate-manifest, original/candidate estimate, prediction, outcome, estimate-kind, frozen configuration, and request-key identities. Four injected failure points prove caller rollback leaves the entire transition unapplied. Successful replay returns the stored result with one publication-request row. PostgreSQL rejects duplicate supersession lineage. Old estimate records remain present but non-serving; evidence-member records are not deleted or rewritten by the inspected publication path.

Command and result:

```text
.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_winner_jobs_reliability_postgresql.py::test_clean_estimate_publication_is_atomic_idempotent_and_never_resurrects_old_rows' -q --tb=short --junitxml=docs/remediation/readiness-phase2/TASK_2_9_9_CERTIFICATION.xml
1 passed, 1 warning in 14.41s
```

## D. Task 2.9.10

The retained assertion expected HTTP 200 but received HTTP 403 because both clients omitted the CSRF token required by the local-admin maturation route. The fixture now supplies the real per-app token to two separate `TestClient` instances and retains a barrier directly in front of the real enqueue call.

Only `tests/integration/test_winner_jobs_reliability_postgresql.py` changed. Both clients use independent Sessions and the same route/limit scope. Their manual cycle request keys can differ, but the durable logical single-flight identity is `winner:h5-next-open:maturation`. PostgreSQL advisory transaction lock `15150008` serializes semantic admission; the partial unique active-workflow index is the durable backstop.

Actual API outcomes: both HTTP 200, identical job/root ID, one `coalesced=false`, one `coalesced=true`, both `QUEUED`. Persisted counts: one maturation `BackgroundJob`, zero processing runs, zero forward outcomes, and zero probability estimates. The one job is its own root, has no parent, depth zero, and `MANUAL` trigger source. Thus the concurrent admission creates neither an orphan nor downstream duplicate publication.

Command and result:

```text
.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_winner_jobs_reliability_postgresql.py::test_two_concurrent_api_clients_receive_one_maturation_workflow' -q --tb=short --junitxml=docs/remediation/readiness-phase2/TASK_2_9_10_CERTIFICATION.xml
1 passed, 1 warning in 10.60s
```

## E. Cross-task invariants

| Invariant | Evidence |
|---|---|
| Winner publication immutability | Direct: original estimate rows remain and become `SUPERSEDED`; no pre-birth fallback resurrects them. Inspected: publication updates lifecycle/pointers only and does not delete estimate/evidence-member history. |
| No duplicate estimates | Direct: same-request replay leaves one publication request, exact clean IDs serve, and duplicate supersession lineage raises `IntegrityError`. |
| No resurrection | Direct: the journal returns `None` before the clean row's creation and the clean latest row after it; old rows never re-enter serving state. |
| One maturation workflow per logical scope | Direct: two concurrent clients return one job/root and PostgreSQL retains one maturation job. Inspected: advisory transaction lock plus partial unique workflow index enforce the same rule across processes/restarts. |
| Correct model/version identity | Direct for publication: exact original/candidate IDs and API estimate kinds are asserted. Inspected: candidate authority validates stored creation configuration and exact generation/lineage; no latest-model substitution exists in this path. |
| Frozen configuration and operation cutoff | Inspected: publication validates retained estimate/generation configuration; maturation admits configuration identity and operation cutoff while holding the advisory lock, and coalesced callers receive the admitted job. |
| Worker ownership and fencing compatibility | Direct: no worker or processing run executes during admission. Inspected: no worker lease, generation, token, recovery, root, or continuation production code changed. |
| API authorization and rejection behavior | Direct: two clients authenticate with the real local-admin CSRF contract and receive 200. Retained evidence proves missing tokens fail closed with 403. No guard was bypassed. |

The tasks share only their integration test module. They do not share a modified production service. Both certifications ran after both fixture corrections, so they certify the same final source state.

## F. Source pins

No reviewed production source or operation family changed. A new mutation boundary was not introduced. The official validation command returned:

```json
{"stale": []}
```

Affected reviewed sources: none.

## G. Safety

- Branch: `codex/ceri-authority-remediation`
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`
- Existing dirty-worktree changes from Tasks 2.9.1–2.9.8 and user work were preserved.
- Both tests used the UUID-named, safety-asserted disposable PostgreSQL fixture and force-dropped only their test databases.
- The normal SwingLens database and historical Runs 22–33 were not accessed or mutated.
- All Winner identities were synthetic and task-owned.
- No IBKR, SEC, EODHD, live provider, normal pipeline, browser, or E2E process ran.
- Task 2.9.10 disabled the background worker; Task 2.9.9 started no worker.
- Migration, connection, statement, lock, and concurrency-barrier waits are finite.
- Test Sessions and engines closed normally; fixture cleanup completed without leaked-resource failures.
- JUnit artifacts contain test identity and timing only; no credential was emitted.
- No reset, stash, discard, merge, push, or deployment occurred.

## H. Remaining blockers

- Task 2.9.9 blockers: none.
- Task 2.9.10 blockers: none.
- Other Phase 2.9 findings remain outside these verdicts; no complete-release claim is made.

## I. Recommended next task

Task 2.9.11: the deterministic pipeline 10/25/100 regression. It was not started.
