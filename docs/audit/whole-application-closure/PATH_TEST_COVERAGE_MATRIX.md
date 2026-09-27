# Path test-coverage matrix

Coverage means a test explicitly asserts the path's authority, state, isolation, idempotency, or production query-shape invariant. A nearby happy-path test does not count. This matrix includes the R1 authority evidence and R2 unit/structural/disposable-PostgreSQL bounded-access evidence.

## Execution paths

| Path ID | Unit test evidence | PostgreSQL integration | Runtime/canary | Untested invariant |
| --- | --- | --- | --- | --- |
| EXEC-001/002 | `test_csv_upload_services.py`, `test_dashboard_upload.py` | `test_webapp_fix_flows.py` partial | browser smoke | rollback/duplicate upload under process interruption |
| EXEC-003/004 | `test_pipeline_service.py`, `test_background_worker.py`, `test_background_job_service.py` | `test_certification_runtime_postgresql.py`, queue tests | durable-worker e2e | full concurrency across API enqueue + cert claim + supervisor |
| EXEC-005..012 | `test_pipeline_executor.py`, domain service suites, golden pipeline | core/config/readiness/regime/sector/technical integration suites | single-run certification | one test asserting every stage's authority survives each commit |
| EXEC-013 | CERI orchestration/batched handler tests plus `test_r2_scoped_loader_guards.py` | `test_ceri_batched_workflow_v2.py`, PIT/source tests, `test_r2_bounded_access_postgresql.py` control-plane isolation | prior canaries | provider partial plus supervisor restart at every DAG edge |
| EXEC-014 | pipeline/CERI handler tests | `test_ceri_pipeline_completion_barrier.py` | prior canaries | certification supervisor loss during finalizer enqueue |
| EXEC-015 | preflight/handoff unit suites | `test_transition_preflight_plan_postgresql.py`, decision config tests | single-run certification | direct mutation routes during active certification |
| EXEC-016/017 | setup/lifecycle comprehensive unit suites | lifecycle/SLSE/alert evidence suites | single-run certification | supervisor recovery during lifecycle commit |
| EXEC-018 | Winner job/capture tests | Winner capture/eligibility/reliability suites | Winner canaries | exact publication race across supervisor restart |
| EXEC-019 | pipeline/worker terminalization unit tests | worker progress/certification integration | durable worker e2e | cancellation at every terminal boundary |
| EXEC-020/021 | Winner handler/cohort/model unit suites | Winner maturation/publication/reliability suites | Winner canaries | certification direct model mutation isolation |
| EXEC-022 | CERI/setup alert tests plus scoped-loader guards | immutable alert evidence plus R2 1,022-row alert SQL-shape fixture | UI/e2e partial | R2 bounded alert invariant covered; broader end-to-end restart remains |
| EXEC-023 | `test_route_security.py` enumerates all 52 routes and exercises NORMAL/unclassified/NORMAL_ONLY/control/session-scoped policies plus real CERI/setup writers | `test_runtime_mutation_authority_postgresql.py` denied cleanup byte snapshot | browser smoke | R1 authority invariant covered; future route drift fails registry count/classification guard |
| EXEC-024 | ops/QA script tests and disposable-target guards | selected restore/ops tests | operator evidence varies | 49 mutation-indicator CLIs lack one uniform production reachability contract |
| EXEC-025 | startup authority test + SEC lifecycle NORMAL/read-only/missing/mismatch tests | `test_runtime_mutation_authority_postgresql.py` exact release-table nonmutation | durable worker e2e | R1 certification startup nonmutation covered |
| EXEC-026 | `test_worker_supervisor_reliability.py`, structural production-caller guard | `test_supervisor_recovery_isolation_postgresql.py` + R1 authority PG matrix | Windows recovery e2e | R1 typed authority/session isolation covered |
| EXEC-027 | background service/worker tests reject missing/untyped authority and preserve cancellation | worker progress/queue/certification + R1 authority PG matrix | recovery e2e | R1 primitive/caller authority covered |
| EXEC-028 | lifespan/serve/startup preflight tests | none needed for business writes | runtime startup | monitor implementation changes could introduce writes without registry test |
| EXEC-029 | Alembic heads/migration tests | ephemeral DB migration suites | deployment | restore/bootstrap operator path remains external evidence |

