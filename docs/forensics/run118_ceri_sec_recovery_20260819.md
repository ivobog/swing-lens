# Run 118 CERI / SEC Recovery Report

Status: complete. Run 118 was recovered through the durable CERI checkpoint, v3 was certified and explicitly promoted, every CERI child workflow job completed, and all final quality gates passed.

## Root-cause confirmation

- The original apparent stall was an occupied worker: an old Winner cohort-refresh job held the only worker until restart.
- The CERI failure itself was a correct fail-closed SEC readiness rejection. Under processor v2 (`sec-guidance:948beb114caa8da9`), 136 of the 180 run tickers had matching sync state and 44 required bootstrap/mapping work.
- Commit `52e7e80` changed visible-text extraction from v2 to v3, producing exact signature `sec-guidance:eed017654682a0c9`. Because sync readiness is signature-specific, the restarted v3 worker correctly saw 0 of 180 ready; v2 readiness could not be reused.
- No CERI provider batch was enqueued by the failed attempt. The safety gate prevented partial SEC ingestion and CERI workflow corruption.
- The damaging behavior was orchestration: a deterministic prerequisite was raised as generic runtime failure after expensive stages, retried five times, and replayed durable upstream work.

The complete pre-mutation evidence is in `docs/forensics/run118_ceri_sec_pre_mutation_20260819.md`.

## Pre-recovery identity and durable checkpoint

| Item | Verified value |
|---|---|
| Upload run | `118`, 180 rows / 180 distinct tickers |
| Pipeline run | `110` |
| Original job | `30993`, `FAILED`, retry 4 / max 3 |
| Failed stage | `CERI_PROVIDER_INGEST` |
| Fundamental scores | 180 rows / 180 tickers |
| Technical scores | 180 rows / 180 tickers |
| Combined results | 180 rows / 180 tickers |
| Ranking results | 900 rows / 180 tickers |
| Market-regime snapshots | 2 |
| Sector-rotation snapshots | 2 |
| CERI child jobs from failed attempt | 0 |

The checkpoint validator independently re-queried those cardinalities and found two persisted technical-error/low-confidence rows. The resumed pipeline reported one incomplete combined row, so the correct terminal pipeline state is `PARTIAL`, not an artificially rewritten `COMPLETED`.

## CIK and applicability remediation

All 14 previously missing CIK mappings were resolved through the canonical SEC mapping and persisted. No ticker was classified NOT_APPLICABLE merely to bypass mapping.

| Ticker | Persisted CIK |
|---|---|
| AMP | `0000820027` |
| ANET | `0001596532` |
| ARQT | `0001787306` |
| EDU | `0001372920` |
| EE | `0001888447` |
| HNGE | `0001673743` |
| HRI | `0001364479` |
| JAZZ | `0001232524` |
| MNST | `0000865752` |
| MTRN | `0001104657` |
| PEN | `0001321732` |
| PRLB | `0001443669` |
| SF | `0000720672` |
| TPC | `0000077543` |

Post-remediation mapping categories before bootstrap: CIK_MISSING 0, UNRESOLVED_MAPPING 0, INVALID_TICKER 0, SEC_NOT_APPLICABLE 0.

## Reliability implementation

### Typed deterministic blocks

- Added `PipelineBlockedError` and stable reason-specific subclasses.
- Added terminal `BLOCKED` states for jobs, pipelines, and pipeline steps.
- A blocked job preserves retry count, records redacted structured diagnostics, releases its lease, and is never automatically retried.
- CERI barriers now stop on terminal unsuccessful upstream batches rather than treating FAILED/BLOCKED/CANCELLED/STALE as successful dependencies.

### Early preflight

Before fundamentals or other expensive stages, preflight now validates:

- readable/enabled CERI SEC guidance configuration;
- deployed versus explicitly ACTIVE processor compatibility;
- the complete ticker universe using exact-signature readiness;
- CIK/mapping/applicability state with per-ticker categories.

The late pre-enqueue CERI guard remains as defense in depth and uses the same canonical diagnostic engine.

### Explicit processor lifecycle

