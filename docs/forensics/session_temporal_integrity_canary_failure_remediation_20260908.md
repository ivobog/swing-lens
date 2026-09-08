# Session & Temporal Integrity Canary Failure Remediation — 2026-09-08

## A. Executive verdict

```text
CANARY FAILURE ROOT CAUSE CERTIFIED: YES
FROZEN-CONTEXT REMEDIATION CERTIFIED: YES
SAFE FOR SECOND CONTROLLED CANARY: YES
```

The first production canary exposed two independent pipeline-context propagation defects. Setup Lifecycle resolved the correct market context while loading data but dropped it before snapshot construction. The asynchronous CERI capture job durably carried the correct context but its handler ignored it and allowed the capture service to create a standalone context from the later wall clock. Both paths are now closed by a shared fail-closed resolver for pipeline-owned work. Delayed execution, retry, JSON serialization, worker restart, and ten market/time-zone boundaries are covered deterministically.

This certification is based on branch `codex/session-temporal-integrity-canary-remediation`, baseline canary evidence commit `545292d55def3cf0ba825ff95b5ccf25bcd2db7a`, implementation commit `8b5082c036b2d5cd3c733241f3b6891cf5fa6c8c`, and test commit `121fbe4f4f9bd319a7704452f30aab741a711d56`. The baseline was clean; both `1912eb4e5a1fb371e3898cb8971afd4e41cce048` and `545292d55def3cf0ba825ff95b5ccf25bcd2db7a` were verified ancestors, with `545292d...` the branch point.

Baseline production identity was database `swinglens` at `127.0.0.1:5432`, Alembic `0068_market_calc_context`, with no app/worker process and zero running or queued jobs. Run 148 was `COMPLETED`, Pipeline 141 was `PARTIAL`, and full-pipeline Job 42862 was `COMPLETED`. The baseline clock snapshot was approximately `2026-09-08T15:26+02:00` Zurich / `13:26Z` / `09:26-04:00` New York. The final read-only snapshot at `2026-09-08T16:35:28+02:00` / `14:35:28Z` / `10:35:28-04:00` again showed zero running/queued jobs and no app/worker process.

## B. Failed canary reconstruction

The authoritative persisted context is context 1, owned by Pipeline 141 and Upload Run 148:

```text
cutoff_at:                  2026-09-08T12:06:38.383968+02:00
latest_completed_session:   2026-09-04
calendar_version:           swinglens-us-equities-v1
bar_readiness_version:      daily-close-plus-15m-v1
```

Lifecycle recapture rows retained `data_as_of_date=2026-09-04`, but all four first-class temporal-lineage fields were null. CERI capture Job 42873 carried context 1 and the exact fields above, but snapshots used `2026-09-08T12:08:01.949706+02:00`, a drift of `+83.565738s`, and persisted a null context ID. CERI's session and calendar happened to remain unchanged because execution had not crossed a session boundary.

| Type | Row ID | Ticker | Expected context ID | Persisted context ID | Expected cutoff/session | Persisted cutoff/session | Delta |
| --- | ---: | --- | ---: | ---: | --- | --- | --- |
| Lifecycle | 36934 | AAPL | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | **NULL / NULL** | Not computable; provenance null |
| Lifecycle | 36935 | AMGN | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | **NULL / NULL** | Not computable; provenance null |
| Lifecycle | 36936 | AWK | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | **NULL / NULL** | Not computable; provenance null |
| Lifecycle | 36937 | CBOE | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | **NULL / NULL** | Not computable; provenance null |
| Lifecycle | 36938 | DHT | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | **NULL / NULL** | Not computable; provenance null |
| CERI | 10914 | AAPL | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | `12:08:01.949706+02` / `2026-09-04` | +83.565738s |
| CERI | 10915 | AMGN | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | `12:08:01.949706+02` / `2026-09-04` | +83.565738s |
| CERI | 10916 | AWK | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | `12:08:01.949706+02` / `2026-09-04` | +83.565738s |
| CERI | 10917 | CBOE | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | `12:08:01.949706+02` / `2026-09-04` | +83.565738s |
| CERI | 10918 | DHT | 1 | **NULL** | `12:06:38.383968+02` / `2026-09-04` | `12:08:01.949706+02` / `2026-09-04` | +83.565738s |

