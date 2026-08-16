# CERI Feature Rebuild Performance Remediation Certification

Observed/updated: 2026-08-15 15:30 Europe/Zurich

Implementation worktree: `C:\Users\Ivica\Documents\swing-lens-feature-rebuild-perf`

Branch: `codex/perf-ceri-feature-rebuild`

Base commit: `62349dd8642294b202f2bdd511bd9c11a88d973b`

Implementation identifier: `batch-prefetch-v1`

## Verdict

The six requested architectural optimizations are implemented and pass the CERI unit,
golden, Run 102, workflow-parity, idempotency, incremental, partial-failure, resume,
cancellation, query-bound, migration, and disposable-PostgreSQL tests described below.
No CERI calculation version, scoring configuration, queue type, request key, payload,
checkpoint shape, or feature identity was changed.

The live worker was not changed, stopped, restarted, or migrated. Strategy B was selected
because run 108 still had a feature job actively writing with the old code and the worker
has no externally callable drain/pause mechanism. No production timing is claimed.

## Root causes removed

| Old behavior | New behavior | Files |
| --- | --- | --- |
| Every ticker loaded complete estimate, earnings, guidance, catalyst, revision-feature, and derived-feature tables. | One company-scoped query per evidence family builds a shared batch context. | `feature_rebuild_service.py`, `batched_job_handlers.py` |
| Revision and derived identities scanned complete Python lists. | Typed identity-key dictionaries provide O(1) lookup; compatibility helpers issue exact scoped queries. | `feature_rebuild_service.py` |
| Each metric/slot/window re-queried estimates and sometimes source records. | Estimates and minimal source provenance load once; `CeriPointInTimeQuery` indexes `(company_id, metric)` once. | `feature_rebuild_service.py`, `point_in_time_query.py` |
| The feature handler called a cold rebuild for every ticker. | It prepares one context for all remaining tickers and reuses it while retaining per-ticker processing runs, savepoints, commits, results, and checkpoints. | `batched_job_handlers.py`, `feature_rebuild_service.py` |
| Event selection reloaded earnings, guidance, and catalysts. | Latest-event selection consumes the batch indexes. | `feature_rebuild_service.py` |
| Stock bars and the benchmark were queried for every company. | One batch query loads all ticker and benchmark daily bars; calculations accept supplied bars. | `feature_rebuild_service.py`, `price_response_service.py` |
| Revision rows and derived rows flushed one at a time. | Each company uses PostgreSQL executemany `INSERT ... ON CONFLICT DO UPDATE` per output family and one final flush inside a savepoint. | `feature_rebuild_service.py`, `price_response_service.py` |

## Optimization implementation

1. Scoped DB access: company/run/ticker filters are applied in SQL. Existing revision,
   derived, price, and build-state rows are restricted to company IDs, session, config,
   and calculation version.
2. Batch rebuild: old queued payloads still contain the same `tickers`, `run_id`, workflow
   key, expected normalization count, and checkpoint metadata. The handler resolves only
   remaining tickers, creates one shared context, and persists/checkpoints each ticker
   independently.
3. Revision preload: estimates are indexed by `(company_id, metric)`. Source-record rows
   load only the provider, correction, timestamp, identity, and evidence-hash columns used
   by comparison rules and the fingerprint.
4. Incremental fingerprint: the SHA-256 input manifest includes company identity,
   session/mode/range, config/calculation identity, canonical estimates, earnings,
   accepted/unaccepted guidance state, catalysts/current revisions, relevant source
   provenance, stock bars, and benchmark bars. A skip also verifies a fingerprint and
   count of the currently durable output manifest, preventing a missing/corrupt output
   from being skipped.
5. Bulk UPSERT: revision, derived, price-response, and build-state families use their
   existing named unique constraints (or the new build-state constraint). Company-level
   savepoints prevent one invalid company from rolling back its peers.
6. Price cache: all qualifying cached IB/IBKR daily bars for the batch plus benchmark are
   loaded once. No network/provider call exists in this path.

## Schema changes

Migration: `0047_ceri_feature_rebuild_performance`, additive on top of
`0046_owpe_pre11_training_compatibility`.

New table `ceri_feature_build_states`:

