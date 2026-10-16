# Task 2.9.11 Deterministic Pipeline Recovery Certification

## A. Executive verdict

**PASS.** The full combined 10/25/100 target passed with deterministic offline provider boundaries, exact per-population counts, bounded execution, and the retained recovery/idempotency checks.

`TASK 2.9.11 DETERMINISTIC PIPELINE: CLOSED`

This closes Task 2.9.11 only. It does not certify the complete Phase 2.9 release.

## B. Timing

- Start: `2026-10-14T14:31:06.2363235+02:00`
- Deadline: `2026-10-14T14:51:06.2363235+02:00`
- Finish: `2026-10-14T14:40:06.2470683+02:00`
- Elapsed: 9 minutes 0.011 seconds
- Budget: 20 minutes maximum; 15-minute target met
- Fresh baseline runs: 0; retained failure evidence was sufficient
- Post-fix certifications: 1 of 1 permitted

## C. Root cause

The retained failure occurred in the first population, 10 tickers. No earlier population had passed because 10 is the first case in the combined loop.

The visible traceback was:

```text
TypeError: unsupported operand type(s) for -: 'NoneType' and 'NoneType'
technical_span["end"] - technical_span["start"]
```

That subtraction masked the actual failure. The fixture supplied a retained zero-request `FetchPlan` and synthetic price evidence, but left `PipelineExecutionDependencies.check_ib_gateway` at its production default. `execute_full_pipeline` performs IB health admission before it resolves the retained plan. The default `check_ib_gateway_status` called `ib_gateway_health_service.check_status`, which constructed an IB client and attempted `ib.client.connect` to the configured loopback endpoint. The connection was refused, the `REQUIRE_IB` pipeline failed in `FETCHING_MARKET_DATA` with `IB_GATEWAY_UNAVAILABLE`, and the Technical adapter never populated either span timestamp.

This was a **fixture/provider-isolation defect**, not a production recovery defect. Production correctly enforces mandatory IB health for `REQUIRE_IB`. The fixture now injects deterministic ready health evidence before execution and fail-fast sentinels for historical-capability and fetch-execution entrypoints. The timing diagnostic now emits an explicit pipeline-status/failure-reason assertion if Technical is not reached.

No production pipeline behavior was changed.

## D. Provider isolation

The target is offline by construction:

- IB Gateway health is supplied by a deterministic in-memory dependency with fixed status `IB_API_READY` and fixed checked time `2026-09-24T13:19:59Z`; it constructs no IB client.
- IB historical-capability and IB fetch-execution dependencies are fail-fast sentinels. Any invocation fails the test immediately.
- Every population retains a zero-request fetch plan and reports exactly one deterministic health-boundary call, zero historical-capability calls, and zero fetch-execution calls.
- All price frames are deterministic rows inserted into the disposable database. `source="IBKR"` is provenance for synthetic retained evidence, not a provider invocation.
- SEC preflight is an injected deterministic completion callback; no SEC client is constructed.
- CERI provider ingestion is disabled before pipeline construction. Scheduling and snapshot capture use deterministic injected callbacks.
- The exercised path contains no EODHD call. Fundamental input comes from the checked-in `financial_inputs.json` fixture, and all market inputs are test-seeded.
- Worker/retry execution reuses the same injected pipeline dependencies or the already persisted synthetic inputs; there is no fallback that reconstructs a live provider.

The certification completed without triggering any fail-fast provider sentinel and without connection-refused output.

## E. Population results

| Population | Input rows | Expected status | Actual status | Evidence integrity | Provider isolation | Result |
|---|---:|---|---|---|---|---|
| 10 | 10 | `COMPLETED`; 10 visible and 10 Technical scores | `COMPLETED`; 10 visible, 10 scores, 0 incomplete, 0 warnings | 31 source-validation queries; 10 exact run-owned scores; stable `No trade` classification | health stub 1; historical 0; fetch 0 | PASS |
| 25 | 25 | `COMPLETED`; 25 visible and 25 Technical scores | `COMPLETED`; 25 visible, 25 scores, 0 incomplete, 0 warnings | 53 source-validation queries; 25 exact run-owned scores; stable `No trade` classification | health stub 1; historical 0; fetch 0 | PASS |
| 100 | 100 | `COMPLETED`; 100 visible and 100 Technical scores; retry remains 100 | `COMPLETED`; 100 visible, 100 scores, retry count 100, 0 incomplete, 0 warnings | 203 source-validation queries; idempotent exact retry; stable `No trade` classification | health stub 1; historical 0; fetch 0 | PASS |

Observed performance and control evidence:

| Population | Pipeline seconds | Technical seconds | Maximum checkpoint gap | Checkpoints | Manifest bytes | Maximum score payload |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 30.065846 | 14.381819 | 6.298152 s | 49 | 693,947 | 9,881 bytes |
| 25 | 28.160304 | 27.039973 | 6.320859 s | 41 | 1,610,116 | 9,894 bytes |
| 100 | 112.464322 | 108.731807 | 51.521249 s | 70 | 6,163,181 | 9,898 bytes |

The 100-row pure-kernel retry took 46.657067 seconds. All source-query, payload, manifest, 60-second checkpoint-gap, 300-second 100-row Technical, and 900-second per-population assertions passed. Each population executed the established stage order: Fundamental, Market handoff, Technical, Market Regime, Combined, Ranking, Sector, CERI, Setup, Lifecycle, Alerts, and Winner.