Read-only reconstruction after the fix loaded context 1 from Job 42873's persisted identity and returned the exact original cutoff, session, and calendar. Thus the old rows remain ten violations, while the new resolver deterministically supplies context 1 for the same durable evidence. No provider replay was needed.

## C. Internet outage assessment

```text
PIPELINE PARTIAL CAUSED BY INTERNET OUTAGE: NO
FROZEN-CONTEXT VIOLATIONS CAUSED BY INTERNET OUTAGE: NO
```

The reported approximately one-hour connectivity loss did not overlap or interrupt this canary's execution in a way visible in its logs or persisted jobs. It neither caused the `PARTIAL` result nor caused the temporal violations. The defects are deterministic and reproduce after seconds; longer delay would only increase the potential damage, not create the bug.

Timeline, in UTC:

| Time | Evidence |
| --- | --- |
| 10:06:38.343 | Pipeline 141 and Job 42862 created. |
| 10:06:38.383 | Frozen context 1 created. |
| 10:06:38.901 | Full-pipeline job claimed. |
| 10:06:40.333–10:06:40.932 | Market-data stage connected and completed; zero requests were planned/executed. |
| 10:06:52.462–10:06:55.379 | Lifecycle capture/evaluation ran and produced the five null-lineage rows. |
| 10:06:55.519–10:06:55.570 | Pipeline became `PARTIAL`; full-pipeline job completed. |
| 10:06:55.648–10:08:01.706 | CERI provider/normalize/feature/finalize jobs ran. |
| 10:08:01.765–10:08:03.923 | CERI capture Job 42873 ran and produced the five drifted snapshots. |
| 10:08:03.990–10:11:02.932 | CERI change and alert continuations completed. |

All 12 pipeline stages completed. Jobs 42862–42875 completed with `retry_count=0`, null error messages, and healthy worker/watchdog progress. No DNS, timeout, connection-refused, gateway, or other provider exception was recorded. Brief `job.deferred` decisions were dependency waits, not retries. The worker remained process 8808 and its lease stayed healthy.

Pipeline 141 was `PARTIAL` because all five otherwise complete `combined_results` rows had ordinary analytical warnings. There were zero incomplete rows, zero IB failures, zero technical failures, zero lifecycle failures, and zero CERI failures. The five warning sets were:

- AAPL: `balance_sheet_stress`, `box_failure`, `failed_breakout`, `value_trap_risk`.
- AMGN: `balance_sheet_stress`, `distribution_risk`, `value_trap_risk`.
- AWK: `balance_sheet_stress`, `earnings_quality_risk`, `liquidity_buffer_weak`, `poor_cash_conversion`, `value_trap_risk`.
- CBOE: `distribution_risk`.
- DHT: `distribution_risk`, `earnings_quality_risk`, `poor_cash_conversion`, `value_trap_risk`.

The pipeline status policy marks any nonzero warning-row count `PARTIAL`. That is independent of network availability and of frozen-context persistence.

## D. Lifecycle root cause

Production call graph at canary commit `545292d...`:

```text
execute_full_pipeline
  -> initial setup capture receives pipeline market_cutoff
  -> SETUP_CAPTURE_HANDOFF_ENABLED=false
  -> _invoke_setup_evaluation without capture_result or market_cutoff
  -> SetupLifecycleEvaluationService.evaluate_run
  -> capture_snapshots_for_run without explicit market_cutoff
  -> SetupLifecycleSourceLoader.load_run_context
  -> market_context_for_upload_run resolves context 1
  -> build_run_source_context called without market_cutoff
  -> TickerSourceContext.market_cutoff=None
  -> snapshot builder omits temporal_lineage
  -> repository persists NULL context/cutoff/session/calendar
```

The decisive drop was in `app/services/setup_lifecycle/source_loader.py` at the call to `build_run_source_context`: the correct context was used to bound source queries but not supplied to the object consumed by the snapshot builder. The upstream evaluator API also made the context optional, so pipeline execution did not express ownership and the standalone fallback remained reachable.

Classification:

- context field absent from the evaluation API contract;
- nested pipeline recapture behaved like an optional/standalone operation;
- correct context resolved but dropped at service-to-builder transition;
- legacy fallback to current context remained reachable if upload-run resolution failed.