Migration `0050_sec_processor_promotion` adds an auditable release table with DEPLOYED, CERTIFIED, ACTIVE, and RETIRED states plus a partial unique constraint permitting one ACTIVE signature. Deployment registration never promotes. Certification records evidence and actor. Promotion locks release rows, frees the prior ACTIVE slot, and assigns the new signature atomically in one transaction.

### Worker fencing

Workers log worker/process/host/start time, Git identity, schema revision, loaded SEC signature, ACTIVE signature, and compatibility. Each ACTIVE SEC worker fences against the database ACTIVE signature before every claim, preventing a stale process from claiming new jobs after promotion.

### Durable stage resume

- A repaired initial preflight can retry the same pipeline from `VALIDATING_RUN`.
- Run 118 can resume from `CERI_PROVIDER_INGEST` after validating all preceding durable stages and row-count/context invariants.
- Completed scoring stages are not reset or replayed.
- Repeated resume attempts re-block the CERI stage without enqueue when readiness is still incomplete.
- The original failed job and all failed-attempt history remain intact; resume creates a new job.

### Operator feedback

The pipeline GUI and status JSON expose BLOCKED reason, signature, universe, ready/not-ready counts, and a context-aware retry/resume action. Route inventory documentation was regenerated.

## Database changes

- Applied Alembic revision `0050_sec_processor_promotion` to the live database.
- Added explicit SEC applicability fields to `ceri_companies`, defaulting fail-closed to REQUIRED.
- Added `ceri_sec_processor_releases` with certification evidence, actor/timestamps, and one-ACTIVE enforcement.
- Seeded the previously certified v2 signature ACTIVE and current v3 signature DEPLOYED.
- After the evidence gate passed, v3 was certified at `2026-08-20 02:50:33+02:00` and atomically promoted at `2026-08-20 02:50:35+02:00` by `codex-run118-recovery`; v2 was retired in the same promotion transaction.

## Certification and recovery evidence

### SHADOW v3 certification

The exact 180-ticker universe was split into four disjoint 45-ticker partitions. Every final report passed its complete check set under `sec-guidance:eed017654682a0c9`; first/repeat fingerprints matched within each partition and repeat traversal classified every discovered document as already terminal/skip-eligible.

| Partition | Tickers | Documents | First bytes | First elapsed | Repeat would-skip | Result |
|---|---:|---:|---:|---:|---:|---|
| 1 | 45 | 6,927 | 5,573,241,960 | 8,636.544 s | 6,927 / 6,927 | PASS |
| 2 | 45 | 7,929 | 6,013,159,369 | 7,307.617 s | 7,929 / 7,929 | PASS |
| 3 | 45 | 7,715 | 6,381,335,262 | 7,439.413 s | 7,715 / 7,715 | PASS |
| 4 | 45 | 8,122 | 6,006,729,335 | 7,449.117 s | 8,122 / 8,122 | PASS |

Transient SEC transport failures in the original attempts were repaired by repeat traversal and then re-certified with clean first/repeat executions for affected partitions. No parser, mapping, or database-integrity failure was waived.

### ACTIVE warm validation and aggregate gate

- ACTIVE warm covered all 180 tickers and discovered 30,693 terminal documents.
- Downloads: 0; would-skip: 30,693 / 30,693.
- Warm bytes: 33,746,757 versus 23,974,465,926 SHADOW-first bytes (approximately 99.86% reduction).
- Warm elapsed: 465.779 seconds versus the 8,636.544-second maximum partition wall time (approximately 94.61% reduction).
- Readiness: READY 180, all other diagnostic categories 0.
- Current-signature extraction states: 18,759 `COMPLETED_NO_RECORDS`, 11,934 `COMPLETED_WITH_RECORDS`, 0 nonterminal.
- Duplicate SEC source-record identity groups: 0.
- Aggregate gate: all 17 checks PASS.

The aggregate evidence is `output/run118_v3_sec_certification_aggregate.json` (with a Markdown rendering beside it).

### Checkpoint resume and downstream completion