The 10-row case exercises the production Fundamental, Market Regime, Combined, Ranking, Sector, Technical, and PostgreSQL evidence paths. The 25/100 scale cases intentionally keep the production Technical/PostgreSQL path while using the test's existing deterministic downstream orchestration. This report does not claim unexecuted provider or downstream persistence behavior.

## F. Recovery authority

- **Frozen C1 configuration:** `resolve_pipeline_configurations` is evaluated before each pipeline is bound. `bound_pipeline` persists that configuration anchor, and the 100-row exact retry explicitly reuses `configurations["core.technical"]`; it does not resolve mutable current configuration again.
- **Calculation context and PIT cutoff:** all populations use the fixed cutoff `2026-09-24T13:19:59Z`, session `2026-09-23`, their bound `MarketCalculationContext`, and their own frozen scope, refresh-cycle, and acquisition-plan IDs. Visibility is re-read through that exact context.
- **Universe isolation:** run IDs 1010, 1011, and 1012 own distinct frozen scopes and ticker memberships. Exact requested, visible, and score counts prove no cross-population leakage.
- **Worker identity and token:** every synthetic control job uses worker `recovery-test`, a population-specific `recovery-owner-<size>` execution token, and a 900-second lease. Progress callbacks heartbeat and record progress with that same persisted token. No lease expired; the largest checkpoint gap was 51.521249 seconds.
- **Retry/idempotency:** the 100-row retry uses the same run, pipeline, cutoff, configuration, and ticker universe. The final count remains 100, proving no duplicate Technical publication.
- **Superseded roots and worker generations:** this target does not perform operator resume, superseded-root selection, or real worker re-registration. Those guarantees remain code-unchanged and are covered by Tasks 2.9.4b, 2.9.5e, 2.9.6, and 2.9.7. No production authority predicate was bypassed or modified here.
- **Duplicate/orphan checks:** the target creates one synthetic root control job per isolated population and no child/continuation job. It starts no durable worker process. All Sessions and the engine are closed before the disposable database is force-dropped.

## G. Files changed

- `tests/recovery/test_whole_application_recovery.py` — injected deterministic/fail-fast provider boundaries, added bounded database waits, recorded per-population JUnit evidence, asserted exact provider-call counts, and replaced the masked Technical timing error with a precise failure.
- `docs/remediation/readiness-phase2/TASK_2_9_11_CERTIFICATION.xml` — exact combined-node JUnit result and per-population measurements.
- `docs/remediation/readiness-phase2/TASK_2_9_11_DETERMINISTIC_PIPELINE.md` — this report.

No production source, schema, migration, financial algorithm, lifecycle policy, worker authority, Winner governance, or historical certificate changed.

## H. Tests

Pre-certification checks:

```powershell
.\.venv\Scripts\python.exe -m py_compile tests\recovery\test_whole_application_recovery.py
git diff --check -- tests/recovery/test_whole_application_recovery.py
.\.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins
```

All passed.

The single post-fix certification was:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  'tests/recovery/test_whole_application_recovery.py::test_deterministic_pipeline_10_25_100' `
  -q --tb=short `
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_11_CERTIFICATION.xml
```

Result: **1 passed, 5 warnings in 333.87 seconds**. JUnit records 1 test, 0 failures, 0 errors, and 0 skips. Its three `population_*` properties contain the observed values reported above. Warnings comprise the pre-existing Starlette/httpx deprecation, two Alembic path-separator deprecations, the known SQLAlchemy cyclic-FK sort warning, and pytest's xUnit2 compatibility warning for `record_property`; the properties are present in the generated artifact.

No baseline, broad suite, browser test, E2E test, live-provider canary, or normal pipeline was run.

## I. Source pins

Only test fixture source changed. No reviewed production or probe address and no operation family changed. The official validator required no regeneration and returned:

```json
{"stale": []}
```

Affected reviewed addresses: none.

## J. Safety

- Branch: `codex/ceri-authority-remediation`
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`
- The factory created one UUID-named database under the required disposable prefix, asserted its disposable identity, set `SWINGLENS_DATABASE_SAFETY_CONTEXT=DISPOSABLE_TEST`, and force-dropped only that database after engine disposal.
- The normal SwingLens database, normal worker state, and historical Runs 22–33 were not accessed or modified.
- No existing process was stopped or altered. No IB Gateway/TWS process was launched.
- Connection timeout was 10 seconds, lock timeout 10 seconds, and statement timeout 300 seconds. Pipeline and retry cancellation deadlines remained finite at 900 seconds, with stricter tested performance limits.
- Test-owned Sessions and engine closed normally; no orphaned process, worker, Session, listener, or database remained.
- JUnit contains deterministic measurements only; no credential, database URL, secret, or provider token was emitted.
- All pre-existing dirty-worktree changes were preserved. No reset, stash, discard, merge, push, or deployment occurred.

## K. Remaining blockers

Current Task 2.9.11 blockers: **none**.

Other browser/E2E findings remain outside this task. This report does not claim complete Phase 2.9 release readiness.

## L. Next recommended task

Task 2.9.12: the next retained browser/E2E regression. Task 2.9.12 was not started.