The fix always sends `market_cutoff` and `pipeline_run_id` from `pipeline_executor.py`, validates both before the evaluation row is created in `evaluation_service.py`, passes the cutoff through recapture, validates capture handoffs, and supplies the resolved cutoff to `build_run_source_context` in `source_loader.py`.

## E. Async CERI root cause

Production call graph at canary commit `545292d...`:

```text
pipeline CERI scheduling
  -> batched workflow serializes context 1/cutoff/session/calendar
  -> provider -> normalize -> feature -> finalizer preserve payload
  -> Job 42873 durable JSON contains the correct frozen identity
  -> execute_capture_run_job decodes only run_id
  -> CeriRunCaptureService.capture_run(db, run_id)
  -> optional market_cutoff is None
  -> standalone_market_context(reason="STANDALONE_CERI_CAPTURE", now())
  -> snapshots persist later cutoff and NULL context ID
```

The exact ignored-argument call was `app/services/ceri/job_handlers.py:333` at `545292d...`. The architectural defect was broader than a missing argument: the capture service intentionally supports standalone operation and its market context was optional, while the handler had no pipeline-owned contract or central ownership validation. That allowed a durable pipeline job to enter the standalone wall-clock fallback.

Classification:

- context serialized and deserialized as JSON but ignored by the handler;
- context field optional where pipeline-owned execution requires it;
- nested operation accidentally starts a standalone context;
- service reconstructs context from `now()`;
- legacy fallback reachable from pipeline-owned execution.

The fixed handler recognizes pipeline ownership from its workflow/temporal identity, requires all four immutable fields, resolves the persisted context, validates context-to-pipeline-to-upload ownership plus repeated payload fields, and passes that object into capture. Missing, mismatched, foreign, or nonexistent context now fails closed.

## F. Retry/requeue analysis

The background-job retry and stale/abandoned-worker recovery paths mutate lease/status/retry metadata on the same job row and do not rewrite `payload_json`. Tests pin payload equality across ordinary retry, stale recovery, and worker-restart recovery. The CERI network-delay test raises a transient `ConnectionError` on the first attempt, advances the effective execution point by 24 hours, JSON round-trips the payload as a restarted worker would, and proves the retry still supplies the original context.

Child-job creation was also reviewed. The batched pipeline path already copied the temporal fields through finalization. The legacy CERI provider → normalize → feature → capture chain did not preserve them at every child transition; this is recorded as `STI-CANARY-F003` and fixed centrally with `_temporal_context_payload`. Capture → change → alert now also retains the immutable provenance for auditability. Neither retry nor requeue creates a replacement context.

## G. Remediation design

`market_calculation_context_service.py` now owns two shared operations:

- `resolve_pipeline_market_context` requires a context ID, loads the persisted row, verifies pipeline ownership, upload-run ownership, the pipeline's authoritative unique context, and optional repeated cutoff/session/calendar fields.
- `assert_pipeline_calculation_context` validates an in-memory lifecycle cutoff against that persisted authority, including exchange timezone, daily-bar-ready time, and bar-readiness policy.

The invariant is therefore enforced as:

```text
pipeline-owned operation
  => persisted calculation_context_id required
  => context owns this pipeline and upload run
  => repeated temporal fields match
  => use persisted context or fail before business writes
```

Standalone Lifecycle/CERI entry points remain valid and may deliberately create a current explicit standalone context. They do not carry a pipeline workflow identity. The pipeline path can no longer silently take that fallback.

## H. Adjacent-path audit

The audit searched production code for context/cutoff/session fields, payload enqueue/deserialization, retry, recapture/rebuild/capture, standalone helpers, and wall-clock calls. Reviewed transitions included full-pipeline setup capture/evaluation, CERI batched scheduling/finalization, legacy CERI child jobs, background-job retry/recovery, technical artifact caching, CERI feature rebuild/reaction, HTF, Relative Strength, IBMI, and Winner temporal paths.

New finding:

- `STI-CANARY-F003` — the legacy CERI child-job chain dropped temporal payload fields between some provider/normalize/feature/capture/change/alert transitions. It did not create the five Run 148 CERI violations because Run 148 used the batched finalizer path, but it could permit the same class of failure for pipeline-owned legacy execution. Fixed and covered by an end-to-end enqueue-chain assertion.

