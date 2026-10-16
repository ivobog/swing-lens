# Task 2.9.8 Model Retirement API Governance

## A. Verdict

**PASS.** The retained regression was caused by an outdated test request, not by a production model-governance defect. The test now supplies a real local-admin request to the centrally guarded route and retains strict positive and negative governance assertions. The exact regression passes.

`TASK 2.9.8 MODEL RETIREMENT GOVERNANCE: CLOSED`

This verdict closes Task 2.9.8 only. It does not certify the complete Phase 2.9 release.

## B. Duration

- Start: `2026-10-14T13:07:09.4377986+02:00`
- Finish: `2026-10-14T13:11:53.3133039+02:00`
- Elapsed: 4 minutes 43.876 seconds
- Budget: 20 minutes maximum; 15-minute target met
- Fresh baseline runs: 0; retained failure evidence was sufficient
- Post-fix integration certifications: 1 of 1 permitted

## C. Root cause

The retained failure occurred when the test expected the decorated public retirement route to return its success payload for model key `t14d-native-admin-governance`. Instead, the route raised HTTP `403` with:

```json
{"code": "ADMIN_FORBIDDEN", "message": "This mutation is restricted to the local administrator."}
```

The test passed a `SimpleNamespace` whose client host was `native-test` and monkeypatched only the route's inner `_require_local_admin` guard. The route is also protected by the centralized `unsafe_route(..., local_admin_required=True)` wrapper. That wrapper correctly rejected the non-Starlette, non-local request before the handler ran. The old fixture therefore no longer represented the supported public API contract.

Production behavior was correct and remained unchanged. The smallest remediation was to use the module's real local-admin `Request` fixture for the permitted call and to prove that a real remote request is rejected by the outer guard. The successful response contract remains `{"model": ..., "lifecycle_event_id": ...}` (HTTP 200 when served through FastAPI); structured domain failures retain their established HTTP mappings.

## D. Model governance behavior

| Invariant | Before | After |
|---|---|---|
| Retirement eligibility | The production registry permitted the fixture's `SHADOW` model to retire. An `ACTIVE` model that is the only active model is blocked without an active fallback unless the specifically authorized override is present. The stale request fixture could not reach this contract through the public guard. | Unchanged production policy. The test proves `SHADOW -> RETIRED`, rejects an operation before model birth, and rejects missing or naive operation time while retaining `SHADOW`. |
| Target model/version identity | The service and registry already required the exact durable `model_id`; no latest/current fallback existed. | The target event is asserted against the exact fixture-owned `model_id`; all unrelated model IDs retain their status, retirement time, and artifact hash. Missing ID `987654321` fails closed. |
| Successful retirement response | Direct service retirement returned `model` plus `lifecycle_event_id`. The stale synthetic route request was rejected before handler execution. | A real local request reaches the decorated route and returns the established payload with target status `RETIRED`; the mounted route's default success status is HTTP 200. |
| Rejected operation response | The retained route call produced HTTP 403 `ADMIN_FORBIDDEN`, contrary to the fixture's intended authorized-call setup. Domain validation already mapped mutation conflicts to 409 and a missing model to 404. | A remote request is explicitly expected to produce HTTP 403 `ADMIN_FORBIDDEN`; a missing model produces HTTP 404 `MODEL_NOT_FOUND`; temporal and authority mutations retain their exact 409 codes. |
| Rejected operation persisted state | Existing rollback assertions covered invalid time and altered model identity, but the centralized authorization rejection had no explicit persistence proof. | The remote rejection leaves the target `RETIRED` and creates no lifecycle event. Invalid time rolls back a staged unrelated row, and altered identity rolls back to the retained model key. |
| Historical evidence preservation | Production retirement did not rewrite prediction evidence, but the target lacked a before/after historical-evidence snapshot. | Every retained estimate's ID, prediction ID, model-version ID, evidence-manifest ID/hash, and lifecycle status is identical before and after retirement and all rejection paths. |
| Retirement audit lineage | The test asserted only the returned model status. | The initial event is bound to the exact target and records `RETIRED`, `SHADOW -> RETIRED`. The repeated authorized request records `RETIRED -> RETIRED` for the same target. |
| Idempotency | The established policy allowed a repeated retirement to retain terminal state and append an auditable repeated-retirement event. | That policy is now explicit: the second authorized request keeps the model `RETIRED` and records an exact-target `RETIRED -> RETIRED` event. Rejected requests append nothing. |

The registry continues to lock and resolve the exact requested model. No replacement is inferred, no active-model substitution is performed, and no concurrency mechanism was changed.

## E. Files changed

- `tests/integration/test_t14d_caller_postgresql.py` — replaced the stale synthetic retirement request with the existing real local-admin request; added remote authorization rejection, exact audit lineage, unrelated-model immutability, historical-evidence immutability, missing-model code, rollback, and repeated-retirement assertions.
- `docs/remediation/readiness-phase2/TASK_2_9_8_CERTIFICATION.xml` — single exact-node JUnit result.
- `docs/remediation/readiness-phase2/TASK_2_9_8_MODEL_RETIREMENT_GOVERNANCE.md` — this certification report.

No production source, schema, migration, model policy, route mapping, or historical certificate was changed for Task 2.9.8. Existing unrelated working-tree changes were preserved.

## F. Tests

Pre-certification checks:

```powershell
.\.venv\Scripts\python.exe -m py_compile tests\integration\test_t14d_caller_postgresql.py
git diff --check -- tests/integration/test_t14d_caller_postgresql.py
```

Both completed without error.

The one permitted post-fix integration certification was:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/integration/test_t14d_caller_postgresql.py::test_model_retirement_api_delivers_exact_new_governance_and_maps_rejections' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_8_CERTIFICATION.xml
```

Result: **1 passed, 4 warnings in 22.59 seconds**. JUnit records 1 test, 0 failures, 0 errors, and 0 skips. The warnings are one Starlette/httpx deprecation, two Alembic path-separator deprecations, and the pre-existing SQLAlchemy mutually dependent foreign-key sort warning.

Artifact: `docs/remediation/readiness-phase2/TASK_2_9_8_CERTIFICATION.xml`

No baseline, broad suite, model-governance suite, browser test, or E2E process was started.

## G. Source pins

Task 2.9.8 changed no reviewed production source and introduced no mutation boundary, so no pin regeneration was required. The official validator command was:

```powershell
.\.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins
```

Final result:

```json
{"stale": []}
```

Affected reviewed addresses: none.

## H. Safety

- Branch: `codex/ceri-authority-remediation`
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`
- The test uses the `contextual_engine` fixture, which creates an isolated disposable PostgreSQL database, applies its test migrations, and disposes the engine during fixture cleanup.
- Only synthetic test-owned pipeline, outcome, model, audit, and evidence rows were used. The normal SwingLens application database and historical Runs 22–33 were not accessed or modified.
- No live provider, network-backed market-data source, pipeline, worker, browser, or E2E process was invoked.
- The target owns no background worker process. Pytest completed normally and emitted no leaked-resource failure.
- The JUnit artifact contains the test identity and timing only; no credentials or secrets were emitted.
- No real model was retired, deleted, replaced, or mutated.

## I. Remaining blockers

Current Task 2.9.8 blockers: **none**.

Unrelated Phase 2.9 findings remain outside this task. This report makes no claim that the full release is ready.

## J. Next recommended single task

Task 2.9.9: remediate and certify the remaining Winner clean-estimate publication/idempotency regression. Task 2.9.9 was not started.
