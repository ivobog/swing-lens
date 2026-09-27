# Path test-coverage matrix

Coverage means a test explicitly asserts the path's authority, state, isolation, or idempotency invariant. A nearby happy-path test does not count. No tests were executed for this read-only audit. Untracked working-tree tests are identified as such and are not treated as committed regression protection.

## Execution paths

| Path ID | Unit test evidence | PostgreSQL integration | Runtime/canary | Untested invariant |
| --- | --- | --- | --- | --- |
| EXEC-001/002 | `test_csv_upload_services.py`, `test_dashboard_upload.py` | `test_webapp_fix_flows.py` partial | browser smoke | rollback/duplicate upload under process interruption |
| EXEC-003/004 | `test_pipeline_service.py`, `test_background_worker.py`, `test_background_job_service.py` | `test_certification_runtime_postgresql.py`, queue tests | durable-worker e2e | full concurrency across API enqueue + cert claim + supervisor |
| EXEC-005..012 | `test_pipeline_executor.py`, domain service suites, golden pipeline | core/config/readiness/regime/sector/technical integration suites | single-run certification | one test asserting every stage's authority survives each commit |
| EXEC-013 | CERI orchestration/batched handler tests | `test_ceri_batched_workflow_v2.py`, PIT/source tests | prior canaries | provider partial plus supervisor restart at every DAG edge |
| EXEC-014 | pipeline/CERI handler tests | `test_ceri_pipeline_completion_barrier.py` | prior canaries | certification supervisor loss during finalizer enqueue |
| EXEC-015 | preflight/handoff unit suites | `test_transition_preflight_plan_postgresql.py`, decision config tests | single-run certification | direct mutation routes during active certification |
| EXEC-016/017 | setup/lifecycle comprehensive unit suites | lifecycle/SLSE/alert evidence suites | single-run certification | supervisor recovery during lifecycle commit |
| EXEC-018 | Winner job/capture tests | Winner capture/eligibility/reliability suites | Winner canaries | exact publication race across supervisor restart |
| EXEC-019 | pipeline/worker terminalization unit tests | worker progress/certification integration | durable worker e2e | cancellation at every terminal boundary |
| EXEC-020/021 | Winner handler/cohort/model unit suites | Winner maturation/publication/reliability suites | Winner canaries | certification direct model mutation isolation |
| EXEC-022 | CERI/setup alert tests | immutable alert evidence tests | UI/e2e partial | unbounded alert rebuild at production cardinality |
| EXEC-023 | `test_route_security.py`, route-specific tests | selected webapp flows | browser smoke | **no central test that all 52 mutating routes deny/limit certification mode** |
| EXEC-024 | ops/QA script tests and disposable-target guards | selected restore/ops tests | operator evidence varies | 49 mutation-indicator CLIs lack one uniform production reachability contract |
| EXEC-025 | worker/process-role/startup tests | external worker and certification tests | durable worker e2e | **SEC processor startup write in certification is not denied/scoped** |
| EXEC-026 | `test_worker_supervisor_reliability.py` | untracked `test_supervisor_recovery_isolation_postgresql.py` | Windows recovery e2e | committed PostgreSQL proof for session-scoped watchdog/death/shutdown |
| EXEC-027 | background service/worker tests | worker progress/queue/certification tests | recovery e2e | direct lower-level requeue called without session capability |
| EXEC-028 | lifespan/serve/startup preflight tests | none needed for business writes | runtime startup | monitor implementation changes could introduce writes without registry test |
| EXEC-029 | Alembic heads/migration tests | ephemeral DB migration suites | deployment | restore/bootstrap operator path remains external evidence |

## Recovery paths