No other unresolved pipeline-owned path was found that silently creates a new market calculation context. Operational `datetime.now()` calls used for leases, progress, acknowledgements, or observation timestamps are not market calculation cutoffs.

## I. Schema/migration impact

```text
new migration required?       no
new migration ID:             none
production migration applied? no
current single head:          0068_market_calc_context
```

The existing schema already supplies the necessary durable structure: one unique `market_calculation_contexts.pipeline_run_id`, ownership links to upload/pipeline runs, nullable lifecycle/CERI context foreign keys and lineage columns, and durable JSON job payloads. Nullable lineage must remain valid for legacy rows and intentional standalone operations. A database-wide `NOT NULL` cannot express “only when pipeline-owned,” especially across JSON workflow identity, without fabricating legacy provenance or breaking standalone behavior. Central application validation before writes is the appropriate enforcement point; PostgreSQL round-trip tests prove the persisted context/job linkage survives session closure and worker reopen.

## J. Test evidence

The three minimal reproductions were first run against the canary code state and failed independently: lifecycle source context became null, pipeline evaluation rejected/omitted the context contract, and CERI capture ignored the correct job context. The same three passed after remediation.

Focused remediation lane:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/setup_lifecycle tests/ceri/test_ceri_orchestration.py tests/ceri/test_wave1_p0.py tests/test_pipeline_executor.py tests/test_background_job_service.py tests/test_session_temporal_integrity_remediation.py -q --tb=short
```

Result: `367 passed, 1 warning in 60.38s`.

The focused lifecycle/CERI/pipeline development lane also completed with `333 passed, 5 skipped`; a narrower final delay/static smoke completed with `54 passed, 1 warning`.

Cross-domain temporal lane used the prior certification's exact file list plus `tests/test_background_job_service.py` and completed with `703 passed, 7 skipped, 1 warning in 241.66s`. It covered forensic characterization, market clock/context, technical cutoff/cache, pipeline orchestration, Setup Lifecycle, CERI capture/async/reaction, HTF, Relative Strength, IBMI, and Winner temporal-integrity regressions.

Full non-slow unit lane:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not slow and not integration and not e2e and not external and not destructive" --ignore=tests/e2e -q --tb=short
```

Result: `2152 passed, 156 deselected, 22 warnings in 558.83s`.

Disposable PostgreSQL lane, using uniquely named guarded databases:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short tests/integration/test_session_temporal_integrity_postgresql.py tests/integration/test_technical_artifact_cache_postgresql.py tests/integration/test_winner_temporal_integrity_postgresql.py
```

Result: `14 passed, 4 warnings in 145.61s`. This includes Alembic `0067 -> 0068`, context plus durable-job JSON round-trip through a reopened SQLAlchemy session, technical-cache integrity, and Winner temporal regressions.

Static verification:

```powershell
.\.venv\Scripts\ruff.exe check app tests
.\.venv\Scripts\ruff.exe format --check <all 14 changed Python files>
.\.venv\Scripts\python.exe -m compileall -q app tests
```

Ruff lint passed across `app` and `tests`; all 14 changed Python files passed format check; `compileall` passed. A whole-tree formatter baseline reports 203 unrelated pre-existing files that Ruff would reformat. None is a changed file and this repository-wide formatting debt was not hidden or broadened into this remediation.

## K. Production safety

Read-only final verification produced exactly the same hashes captured at baseline for lifecycle rows 36934–36938 and CERI rows 10914–10918. Job-state totals remained `BLOCKED 1`, `CANCELLED 1056`, `COMPLETED 24347`, `FAILED 1195`, `PARTIAL 15865`, `STALE 12`, with zero running and zero queued. Production remained at `0068_market_calc_context` and no SwingLens app/worker process was present.

```text
Run 148 rows unchanged:            yes
historical contamination unchanged: yes
production business writes:         none
production jobs enqueued:            none
production migration applied:        none
historical repair performed:         no
second real canary run:               no
```

Disposable PostgreSQL database creation/migration/drop was isolated from database `swinglens`; production access used only reads and rollback/connection close.

## L. Final recommendation

```text
May a separate task run Controlled Canary #2?

YES
```

The second canary must remain a separate task and should specifically assert that final lifecycle and CERI rows carry context 1-equivalent identity, not merely the same session date. It should also verify the capture/change/alert durable payload chain and preserve the strict no-repair treatment of Run 148 evidence.