## Recovery paths

| Path ID | Unit test | PostgreSQL integration | Runtime/canary | Untested |
| --- | --- | --- | --- | --- |
| REC-001/002 | background worker/job tests | queue/worker progress tests | recovery e2e | deterministic classifier coverage is broad; all driver variants not covered |
| REC-003/004 | background job/worker typed-authority and cancellation tests | certification runtime, worker progress, R1 NORMAL transition | recovery e2e | NORMAL cross-worker race matrix remains broader than R1 |
| REC-005/006 | supervisor reliability + missing/untyped primitive + structural caller tests | supervisor isolation + R1 unrelated-session byte snapshot | Windows recovery e2e | intrinsic authority and current-session scope covered |
| REC-007/008/009 | supervisor reliability and structural caller tests | supervisor isolation + R1 recovery matrix | none for current patch | typed authority/cancellation/cross-session isolation covered |
| REC-010 | process-role/worker startup tests | external worker | Windows durable recovery | certification worker crash at startup SEC registration |
| REC-011 | pipeline service tests | T14D caller integration | prior canaries | resume after every stage/terminal state |
| REC-012/013 | SEC readiness unit tests | `test_sec_readiness_repair_postgresql.py` | prior canaries | supervisor loss during repair continuation |
| REC-014/015 | CERI handler/pipeline tests | completion barrier plus R2 alert/progress production-shape proof | Run 166 reached path | exact-session supervisor interruption remains a later certification exercise |
| REC-016/017 | Winner handlers/scheduler/cohort tests | Winner reliability/maturation tests | Winner canaries | depth/exhaustion under process restart |
| REC-018 | IB fetch unit suites | `test_ib_fetch_recovery_postgresql.py` | IB probes | all cancel/retry interleavings |
| REC-019 | CERI backfill/replay/unit suites plus structural scoped-loader guard | PIT/residual/source bundle plus R2 purge/alert cardinality proof | controlled replay evidence | global purge/alert access shape covered |
| REC-020 | setup replay/maintenance tests | original-context/lifecycle integration | none | certification direct API isolation |
| REC-021 | prewarm tests | `test_market_data_prewarm_postgresql.py` | none | supervisor restart while preempted |
| REC-022 | background/pipeline/prewarm cancellation tests | selected integration tests | e2e partial | complete terminal-state non-resurrection matrix |

## Writer/table cross-check

| Writer IDs | Direct coverage | Gap |
| --- | --- | --- |
| WRITE-001..017 runtime/config | pipeline, worker, certification, preflight, T14D, route registry, typed recovery suites | R1 includes denied-web and recovery before/after PostgreSQL proofs; broader stage concurrency remains |
| WRITE-018..021 scope identity | T15A/T15B unit + PostgreSQL | no gap in writer identity; restart composition only |
| WRITE-022..033 core/market/evidence | Phase 2–4, T14B, temporal remediation, artifact cache suites | whole-stage commit-by-commit integration absent |
| WRITE-034..052 CERI | extensive CERI unit/PIT/source/postgres suites; R1 authority plus R2 alert/purge/normalizer query-shape and control-plane isolation | no remaining R2 bounded-access gap |
| WRITE-053..059 setup/lifecycle/alerts | setup unit + lifecycle/alert evidence PostgreSQL + real setup-route certification denial | R1 authority closed; restart composition remains |
| WRITE-060..070 Winner | Winner unit + reliability/canary PostgreSQL suites; all direct mutation routes classified NORMAL_ONLY | R1 mode isolation closed; publication race remains separate |
| WRITE-071/072 IBMI | IBMI unit/persistence/resilience suites plus R2 600-episode/link/candidate page and target-lock proof | global journal access gap closed; no deferred R4 aggregate is attached to this writer family |
| WRITE-073 read-only | no writer test required | schema should enforce/document read-only intent |