- unique identity: company, as-of session, historical mode, config hash, calculation version;
- deterministic input and output evidence hashes;
- output feature count, implementation version, successful completion timestamp;
- company/session lookup index.

New indexes:

- `ix_ceri_revision_features_batch_identity` on company/session/config/calculation;
- `ix_ceri_derived_features_batch_identity` on company/session/config/calculation.

No existing table, constraint, row, or index is dropped or rewritten.

## Compatibility

Existing queued runs 105-110 are payload-compatible without recreation. The job type,
workflow/request keys, run ID, ticker list, expected normalization count, processing-run
request keys, checkpoint metadata, coalescing behavior, and completed-ticker resume
behavior are unchanged. A migration is required before the new handler starts because it
reads/writes the additive build-state table.

## Queue cutover decision

**Strategy B used.** At 2026-08-15 15:30:02 +02:00 job 30545 for run 108 was RUNNING on
`local-background-worker`, had 25/50 checkpointed tickers, heartbeat 15:26:46, and lease
through 15:41:46. Six more run-108 feature jobs, all run-109 feature jobs, and all run-110
feature jobs remained queued. The worker only exposes an in-process stop event; no safe
external finish-current-then-pause-dequeue control was found. The live checkout, worker,
database schema, queue statuses, priorities, and jobs were left untouched.

## Current runs

| Run | Feature jobs old code | Feature jobs new code | Feature complete | Snapshot state | Notes |
| --- | ---: | ---: | --- | --- | --- |
| 105 | 4 completed | 0 | Yes | 1 queued | Left intact; no feature rerun |
| 106 | 4 completed | 0 | Yes | 1 queued | Left intact; no feature rerun |
| 107 | 5 completed | 0 | Yes | 1 queued | Left intact; no feature rerun |
| 108 | 2 completed, 1 running (25/50), 6 queued | 0 | No | Not created; finalizer queued | Old worker continues |
| 109 | 5 queued | 0 | No | Not created; finalizer queued | Provider/normalization work also queued |
| 110 | 9 queued | 0 | No | Not created; finalizer queued | Provider/normalization work also queued |

## Test evidence

- Ruff on all changed Python/migration/test files: pass.
- `tests/ceri`: pass, including feature, point-in-time, revisions, earnings/surprise,
  catalysts, confidence, price response, Golden verticals, Run 102 golden certification,
  controlled replay, and workflow/restart/idempotency coverage.
- `tests/integration/test_ceri_batched_workflow_v2.py`: pass on disposable PostgreSQL,
  including legacy semantic fingerprint versus batched output, migration, bulk UPSERT,
  second-run skip, and query bound.
- Final combined CERI and PostgreSQL workflow run: 365 passed, 0 failed. The final
  focused feature/resume/cancellation run also passed all 19 tests.
- Incremental tests: unchanged skip, changed estimate, changed price bar, changed config.
- Failure/recovery tests: per-company failure isolation, old checkpoint resume, cancellation
  never checkpoints the unfinished ticker.
- Broad single-run browser E2E: FAIL, 748/755 comparisons passed. Five failures concern
  unrelated Winner Evidence maturation/graph behavior. Two CERI assertions required at
  least one alert and an acknowledgement action; the run produced zero change events, so
  no alert existed. Eight CERI snapshots, CERI exports, GUI/DB feature comparisons,
  lineage, isolation, and integrity checks passed. Evidence directory:
  `test-results/single-run-certification/20260815T132648Z-446a839a`.

## Optimized-only performance evidence

This is a deterministic disposable-PostgreSQL 50-company architectural benchmark with
one canonical EPS slot per company. It is not production evidence and was not compared by
re-running old code.

| Metric | First build | Unchanged rerun |
| --- | ---: | ---: |
| Tickers | 50 | 50 |
| Wall time | 1,049 ms | 61 ms |
| Seconds/ticker | 0.02098 | 0.00122 |
| Companies rebuilt | 50 | 0 |
| Companies skipped | 0 | 50 |
| SELECTs | 11 | batch-scoped |
| UPSERT/write statements | 200 | 0 feature writes |
| Context load | 89 ms | included |
| Persistence | 790 ms | 0 ms |
| Revision compute | 22 ms | 0 ms |
| Confidence compute | 1 ms | 0 ms |
| Price-response compute | 2 ms | 0 ms |
| Other family compute | <1 ms each | 0 ms |

