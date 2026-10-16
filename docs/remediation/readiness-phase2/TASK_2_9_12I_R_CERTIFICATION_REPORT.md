# Task 2.9.12i-R — Historical v1/v2 Anchor Compatibility Certification Recovery

## 1. Executive verdict

**PASS.** The repository virtual environment passed dependency preflight, both exact focused
PostgreSQL nodes passed, their positive and negative assertions executed, and T14 source-pin
validation returned no stale sources.

`TASK 2.9.12I HISTORICAL ANCHOR COMPATIBILITY: CLOSED`

This closes only historical v1/v2 run-start anchor compatibility. Chromium-100 and complete
Phase 2.9 release certification were not executed.

- Start: `2026-10-14T22:39:28.7088687+02:00`
- Deadline: `2026-10-14T22:59:28.7088687+02:00`
- Certification/cleanup finish: `2026-10-14T22:40:49.3578235+02:00`
- Report finish: `2026-10-14T22:42:04.3025743+02:00`
- Branch: `codex/ceri-authority-remediation`
- Baseline commit: `6656c55dd59c294c2ea471571863046e02bf7c35`

## 2. Environment preflight

Resolved interpreter:

```text
C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe
```

The required imports `pytest`, `prometheus_client`, `sqlalchemy`, `alembic`, and `psycopg`
all succeeded and printed:

```text
DEPENDENCY_PREFLIGHT_PASS
```

No package was installed and system Python was not used as a fallback. The existing dirty
working tree, production implementation, compatibility tests, prior report, branch, and commit
were inspected and preserved.

The selected fixture creates UUID-suffixed `swinglens_pytest_*` databases, verifies that the
candidate is not the active SwingLens database, sets the disposable-test safety context, and
forces database deletion in `finally`.

## 3. Exact focused certification

Exactly one pytest invocation was executed:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/integration/test_transition_preflight_plan_postgresql.py::test_two_phase_manifest_accepts_committed_run_local_artifacts_and_is_idempotent' `
  'tests/integration/test_transition_preflight_plan_postgresql.py::test_historical_v1_anchor_is_verified_without_rewrite_and_fails_closed' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12I_R_CERTIFICATION.xml
