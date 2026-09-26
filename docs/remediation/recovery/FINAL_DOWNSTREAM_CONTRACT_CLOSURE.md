# Final downstream production-contract closure

### GAP 1 — SEC/CERI PROVIDER-INGEST COMPLETION BARRIER: PASS

Provider-ingest mode now persists `WAITING_FOR_CERI_COMPLETION`, releases the canonical single worker, executes the real provider/normalize/feature/finalize/capture/change/alert DAG, and resumes the same `PipelineRun` at `FREEZING_DECISION_HANDOFF_MANIFEST`. The continuation preserves the upload run, calculation context, cutoff, business session, calendar version, configuration anchors, scope, semantic authority, and provider workflow identity. It does not replay Fundamental, Market, Technical, Market Regime, Combined, Ranking, or Sector.

Continuation identity is the durable request key `resume-pipeline:<pipeline>:after-ceri:<workflow>:from:<step>`. Barrier evaluation verifies a terminal policy-valid workflow and the expected run-scoped certified capture before enqueueing. Duplicate finalizer/barrier evaluation coalesces to one continuation. Required child failure rolls up to the parent, prevents Setup and Winner, and cannot leave a successful parent. Restart recovery resumes the retained pipeline and authority rather than creating a new pipeline or calculation context.

| Certification | Verdict | Evidence |
| --- | --- | --- |
| Single-worker barrier, child failure, duplicate finalizer, restart/recovery | PASS | `tests/integration/test_ceri_pipeline_completion_barrier.py` |
| Exact-10 provider-enabled pipeline | PASS | Exact membership `BHE, BLLN, KLIC, LSCC, PDFS, ACMR, RDVT, AVT, DVN, JNJ`; terminal `COMPLETED`; frozen providers; real asynchronous DAG and real downstream writers; no manual CERI evidence |
| Exact-10 final broad-run metrics | PASS | 60 provider records; 250.886922s total; 29.167093s Technical; 84.769854s CERI; 2.748449s maximum checkpoint gap; jobs: 4 ingest, 4 normalize, 1 feature, 1 finalize, 1 capture, 1 change, 1 alert, 2 pipeline executions |
| Frozen-100 provider-enabled pipeline | PASS | Terminal `COMPLETED`; 600 provider records; no retry, recovery, lease expiry, stall, duplicate continuation, or source/evidence amplification |
| Frozen-100 final broad-run metrics | PASS | 2,474.589751s total; 178.472884s Technical (`<5m`); 766.055459s CERI; 20.543906s maximum checkpoint gap (`<60s`); jobs: 16 ingest, 8 normalize, 2 feature, 1 finalize, 1 capture, 1 change, 1 alert, 2 pipeline executions |
| Continuous CERI-to-Winner real-writer tail | PASS | Provider ingest through certified CERI capture/change/alerts, handoff, Setup, Lifecycle/alerts, Winner capture, maturation, materialization, and publication under one retained database/run/configuration lineage |

The final broad run exposed and closed two timing gaps before this certification: capture input preparation and the post-scoring snapshot persistence tail now emit durable progress checkpoints. The final frozen-100 capture maximum was 17.874372s and the aggregate CERI maximum was 20.543906s. Frozen external responses were used; no live SEC request, IB historical acquisition, live upload, or live pipeline was run.

### GAP 2 — WINNER MATERIAL-POSITIVE PUBLICATION: PASS

`tests/integration/test_winner_material_positive_publication_postgresql.py` proves the production writer chain on disposable PostgreSQL:

`native certified capture -> certified prediction -> certified episode -> decision-time estimate -> forward outcome maturation -> certified financial observation -> evidence watermark -> cohort generation -> materialization -> publication -> serving projection`.