- New resume job: `30995`, `FULL_PIPELINE`, `COMPLETED`, retry count 0.
- Original job `30993` remains `FAILED` with retry 4 / max 3; no history was deleted or rewritten.
- Pipeline identity remained `110`, upload run remained `118`, and resume started at `CERI_PROVIDER_INGEST`.
- All prior scoring/ranking stages remained durable and were not replayed.
- All downstream pipeline steps completed: CERI enqueue, setup capture, lifecycle evaluation, and Winner capture.
- All asynchronous CERI jobs completed: 32 provider batches, 16 normalize batches, 4 feature batches, 1 finalizer, 1 capture, 1 change-detection job, and 1 alert rebuild (56 / 56 `COMPLETED`; 0 failed/blocked/cancelled/stale).

Final state and output cardinalities:

| Item | Final value |
|---|---:|
| Upload run 118 | `COMPLETED`, 180 rows / 180 tickers |
| Pipeline 110 | `PARTIAL`, all 12 stages terminal-complete, 1 incomplete row, 0 IB failures |
| Fundamental / technical / combined | 180 / 180 / 180 |
| Rankings | 900 rows / 180 tickers |
| CERI score snapshots | 173 rows / 173 tickers |
| CERI change events | 82 |
| CERI alert events | 15 |
| Setup evaluation runs | 2 |
| Setup signal snapshots | 180 rows / 180 tickers |
| Setup lifecycle events | 276 |
| Winner prediction snapshots | 180 rows / 180 tickers |

The final read-only inspection is `output/run118_recovery_final.json`.

## Test evidence

- Initial diagnostic full suite: 1,751 passed, 9 skipped, 5 failed in 28m34s. All five were investigated; schema/route inventories and intentional worker-fencing isolation were corrected, and the end-to-end connection-exhaustion interference from concurrent certification was reproduced and cleared.
- Isolated end-to-end certification: 1 passed in 2m47s.
- Corrected direct failures: 4 passed.
- Affected-area suite including PostgreSQL CERI concurrency/idempotency: 107 passed.
- Latest preflight/batched/resume suite: 47 passed.
- Final full repository suite after recovery: **1,767 passed, 9 skipped, 0 failed, 14 warnings in 590.05 seconds (9m50s)**.
- Ruff across `app`, `scripts`, `tests`, and migration 0050: PASS.
- `compileall` across `app`, `scripts`, and `tests`: PASS.
- `git diff --check`: PASS (Windows line-ending notices only).

During the live aggregate run, PostgreSQL exposed an ungrouped selected column in the new duplicate-invariant subquery. The query was corrected to select a grouped identity column, Ruff passed, and the live aggregate then passed all 17 checks; the final full suite includes that correction.

## Implementation file inventory

Key new files:

- `app/services/pipeline_prerequisites.py`
- `app/services/ceri/sec/pipeline_preflight.py`
- `app/services/ceri/sec/processor_lifecycle.py`
- `app/services/ceri/sec/readiness_diagnostics.py`
- `alembic/versions/20260819_0050_sec_processor_promotion.py`
- `scripts/aggregate_sec_certification.py`
- `scripts/inspect_run_recovery.py`
- `scripts/manage_sec_processor.py`
- `scripts/resolve_sec_ciks.py`
- `tests/ceri/test_sec_pipeline_preflight.py`
- `tests/ceri/test_sec_processor_lifecycle.py`

Key modified areas include background job/worker retry and fencing, CERI batch barriers/workflow, pipeline execution/resume, lifecycle models, run routes/templates/JavaScript, route inventory, and the corresponding worker/pipeline/CERI/e2e tests.

## Remaining risks / limitations

- The resume implementation intentionally supports only `VALIDATING_RUN` restart and the proven run-118 `CERI_PROVIDER_INGEST` checkpoint. It does not pretend to resume arbitrary mid-stage failures.
- CERI provider ingest remains an asynchronous background workflow. Pipeline terminal status alone is not sufficient operational proof; child-workflow terminal status must also be monitored. This recovery verified both independently.
- A direct rollback command for a RETIRED processor release is not introduced; rollback would require an explicit audited lifecycle extension rather than silent status mutation.