```

Result:

- Native exit code: `0`
- Tests: `2`
- Passed: `2`
- Failed: `0`
- Errors: `0`
- Skipped: `0`
- Pytest-reported duration: `17.54s`
- JUnit suite duration: `17.526s`
- Outer process wall time: `26.053s`
- Warnings: three dependency/configuration deprecations; no test warning changed the verdict

Native JUnit was preserved at `TASK_2_9_12I_R_CERTIFICATION.xml`.

## 4. Executed positive assertions

### V2 legitimate continuation

`test_two_phase_manifest_accepts_committed_run_local_artifacts_and_is_idempotent` passed in
`9.240s`. It executed these assertions:

- New preflight writes `transition-run-start-anchor-v2`.
- V2 omits only `run.status` and `run.processed_at` from the run payload.
- Run-start source lineage is frozen before downstream artifact production.
- The real pipeline start consumes the plan and preserves context binding.
- The decision handoff is frozen after committed upstream artifacts.
- Lifecycle bookkeeping advances without invalidating V2.
- Setup pre-mutation verification succeeds.
- Handoff reuse returns the same manifest and fingerprint.
- Exactly one handoff manifest exists.

### V1 legitimate continuation

`test_historical_v1_anchor_is_verified_without_rewrite_and_fails_closed` passed in `7.907s`.
It executed these assertions:

- V1 is built by the production builder as a genuinely canonical V1 payload, not a relabelled
  V2 document.
- The V1 payload includes its historical `run.status` and `run.processed_at` keys.
- Pipeline start accepts the canonical V1 plan.
- The legitimate handoff is created and remains bound to the correct pipeline and V1 fingerprint.
- Changing only `run.status` and `run.processed_at` does not invalidate immutable source evidence.
- Handoff reuse and Setup pre-mutation verification succeed after lifecycle drift.
- The original stored V1 JSON and fingerprint remain byte-semantically unchanged.
- No duplicate handoff manifest is introduced.

## 5. Executed negative assertions

The V1 test executed every listed rejection through production verification paths:

| Case | Executed result |
|---|---|
| Protected raw-source mutation | Rejected with `DECISION_MANIFEST_MISMATCH` |
| Frozen context mutation | Rejected with `DECISION_MANIFEST_MISMATCH` |
| Persisted JSON tamper without matching fingerprint | Rejected with `DECISION_MANIFEST_MISMATCH` |
| Unsupported contract identifier | Rejected with `MALFORMED_DECISION_MANIFEST` |
| Missing anchor JSON/fingerprint | Rejected with `MALFORMED_DECISION_MANIFEST` |
| V2-shaped payload labelled V1 | Rejected with `MALFORMED_DECISION_MANIFEST` |
| Wrong upload-run binding | Rejected with `STALE_PREFLIGHT` |

The V2 test separately executed a real raw-source mutation rejection. Every negative case asserted
zero `SetupSignalSnapshot` publication. V1 negative transactions were rolled back, the durable V1
anchor/fingerprint remained unchanged, and the existing handoff fingerprint was preserved.
An attempted downstream handoff mutation also retained the existing immutable-evidence rejection.

## 6. Historical anchor and authority conclusions

- V1 and V2 dispatch is explicit from the stored contract identifier.
- A stored hash alone is insufficient: the verifier first authenticates the exact stored payload,
  then independently compares its immutable projection with current authoritative source evidence.
- V1 permits drift only in `run.status` and `run.processed_at`.
- No historical anchor is upgraded, replaced, or rewritten.
- Unknown, missing, forged, and structurally confused contracts fail closed.
- Anchor validity does not bypass upload-run, pipeline, handoff, or Setup publication authority.
- No unauthorized partial publication occurred.

## 7. T14 source-pin validation

No production or test file was changed by this recovery task. The official validator was run
without regeneration:

- Retained production source blob: `52026a2ba4bb0a8d53bb8454b487469c20e2ba65`
- Retained focused test blob: `a1f881c440ec48bf4d5ccb23f77bd9702840bcbc`

```powershell
.\.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins
```

Result:

```json
{"stale": []}
```

## 8. Cleanup and isolation

- Both tests used empty disposable PostgreSQL databases guarded by active-database identity checks.
- Successful pytest teardown with zero errors confirms that both fixture-owned forced-drop actions
  completed without raising.
- No repository-venv pytest/test Python process remained after the run.
- No test-owned listener remained after the run.
- A post-run read-only admin inventory found 36 older or otherwise unattributed databases with a
  `swinglens_pytest_*` prefix. Their names were not emitted by this focused run, they are not needed
  for this certification, and they were not deleted because destructive cleanup outside this run
  was not authorized.
- No normal application database was selected by the fixture.
- Historical Runs 22–33 were not accessed or mutated.
- No IBKR, SEC, EODHD, or live CERI provider was contacted.
- No Chromium, browser, live pipeline, full suite, installation, merge, reset, stash, discard,
  commit, push, or deployment occurred.

## 9. Evidence distinction

The prior `TASK_2_9_12I_CERTIFICATION.xml` records the original system-Python collection error and
remains intact. This recovery produced a separate native passing JUnit file and a separate executed
compatibility matrix. No previous Task 2.9.12i evidence was overwritten.

## 10. Remaining release blockers

1. Tasks 2.9.12a/d/e remain open until a complete Chromium-100 run passes.
2. `TASK 2.9.12J FINAL CHROMIUM-100 CERTIFICATION` is the recommended next task, but was not started.
3. `FULL PHASE 2.9 RELEASE CERTIFICATION: NOT YET EXECUTED`.

`TASK 2.9.12I HISTORICAL ANCHOR COMPATIBILITY: CLOSED`