## R5.1 operational control paths

| Path ID | Unit test | PostgreSQL integration | Runtime/canary |
| --- | --- | --- | --- |
| AUTO-018 | `tests/test_background_worker.py`, `tests/test_pre_enqueue_operational_gate.py`, `tests/test_readiness_observability.py` | `tests/integration/test_worker_preclaim_resilience_postgresql.py` | no canary; host remains blocked by committed-memory admission |
| TX-26 | `tests/test_background_worker.py` | `tests/integration/test_worker_preclaim_resilience_postgresql.py` | no canary; exact worker registration only |

## Job-type cross-check

All 34 registered handler keys have at least a unit test that constructs or invokes the handler family. The strongest PostgreSQL coverage exists for FULL_PIPELINE, SEC repair, IB_FETCH, CERI batched/continuation, setup lifecycle evaluation, Winner capture/maturation/cohort, market prewarm, and certification claims. The operational `WORKER_RECOVERY_PROBE` has unit/e2e operational coverage. The disabled Winner training/similarity constants have an explicit negative registration test in `tests/winner_probability/test_job_handlers.py`.

The missing cross-cutting assertion is not handler existence; it is that each creator, claim, recovery, cancellation, and mode path enforces the same authority token. No single test currently iterates the 34-type map and proves all seven dimensions.

## Findings coverage

| Finding | Existing direct test? |
| --- | --- |
| GAP-001 direct synchronous API mutation in certification | **Yes — CLOSED**: complete registry/policy tests plus PostgreSQL denied-write byte snapshot |
| GAP-002 worker startup SEC release mutation in certification | **Yes — CLOSED**: startup/lifecycle tests plus PostgreSQL exact table nonmutation |
| GAP-003 recovery primitive called without session authority | **Yes — CLOSED**: missing/untyped rejection, structural callers, supervisor isolation, PostgreSQL unrelated-lineage nonmutation/NORMAL transition |
| GAP-004 CERI alert global read | **Yes — CLOSED:** 1,022 changes/two alerts/255 companies, exact five changes/five companies/one cooldown row, three predicated queries |
| GAP-005 purge global corpus read | **Yes — CLOSED:** 251 large source payloads, exact preview/execution parity, target mutation and unrelated preservation |
| GAP-006 catalyst revision global read | **Yes — CLOSED:** 253 revisions, event-scoped `MAX`, one scalar materialization |
| GAP-007 IB journal global locks/reads | **Yes — CLOSED:** 600 episodes/links/candidates, bounded page/candidate pool and one target lock |
| GAP-008 generic unscoped CERI helper APIs | **Yes — CLOSED:** production-session rejection and statement-level large-entity guards |
| GAP-009 previous audit drift (115 -> 120 tables) | **Yes — CLOSED:** generated 120-table census, 119 mutable writer mappings, synthetic table drift rejection, CI registry gate |
| GAP-012 split runtime policy | **Yes — CLOSED:** explicit NORMAL/CERTIFICATION policy for routes, jobs, and autonomous triggers plus synthetic missing-mode rejection |
| GAP-010 same-transaction CERI progress visibility | **Yes — CLOSED:** independent connection sees progress/cancel before domain rollback; liveness survives while domain row does not |
| GAP-011 retained aggregate operability | **Yes — CLOSED:** `test_aggregate_operability_postgresql.py` seeds 1.747 million rows, proves semantics, and enforces rows/result/estimate/index/seq-scan/time/spill budgets for READ-002/003/010/012/022/023/025/026; the static registry guard enforces policy/source/index/test presence |

## Coverage verdict

The matrix is now backed by a machine-readable path-to-test registry whose file/node references are checked in CI. R1 authority, R2 bounded-read/transaction coverage, R3 drift closure, and R4 aggregate operability are PASS. Remaining P0/P1/P2 counts are all zero. This permits R5 work but is not approval for canary resumption.