Rows loaded on the first build: 50 companies, 50 estimates, 50 minimal source records,
and zero earnings/guidance/catalyst/existing-feature/price-bar rows. The SELECT count is
constant by family rather than ticker × metric × slot × window.

The historical operational reference remains 40-45 minutes per 50-ticker production
batch (~41.5 seconds/ticker). The synthetic result demonstrates removal of the access
pathology but does not substitute for the first naturally queued production measurement
after a later safe cutover.

## Remaining bottlenecks

1. Per-company recovery-safe UPSERT/savepoint work dominated the synthetic run (790 ms of
   1,049 ms). Small multi-company chunks could reduce round trips later, but would enlarge
   failure rollback scope.
2. Canonical estimate materialization and fingerprint serialization will dominate for
   companies with long evidence histories.
3. Per-ticker processing-run completion and durable checkpoint commits remain intentional
   recovery costs.
4. Price-bar volume can dominate context memory for production histories; a safe event-
   derived date bound is the next likely read optimization.

## SQL Flight Recorder

The application-wide recorder is implemented in `app/observability/db_monitor.py`, with
offline analysis in `app/observability/db_monitor_analysis.py` and
`scripts/analyze_db_monitor.py`. Settings are defined in `app/settings.py` and documented
in `.env.example` and `docs/operations/database_monitor.md`.

- Sink: bounded in-memory queue to process-specific, daily/size-rotated JSONL under
  `logs/db-monitor`; default retention is 8 days. No trace row is written to PostgreSQL.
- SQLAlchemy hooks: `before_cursor_execute`, `after_cursor_execute`, `handle_error`, plus
  count-only ORM `after_flush` telemetry.
- HTTP correlation: FastAPI middleware with propagated/generated request ID and task-safe
  context reset in `finally`; statement and request-summary records include resolved route.
- Job correlation: context immediately around the actual handler with job/type/run/worker/
  workflow values copied only when present; reset and job-summary emission occur in
  `finally`.
- Thresholds: slow at 100ms; compact full application stack at 250ms; all-SQL full stacks
  remain an explicit, default-off short-diagnostic option.
- Parameter policy: no raw bind/ORM values; only normalized structure and parameter
  count/type/shape. Fingerprints are SHA-256 of whitespace/literal/comment-normalized SQL.
- PostgreSQL status: `pg_stat_statements` is available but not installed or preloaded;
  enabling it requires a restart and was deferred because one live job was active.
  `auto_explain` is unavailable/not preloaded and remains off.
- Long-query sampler: implemented using a separate, uninstrumented connection with a
  1500ms threshold and 10-second cadence; default off.
- GUI: not added. The mandatory streaming CLI provides bounded aggregate and fingerprint-
  detail reports without introducing a new local-admin surface during the live-job window.

Tests cover SELECT/INSERT/UPDATE/error capture, nested execution timing, HTTP and job
context/reset, threshold-gated stacks, parameter safety, file-only/nonrecursive output,
rotation/retention, telemetry-writer failure isolation, incomplete-tail analysis,
fingerprint rankings, route/job summaries, N+1 candidates, and write-heavy aggregation.
The 11 recorder tests and 2 analyzer tests pass. The post-integration broad unit lane passes
1,589 tests (103 slow/integration/e2e/external tests deselected), Ruff passes across the
application and all changed Python files, and the disposable-PostgreSQL CERI workflow lane
passes all 5 tests. A read-only live sampler smoke check also completed successfully and
returned no query above the configured threshold at that instant.

Sanitized examples of an HTTP query, background-job query, slow query with stack, request
summary, and job summary are maintained in `docs/operations/database_monitor.md`. The
`CERI_FEATURE_BATCH` telemetry now embeds a live `sql_monitor` snapshot containing wall
time, SQL counts by operation, SQL total/max, unique fingerprints, most repeated query,
top cumulative queries, duplicate count/N+1 signal, and flush counts alongside ticker and
feature-specific timings. The first naturally queued optimized batch after safe cutover
will populate production values; the implementation report does not wait for the one-week
observation period.