| Path ID | Unit test | PostgreSQL integration | Runtime/canary | Untested |
| --- | --- | --- | --- | --- |
| REC-001/002 | background worker/job tests | queue/worker progress tests | recovery e2e | deterministic classifier coverage is broad; all driver variants not covered |
| REC-003/004 | background job/worker tests | certification runtime and worker progress partial | recovery e2e | NORMAL cross-worker race matrix |
| REC-005/006 | supervisor reliability + background service tests | untracked supervisor isolation test | Windows recovery e2e | certification requeue primitive intrinsic-authority assertion |
| REC-007/008/009 | supervisor reliability tests (pre-existing changes add assertions) | untracked supervisor isolation test | none for current patch | committed concurrent PostgreSQL proof |
| REC-010 | process-role/worker startup tests | external worker | Windows durable recovery | certification worker crash at startup SEC registration |
| REC-011 | pipeline service tests | T14D caller integration | prior canaries | resume after every stage/terminal state |
| REC-012/013 | SEC readiness unit tests | `test_sec_readiness_repair_postgresql.py` | prior canaries | supervisor loss during repair continuation |
| REC-014/015 | CERI handler/pipeline tests | completion barrier | Run 166 reached path | alert-rebuild scale and exact-session supervisor interruption |
| REC-016/017 | Winner handlers/scheduler/cohort tests | Winner reliability/maturation tests | Winner canaries | depth/exhaustion under process restart |
| REC-018 | IB fetch unit suites | `test_ib_fetch_recovery_postgresql.py` | IB probes | all cancel/retry interleavings |
| REC-019 | CERI backfill/replay/unit suites | PIT/residual/source bundle tests | controlled replay evidence | global purge/alert scale |
| REC-020 | setup replay/maintenance tests | original-context/lifecycle integration | none | certification direct API isolation |
| REC-021 | prewarm tests | `test_market_data_prewarm_postgresql.py` | none | supervisor restart while preempted |
| REC-022 | background/pipeline/prewarm cancellation tests | selected integration tests | e2e partial | complete terminal-state non-resurrection matrix |

## Writer/table cross-check

| Writer IDs | Direct coverage | Gap |
| --- | --- | --- |
| WRITE-001..017 runtime/config | pipeline, worker, certification, preflight, T14D suites | direct-route mode matrix and supervisor committed PG tests |
| WRITE-018..021 scope identity | T15A/T15B unit + PostgreSQL | no gap in writer identity; restart composition only |
| WRITE-022..033 core/market/evidence | Phase 2–4, T14B, temporal remediation, artifact cache suites | whole-stage commit-by-commit integration absent |
| WRITE-034..052 CERI | extensive CERI unit/PIT/source/postgres suites; current untracked Run-166 tests for change scoping/source chunks | purge/alert/normalizer scale and startup release mode |
| WRITE-053..059 setup/lifecycle/alerts | setup unit + lifecycle/alert evidence PostgreSQL | direct admin routes in certification |
| WRITE-060..070 Winner | Winner unit + reliability/canary PostgreSQL suites | direct model/publication route mode isolation |
| WRITE-071/072 IBMI | IBMI unit/persistence/resilience suites | global journal read/lock cardinality |
| WRITE-073 read-only | no writer test required | schema should enforce/document read-only intent |

## Job-type cross-check

All 34 registered handler keys have at least a unit test that constructs or invokes the handler family. The strongest PostgreSQL coverage exists for FULL_PIPELINE, SEC repair, IB_FETCH, CERI batched/continuation, setup lifecycle evaluation, Winner capture/maturation/cohort, market prewarm, and certification claims. The operational `WORKER_RECOVERY_PROBE` has unit/e2e operational coverage. The disabled Winner training/similarity constants have an explicit negative registration test in `tests/winner_probability/test_job_handlers.py`.

The missing cross-cutting assertion is not handler existence; it is that each creator, claim, recovery, cancellation, and mode path enforces the same authority token. No single test currently iterates the 34-type map and proves all seven dimensions.

## Findings coverage

| Finding | Existing direct test? |
| --- | --- |
| GAP-001 direct synchronous API mutation in certification | No |
| GAP-002 worker startup SEC release mutation in certification | No |
| GAP-003 recovery primitive called without session authority | Unit callers partly; no committed PG negative test |
| GAP-004 CERI alert global read | No scale/bounded-query assertion |
| GAP-005 purge global corpus read | Functional tests, no cardinality/bounded-query assertion |
| GAP-006 catalyst revision global read | Functional tests, no SQL-shape/scale assertion |
| GAP-007 IB journal global locks/reads | Functional tests, no large-cardinality lock-scope assertion |
| GAP-008 generic unscoped CERI helper APIs | No structural query-policy test |
| GAP-009 previous audit drift (115 -> 120 tables) | T14A inventory test covers old artifacts, not current schema delta automatically |
| GAP-010 same-transaction CERI progress visibility | No direct multi-connection assertion |

## Coverage verdict

The matrix is complete as an inventory and identifies direct evidence versus adjacency. It is not a PASS for canary resumption: three P1 authority paths and four high-risk unbounded-read paths lack direct committed PostgreSQL/scale coverage.