| Acceptance | Result |
| --- | --- |
| Matured certified observations | 1 |
| Certified financial population | 1 |
| Evidence rows loaded / generation evidence rows | 1 / 1 |
| Evidence manifest members | >0 |
| Certified Winner episodes | 1 |
| Decision-time serving estimates | 1 |
| Published generations for the refresh state | 1 |
| Generation status | `PUBLISHED` |
| Serving/current projection | Points to the new generation and exact watermark |
| Predecessor/current-publication contract | Validated by publication authorities and reliability tests |
| Legacy unsealed predictions | Excluded from the financial population |
| Publication retry | `ALREADY_ACTIVE`; no duplicate serving generation |
| Rollback/atomicity | Existing PostgreSQL rollback and publication reliability controls pass |

The exact-10 integrated certification independently matured one certified observation and published one generation with ten Winner episodes and one serving estimate.

### GAP 3 — REACHABLE DOWNSTREAM MUTATION-GUARD INVENTORY: PASS

The bounded production reachability inventory covers CERI provider/capture through Decision Handoff, Setup, Lifecycle, alerts, Winner outcomes/cohorts, and publication. `tests/test_reachable_downstream_guard_inventory.py` discovers concrete guard codes from reviewed production writers and rejects missing proof files or non-final statuses.

| Inventory measure | Result |
| --- | --- |
| Reviewed reachable authority/writer surfaces | 22 / 22 verified |
| Concrete reachable guard codes | 213 / 213 verified |
| Allowed status set | `VERIFIED_POSITIVE`, `VERIFIED_NEGATIVE`, `NOT_REACHABLE_CURRENT_CONFIG` only |
| Unknown, assumed, partial, or untested entries | 0 |

The 22 surfaces are: shared retained-source boundary, shared calculation boundary, CERI capture, CERI change, CERI alerts, CERI completion barrier, Decision Handoff, Setup caller, Setup capture, Setup change, Lifecycle, Setup alerts, Winner prediction, Winner prediction seal, Winner episode, Winner obligations, Winner outcomes, Winner estimates, Winner cohort, Winner generation publication, Winner materialization, and Winner publication evidence. Each generated inventory row records its exact guard, production writer, required authority, named production-shaped test, and final status.

The release-delta review also records five newly reachable mutation sinks: pipeline wait-state mutation; pipeline CERI failure roll-up; continuation enqueue; child-failure roll-up; and legacy Lifecycle retirement. The final inventory suites passed before the final broad run: 22/22 surfaces and 213/213 codes.

### GAP 4 — BROAD REPOSITORY SUITE: FAIL

The user-directed one final broad invocation was run once and was not rerun:

`python -m pytest -q`

| Result | Count |
| --- | ---: |
| Passed | 4,126 |
| Failed | 1 |
| Skipped | 10 |
| Warnings | 2,035 |
| Duration | 10,106.59s (2:48:26) |

The sole failure was `tests/recovery/test_whole_application_recovery.py::test_deterministic_pipeline_10_25_100`. Under broad-suite import order, the fixture patches `app.settings.get_settings` but the already-imported `app.services.pipeline_executor.get_settings` binding retains provider-ingest mode. The synthetic recovery adapter therefore returns before its Technical callback, leaving `technical_span.start` and `technical_span.end` as `None`; metrics assembly at line 896 raises `TypeError: unsupported operand type(s) for -: 'NoneType' and 'NoneType'`. The same test had passed in focused execution, establishing an order-dependent recovery-test harness defect. It does not invalidate the final broad run's passing real provider-enabled exact-10 and frozen-100 executions, but the explicit release rule is `0 failed`, so GAP 4 and the overall release certification remain failed.

Remaining findings: P0 = 0; P1 = 1 (order-dependent recovery harness/settings binding). READY FOR FINAL SMALL LIVE SLICE: **NO** because the broad release gate is not clean. READY FOR FINAL 100 LIVE CANARY: **NO**. No live pipeline, live upload, network acquisition, push, or merge occurred. Branch: `codex/final-downstream-contract-closure`. The pre-existing user-owned `WHOLE_APPLICATION_RECOVERY_AUDIT.md` remains untouched and uncommitted.
