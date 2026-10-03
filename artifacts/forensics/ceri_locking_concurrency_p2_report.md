# CERI P2 locking, transaction-isolation, and feature-concurrency report

Date: 2026-10-03 (Europe/Zurich)

## EXECUTIVE VERDICT

- P2 CURRENT LOCKING MODEL REPRODUCED: PASS
- SHARED ROW CONTENTION PROVED: PASS
- SAFE LOCKING REDESIGN: PASS
- SOURCE MUTATION PROTECTION PRESERVED: PASS
- TWO FEATURE BATCHES CAN EXECUTE CONCURRENTLY: PASS
- EXACT JOB OWNERSHIP UNDER CONCURRENCY: PASS
- STALE WORKER FENCING: PASS
- CHECKPOINT ISOLATION: PASS
- FINALIZER RACE SAFETY: PASS
- P0 INVARIANTS PRESERVED: PASS
- P1 DIGEST/EVIDENCE PARITY PRESERVED: PASS
- MEMORY/RESOURCE GATE: PASS
- CONTROLLED PARALLEL BENCHMARK: FAIL (safe and exactly equivalent, but unstable and slower in the exact repeat)
- MEASURED FEATURE SPEEDUP: `0.811x` exact run (repeat range `0.811x`–`1.194x`)
- MEASURED FULL-CERI SPEEDUP: `0.950x projected` exact run (no production run)
- REAL PRODUCTION PIPELINE: FAIL (NOT RUN; value gate failed)
- REAL CERI CERTIFIED COMPLETION: FAIL (NOT RUN)
- RUN 11 MODIFIED: MUST BE NO
- RUN 13 MODIFIED: MUST BE NO
- RUN 14 MODIFIED: MUST BE NO
- P2 WORTH ENABLEING BY DEFAULT: NO

P2 is **safe but not worth enabling by default on this host**. The lock redesign removes reader/reader serialization without weakening source-body protection, and the constrained-worker mechanism preserves ownership. Exact serial/concurrent outputs match. However, the two production-sized parallel repeats ranged from a 19.4% improvement to a 23.4% regression. The corrected exact repeat took 870.297 seconds versus 705.609 seconds serially. On a two-physical-core/four-logical-CPU host, writer-manifest CPU contention overwhelms the removed database wait. The production gate therefore prohibited a live canary.

The existing Run 14 post-CERI failure, `HISTORICAL_EVIDENCE_UNAVAILABLE: resume artifact missing COHR:market_regime_snapshot`, remains out of scope and untouched.

## 1. Exact current locking and transaction model

### Preserved boundary

- Branch: `codex/ceri-authority-remediation`
- HEAD: `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae`
- Schema head: `0089_pipeline_execution_authority`
- Python: 3.12.2
- PostgreSQL: 18.3, Windows x86-64
- Effective isolation: PostgreSQL `READ COMMITTED`
- Host: four logical CPUs, two physical cores, approximately 16 GiB RAM
- Initial P0/P1 tracked diff was saved verbatim as Git blob `34da38768608d55144e8825af49bb7d3865ca222`; SHA-256 of the binary diff stream is `ecdb90d79455e53a8c57dd4b847d9f99e65ef97dbeac49d1af37e41164b25af4`.

The complete pre-P2 status, diff stat, file ownership map, runtime fingerprint, and immutable-run boundary are in `artifacts/forensics/ceri_p2_baseline_state.md`.

Final read-only recheck after all P2 work:

| Run | Pipeline state | CERI jobs | Feature manifests |
|---:|---|---|---|
| 11 | `FAILED`, `MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH` | jobs 263–292, 30/30 completed | 19: 83,167 / `8fc36165…4eba`; 20: 72,077 / `445537ff…54aa` |
| 13 | `FAILED`, `MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH` | jobs 327–357, 30/30 completed | 23: 83,406 / `29dab588…2455`; 24: 72,314 / `d4c6cf68…8c7` |
| 14 | `FAILED` only at the out-of-scope post-CERI COHR continuation | jobs 360–389, 30/30 completed | 25: 83,553 / `ef350d8b…0fd1`; 26: 72,449 / `d62243b4…aca4` |

These statuses, job ranges/counts, manifest IDs/counts, and fingerprints are unchanged from the preserved boundary. Every diagnostic transaction that touched production-sized evidence rolled back.

### Transaction trace

One feature job uses three distinct transactional roles:

| Role | Session/connection | Starts | Commits/ends | Locks/state |
|---|---|---|---|---|
| Durable claim | worker queue Session | `claim_next_job` | immediately after claim | locks worker registration row; claims one queued/recovering job with `FOR UPDATE SKIP LOCKED`; writes worker, lease owner, execution token |
| Financial calculation | independent fenced domain Session/connection | first source/context SQL before `prepare_batch` | worker completion commit, or rollback on failure | `READ COMMITTED`, read/write; source row locks survive the complete batch calculation and publication |
| Control plane | detached short Sessions/connections | lease/progress/checkpoint calls | each control update commits independently | token-conditional checkpoint/lease updates; does not commit or release financial source locks |

The financial path is:

`execute_feature_batch_job` → load retained manifest → `prepare_batch` → exact retained-source loads → manifest/body seal → 50 or 44 ticker loop → nested writers → one bounded full audit → final no-op refresh → handler return → token-fenced completion → financial Session commit.

There is no global advisory lock serializing feature work. Execution ownership protects each background-job row, so two different feature jobs can execute together. Before P2, they nevertheless serialized on source rows because every retained query used unqualified `FOR UPDATE`, including retry chunks and refresh/fallback queries. The raw Run-membership subquery also used `FOR UPDATE`.

The transaction was intentionally **not shortened**. P2 changes only compatibility and qualification of row locks; it keeps source authority alive until durable feature publication.

### Source tables and required locks

| Table | Rows A | Rows B | Shared | Old lock | Feature writes it? | Other mutation possible? | P2 lock |
|---|---:|---:|---:|---|---|---|---|
| `price_bars` | 75,908 | 65,944 | 1,508 | unqualified `FOR UPDATE` | No | Yes: provider/maintenance UPDATE or DELETE | `FOR SHARE OF price_bars` |
| `ceri_source_records` | 3,598 | 3,017 | 0 | unqualified `FOR UPDATE` | No | Yes: ingest/normalization/maintenance | `FOR SHARE OF ceri_source_records` |
| `ceri_estimate_snapshots` | 2,847 | 2,284 | 0 | unqualified `FOR UPDATE` | No | Existing rows are evidence; maintenance mutation remains technically possible | `FOR SHARE OF ceri_estimate_snapshots` |
| `ceri_earnings_actuals` | 352 | 306 | 0 | unqualified `FOR UPDATE` | No | Existing rows are evidence; maintenance mutation remains technically possible | `FOR SHARE OF ceri_earnings_actuals` |
| `ceri_catalyst_events` | 399 | 427 | 0 | unqualified `FOR UPDATE` | No | Normalization/maintenance | `FOR SHARE OF ceri_catalyst_events` |
| `ceri_catalyst_event_revisions` | 399 | 427 | 0 | unqualified `FOR UPDATE` | No | Revision creation is append-oriented, but existing bodies still need protection | `FOR SHARE OF ceri_catalyst_event_revisions` |
| `ceri_companies` | 50 | 44 | 0 | unqualified `FOR UPDATE` | No | Normalization/maintenance | `FOR SHARE OF ceri_companies` |
| `ceri_guidance_snapshots` | 0 | 0 | 0 | unqualified `FOR UPDATE` when present | No | Same retained-evidence rule | `FOR SHARE OF ceri_guidance_snapshots` |
| `raw_company_rows` membership support | 94 | 94 | 94 | subquery `FOR UPDATE` | No | Run membership can be maintained outside feature work | subquery `FOR SHARE` |

`raw_company_rows` is a membership authority, not a retained manifest member, so its 94 rows do not appear in the 83,553/72,449 source counts. That distinction explains why the first observed production-sized collision was not visible in the source-manifest overlap table.

The emitted retained SQL changed from the shape `SELECT ... FROM <table> ... FOR UPDATE` to `SELECT ... FROM <table> ... FOR SHARE OF <table>`. Qualification matters: it prevents an ORM query from locking other lockable relations or subquery rows incidentally. The raw membership subquery receives its own explicit `FOR SHARE`.

### Before/after lock matrix

| Resource | Old serial lock | Old parallel behavior | P2 lock | P2 parallel behavior | Mutation blocked? |
|---|---|---|---|---|---|
| Shared SPY price bars | `FOR UPDATE` | second reader waits | `FOR SHARE OF price_bars` | readers coexist | Yes: UPDATE and DELETE |
| Company-local price bars | `FOR UPDATE` | disjoint rows coexist, but over-strong | `FOR SHARE OF price_bars` | coexist | Yes |
| Source records | `FOR UPDATE` | disjoint today; future overlap would wait | qualified `FOR SHARE` | readers coexist | Yes |
| Estimates/earnings/catalysts | `FOR UPDATE` | disjoint today; future overlap would wait | qualified `FOR SHARE` | readers coexist | Yes |
| Company rows | `FOR UPDATE`; query also carried a locking membership subquery | outer rows disjoint, but shared raw membership serialized acquisition | company `FOR SHARE`; raw membership `FOR SHARE` | readers coexist | Yes |
| Job claim row | `FOR UPDATE SKIP LOCKED` | exactly one claimant | unchanged | exactly one claimant | Yes |
| Worker registration row | `FOR UPDATE` | quiesce/claim total order | unchanged | same | Yes |
| Advisory ownership locks | none for feature serialization | none | unchanged | none | N/A |

## 2. Exact Run 14 manifest overlap

Run 14 immutable manifests 25 and 26 were recalculated by primary-key identity and row fingerprint; no prior overlap count was reused.

| Table | Batch A IDs | Batch B IDs | Shared IDs | Shared % A | Shared % B |
|---|---:|---:|---:|---:|---:|
| `price_bars` | 75,908 | 65,944 | 1,508 | 1.987% | 2.287% |
| `ceri_source_records` | 3,598 | 3,017 | 0 | 0% | 0% |
| `ceri_estimate_snapshots` | 2,847 | 2,284 | 0 | 0% | 0% |
| `ceri_earnings_actuals` | 352 | 306 | 0 | 0% | 0% |
| `ceri_catalyst_events` | 399 | 427 | 0 | 0% | 0% |
| `ceri_catalyst_event_revisions` | 399 | 427 | 0 | 0% | 0% |
| `ceri_companies` | 50 | 44 | 0 | 0% | 0% |
| **All retained identities** | **83,553** | **72,449** | **1,508** | **1.804842%** | **2.081464%** |

All 1,508 shared rows are `SPY`, daily, from 2023-10-02 through 2026-10-02: 754 `ADJUSTED_LAST` and 754 `TRADES`. They exist in both bundles because the configured benchmark/reference series is deliberately added to every company batch. There are no shared provider-derived company-local rows and zero fingerprint mismatches.

The full primary keys and exact fingerprint for every shared row are in `ceri_p2_run14_overlap.json` (SHA-256 `b3d02ee1a3ea6cd662bd4b8be1c6eed702234509802b34efab828578aedb47a7`). The first shared identity is `price_bars.id=1`, SPY/ADJUSTED_LAST/2023-10-02, fingerprint `705669fa40d0bb931af6f1107f38ff58b73195851ad98d01853b98ed21a2fb51`.

## 3. Reproduced current contention

The pre-redesign, rollback-only experiment opened independent processes, Sessions, and PostgreSQL connections and performed the complete Run 14-sized `prepare_batch` acquisition for jobs 384 and 385.

- Holder A: PostgreSQL PID 16020, 83,553 rows, manifest fingerprint `ef350d8bf39b677921be68639fba87406a9abc99aba49a54b6922283be790fd1`, prepared in 19.520 seconds, then remained idle in transaction.
- Contender B: PostgreSQL PID 19748, waiting with `wait_event_type=Lock`, `wait_event=transactionid`, `pg_blocking_pids=[16020]`.
- Ungranted lock: `transactionid ShareLock` on transaction 5388953.
- Waiting statement: the `ceri_companies` query whose Run 14 membership subquery selected `raw_company_rows ... WHERE run_id = 14 FOR UPDATE`.
- Observed blocker transaction age: 23.665 seconds.
- B completed only after A rolled back; no durable data changed.

Thus, the earliest actual blocker was the 94-row shared `raw_company_rows` membership query, not the later SPY load. The SPY contention is independently proved on the first exact shared SPY identity (`price_bars.id=1`): two old-style `FOR UPDATE` readers conflict and the second timed out with SQLSTATE `55P03` after 0.524 seconds. It was guaranteed to become a second blocker had B passed membership acquisition.

Evidence: `ceri_p2_current_lock_contention.json` (SHA-256 `a6a307045c9f46f725fd2ec89a538cf1a209fd067197f4662a93fa4712d7d349`) and `ceri_p2_lock_mode_proof.json` (SHA-256 `837101d62287340400d7d2f4f01bce142b8c3c9837cee8fcd812567159d15bb4`).

## 4. Lock redesign alternatives evaluated

| Alternative | Decision | Evidence/reason |
|---|---|---|
| Qualified `FOR SHARE` | Selected | Concurrent readers are compatible; UPDATE and DELETE of admitted rows remain blocked through publication. |
| `FOR KEY SHARE` | Rejected | Real PostgreSQL test allowed a non-key `data_hash` UPDATE in 0.002 seconds. That can change an admitted physical body. |
| Remove row locks | Rejected | Under `READ COMMITTED`, another process can mutate a selected body after admission. Process-local invalidation cannot observe external SQL. |
| Move to `REPEATABLE READ` | Rejected | More invasive; snapshot visibility alone does not block concurrent physical-body mutation, and retry/phantom behavior would need a separate redesign. |
| Split benchmark evidence | Rejected | It addresses today's SPY overlap but not shared membership or future overlaps in other retained tables, and complicates one-manifest authority. |
| Unlock after immutable witness capture | Rejected for P2 | The witness detects identity/body divergence but does not prevent a concurrent writer from changing physical evidence before durable publication; proving a weaker model is outside P2. |

## 5. Selected design and correctness proof

All retained-source load, retry-chunk, refresh, and fallback lock sites now use SQLAlchemy `.with_for_update(read=True, of=<retained model>)`, which PostgreSQL emits as `FOR SHARE OF <table>`. The raw run-membership subquery uses `.with_for_update(read=True)`.

The invariant is unchanged: once an exact retained row is admitted, no ORM mutation, Core DML, raw SQL, provider/normalization worker, other pipeline, or maintenance process can UPDATE or DELETE that row before the financial transaction commits. The database enforces it across processes and connections. Inserts and new revisions can proceed, but they are not members of the already frozen exact manifest.

Rollback-only PostgreSQL proof on shared SPY row 1:

- second `FOR SHARE` reader completed in 0.0028 seconds;
- Core UPDATE was blocked and timed out with `55P03`;
- raw SQL DELETE was blocked and timed out with `55P03`;
- the row's before/after `data_hash` was identical;
- `FOR KEY SHARE` demonstrably allowed the forbidden non-key body update.

After redesign, both full Run 14-sized acquisitions completed while A held its transaction: A 22.408 seconds and B 20.655 seconds, with exact original bundle fingerprints and no blocker observed. Evidence: `ceri_p2_shared_lock_contention.json` (SHA-256 `971df7c6c9b5b06b9282ff8834c09390e83ba541d3ebf28ff5e810c46270541d`).

### Transaction lifetime before/after

| Boundary | Before P2 | After P2 |
|---|---|---|
| Source transaction starts | first context/source SQL in feature handler | unchanged |
| Last source load | end of `prepare_batch` | unchanged |
| First/last ticker | within the same financial transaction | unchanged |
| Bounded audit | once after ticker loop, same transaction | unchanged |
| Final refresh | same transaction; clean normal path is no-op | unchanged |
| Completion | worker token-fenced durable commit | unchanged |
| Lock release | commit/rollback after handler completion | unchanged |

Run 14 production batch walls were 415.017 and 325.038 seconds. In the exact P2 serial diagnostic, the directly measured authority spans were at least 384.263 seconds for A (`20.488 + 349.308 + 14.467`) and 305.924 seconds for B (`15.427 + 277.336 + 13.161`), plus setup/output. In the exact parallel diagnostic they expanded to approximately 859.827 and 773.531 seconds because of CPU contention. P2 did not shorten transaction authority to gain speed.

## 6. Files changed

P2-specific production changes:

- `app/services/source_mutation_authority.py`: qualified shared retained-row locks.
- `app/services/ceri/feature_rebuild_service.py`: shared raw membership lock.
- `app/services/background_job_service.py`: optional job-type claim allowlist.
- `app/services/background_worker.py`: propagate constrained job types through worker/control paths.
- `app/worker.py`: `--job-types` CLI allowlist.

P2-specific tests and diagnostics:

- `tests/integration/test_t14d_source_bundle_postgresql.py`
- `tests/integration/test_ceri_batched_workflow_v2.py`
- `tests/test_worker_cli.py`
- `scripts/profile_ceri_p2_concurrency.py`
- `scripts/benchmark_ceri_p2_parallel.py`
- `scripts/verify_ceri_p2_lock_modes.py`
- `scripts/profile_ceri_writer_manifest.py` (diagnostic context filtering only)
- `artifacts/forensics/ceri_p2_*`

The pre-existing P0/P1 dirty files remain layered and uncommitted. No reset, clean, checkout, rebase, commit, or push occurred.

## 7. Worker architecture

The smallest safe mechanism is an optional, externally launched constrained durable worker:

`python -m app.worker --worker-id <unique-id> --queues background --job-types CERI_FEATURE_BATCH`

The allowlist is applied inside the atomic `SELECT ... FOR UPDATE SKIP LOCKED` claim query, not after claim. Such a worker cannot claim provider, normalization, finalizer, capture, change, alert, or unrelated jobs. The existing worker remains unchanged and still supports the entire queue.

Critically, this mechanism is **implemented but not enabled in the supervisor/default configuration**. No second production worker was launched because the controlled value gate failed. Every batch retains its own process, Session, connection, source bundle, immutable source manifest, P1 cache, mutation declaration state, and transaction authority.

## 8. Ownership, lease, checkpoint, and retry proof

Real PostgreSQL concurrent claim testing raced two feature-only workers against one queued feature job and one unrelated finalizer. Exactly one feature claimant received the job, worker owner, and nonempty execution token; the second received no job; the finalizer remained queued and unowned.

Existing token predicates remain on progress, checkpoint, completion, partial, cancel, retry, and failure transitions. A stale token cannot update the current attempt. `FOR UPDATE SKIP LOCKED` prevents simultaneous ownership, lease expiry clears the old owner/token before deterministic recovery, and late completion is rejected.

Batch checkpoints are stored on their own job and are updated with that job's current token. Batch A cannot address batch B's job row. Parent cancellation is checked from both feature handlers; a cancelled feature saves only its own completed-ticker checkpoint and stops. Retry reloads the same durable manifest ID and validates every exact body/fingerprint before continuing.

## 9. Finalizer and certification race proof

`_require_terminal_stage` queries by exact `workflow_key` and job type, requires the exact expected count, defers while any member is queued/recovering/running, and rejects failed/blocked/cancelled terminal members. Only `COMPLETED` or `PARTIAL` membership may pass.

Consequences under every completion order:

- A-first, B-first, and nearly simultaneous completion remain blocked until both exact members are terminal-successful.
- A failed while B runs, or B failed after A completes, cannot certify.
- retry remains nonterminal and blocks the barrier.
- cancellation is unsuccessful terminal membership and blocks.
- a stale worker cannot make its old attempt terminal because completion is token-fenced.
- repeated finalizer execution uses the workflow-scoped capture request key and durable uniqueness/coalescing, so exactly one capture job exists.

Capture therefore sees complete certified membership; change detection and alert rebuild remain downstream of that one capture. P2 does not parallelize any of these stages.

## 10. Memory, CPU, and database resource model

| Metric | Exact P2 serial replay | Exact P2 parallel replay |
|---|---:|---:|
| Peak combined RSS | 940,802,048 B (897.2 MiB) | 1,604,960,256 B (1,530.6 MiB) |
| Peak combined private bytes | 992,722,944 B | 1,678,163,968 B |
| Minimum host available memory | 6,059,614,208 B | 5,495,640,064 B |
| Host CPU average | 49.429% | 93.389% |
| Host CPU peak | 100% | 100% |
| PostgreSQL lock-wait samples | 0 | 0 |
| Extra process/connection if enabled | 0 | +1 worker / +1 active feature connection |

Single-process peaks were 940,802,048 B for job 384 and 833,765,376 B for job 385. Concurrent peaks were 918,122,496 B and 811,196,416 B. Available memory remained above 5.49 GB, so the memory admission gate passes without bypassing any readiness guard.

CPU is the limiting resource. Both Run 14 batches were already approximately one full core each (97.220% and 97.506% process CPU/wall). Parallel average host CPU rose to 93.389% on only two physical cores. Writer-manifest CPU seconds also expanded materially under contention. PostgreSQL did not become the bottleneck after the lock fix: zero lock-wait samples, no deadlocks, and no serialization failures were observed.

## 11. Frozen serial-versus-parallel evidence parity

The corrected exact frozen benchmark executed real source authority and writer paths against Run 14 manifests, using independent processes/Sessions and rolling back diagnostic writes. The earlier diagnostic selected build-state rows only by company ID and could pick another calculation context; it was corrected to include session, mode, config, calculation version, ownership, and `calculation_context_id`. The earlier mismatch was a query defect, not an evidence defect.

| Identity | Serial | Parallel | Result |
|---|---|---|---|
| Job 384 source count | 83,553 | 83,553 | exact |
| Job 384 bundle fingerprint | `ef350d8b…0fd1` | `ef350d8b…0fd1` | exact |
| Job 384 semantic projection SHA-256 | `e5404a4f…d0550` | `e5404a4f…d0550` | exact |
| Job 385 source count | 72,449 | 72,449 | exact |
| Job 385 bundle fingerprint | `d62243b4…aca4` | `d62243b4…aca4` | exact |
| Job 385 semantic projection SHA-256 | `1d9b54b8…bfff` | `1d9b54b8…bfff` | exact |
| Per-company results and feature counts | reference | identical | exact |
| Input/output evidence hashes | reference | identical | exact |

The semantic projections include selected tickers, exact source count, bundle fingerprint, per-company feature payload/result counts, and every input/output evidence hash. Full SHA values and payloads are retained in the benchmark JSON files.

P1 path-comparison testing on HURN executed both optimized and reference paths in the same invocation. Native manifests, canonical bytes, and digests matched the reference at all compared writer boundaries. Cross-run writer digests are not expected to be stable because the existing manifest intentionally contains invocation-time state such as timing/completion metadata; the valid P1 proof is optimized versus reference under the same invocation.

## 12. Controlled performance benchmark

| Measurement | Run 14 serial baseline | P2 serial exact replay | P2 parallel exact | Change vs replay |
|---|---:|---:|---:|---:|
| Feature makespan | 740.441 s | 705.609 s | 870.297 s | +164.688 s / 23.34% slower |
| Batch A wall | 415.017 s | ~384.263 s authority span | ~859.827 s authority span | CPU-contended |
| Batch B wall | 325.038 s | ~305.924 s authority span | ~773.531 s authority span | CPU-contended |
| Batch A writer wall | 413.895 s instrumented batch | 349.308 s | 791.694 s | +442.386 s |
| Batch B writer wall | 324.018 s instrumented batch | 277.336 s | 689.572 s | +412.236 s |
| Batch A writer CPU | 402.390 s production | 338.984 s | 625.797 s | +286.813 s |
| Batch B writer CPU | 315.937 s production | 267.609 s | 532.094 s | +264.485 s |
| Writer SQL wall | 11.451 s production | 4.757 s | recorded in artifact | not limiting |
| Lock-wait samples | serial | 0 | 0 | removed |
| Pool wait | not separately instrumented | none observed | none observed | no pool bottleneck |
| Peak total RSS | not captured | 940.8 MB | 1,605.0 MB | +664.2 MB |
| Evidence parity | PASS | PASS | PASS | unchanged |

Exact overlap efficiency is `705.609 / 870.297 = 0.8108x`, versus the theoretical `~1.78x`. The earlier production-sized parallel repeat took 591.641 seconds against a 706.219-second serial repeat, or `1.1937x`, saving 114.578 seconds. Repeating the same workload with corrected exact evidence selection instead regressed by 164.688 seconds. Both parallel runs had zero sampled database lock waits and healthy memory; the 0.811x–1.194x range is host CPU/scheduling instability, not database serialization.

Therefore the controlled benchmark is marked FAIL under the requested usefulness/stability gate even though safety and semantic parity pass.

## 13. Run 14 versus P2 production benchmark

No P2 production pipeline was started. The instruction permits production only after a useful controlled improvement; that gate failed. Values below are deliberately not fabricated.

| Measurement | Run 14 P1 baseline | P2 production | Change |
|---|---:|---:|---:|
| CERI total | 2,465.386 s | NOT RUN | N/A |
| provider/normalize | 1,544.292 s | NOT RUN | N/A |
| feature wall | 740.441 s | NOT RUN | N/A |
| batch A wall | 415.017 s | NOT RUN | N/A |
| batch B wall | 325.038 s | NOT RUN | N/A |
| post-feature | 180.653 s | NOT RUN | N/A |
| feature seconds/ticker | 7.877 s | NOT RUN | N/A |
| feature SQL | 11.451 s | NOT RUN | N/A |
| persistence wrapper | 280.978 s | NOT RUN | N/A |
| writer manifest | 558.058 s | NOT RUN | N/A |
| named feature compute | 4.659 s | NOT RUN | N/A |
| peak worker count | 1 | NOT ENABLED | 0 |
| peak feature workers | 1 | NOT ENABLED | 0 |
| peak combined RSS | not captured | NOT RUN | N/A |
| lock wait | serial | NOT RUN | N/A |
| executed refreshes | 0 | NOT RUN | N/A |
| source refresh rows | 0 | NOT RUN | N/A |
| bounded audits | 2 | NOT RUN | N/A |

For decision support only, substituting the exact controlled parallel feature time into otherwise unchanged Run 14 stages projects `1,544.292 + 870.297 + 180.653 = 2,595.242 s`, or `0.9500x` overall (5.0% slower). The favorable repeat would project 2,316.586 seconds or 1.0642x. Neither is a production measurement, and the range is not stable enough to enable.

Historical context:

| Measurement | Run 11 | Run 13 P0 | Run 14 P1 | P2 exact controlled |
|---|---:|---:|---:|---:|
| CERI total | 13,065.931 s | 4,968.483 s | 2,465.386 s | 2,595.242 s projected |
| feature wall | 11,404.773 s | 3,091.732 s | 740.441 s | 870.297 s controlled |
| batch A | 6,219.333 s | 1,883.576 s | 415.017 s | ~859.827 s authority span |
| batch B | 5,185.440 s | 1,207.906 s | 325.038 s | ~773.531 s authority span |
| feature SQL | 154.640 s | 14.679 s | 11.451 s | no lock bottleneck |

## 14. P0 regression telemetry

| Metric | Serial job 384 | Serial job 385 | Parallel job 384 | Parallel job 385 | Required |
|---|---:|---:|---:|---:|---:|
| Assertion rows | 0 | 0 | 0 | 0 | 0 |
| Dirty rows | 0 | 0 | 0 | 0 | 0 |
| Full audit calls | 1 | 1 | 1 | 1 | exactly 1/batch |
| Full audit rows | 83,553 | 72,449 | 83,553 | 72,449 | exact source count |
| Refresh calls | 308 | 272 | 308 | 272 | bounded/no-op |
| Executed refreshes | 0 | 0 | 0 | 0 | 0 |
| Source refresh rows | 0 | 0 | 0 | 0 | 0 |
| Unexpected invalidations | 0 | 0 | 0 | 0 | 0 |

The 75k-row retry path still chunks the exact manifest without exceeding PostgreSQL parameter limits. Retry with a changed source body remains fail-closed. ORM, Core, raw SQL, same-transaction, cross-Session, and physical-commit mutation paths remain covered by P0 regression tests.

## 15. P1 regression telemetry

| Metric | Job 384 serial | Job 385 serial | P2 effect |
|---|---:|---:|---|
| Writer calls | 100 | 88 | unchanged |
| Writer manifest wall | 316.455 s | 244.896 s | format/path unchanged |
| Fingerprint wall | 122.166 s | 95.678 s | format/path unchanged |
| Canonical bytes | 3,961,855,172 | 3,035,600,033 | unchanged semantics |
| P1 cache scope | process/bundle/transaction | process/bundle/transaction | never shared across workers |

P2 does not alter canonical evidence format, source-value cache semantics, writer declaration/fence boundaries, nested outer/inner writer identities, or digest computation. The remaining writer serialization cost is explicitly out of scope.

## 16. Test commands and results

Focused progression used real PostgreSQL where concurrency semantics mattered:

1. Worker CLI/claim/worker unit gate: `pytest tests/test_worker_cli.py tests/test_background_job_service.py tests/test_background_worker.py -q` → **82 passed**.
2. Focused authority/evidence/worker suite: `pytest tests/test_source_mutation_authority.py tests/test_canonical_evidence_serializer.py tests/ceri/test_batched_workflow_v2.py tests/test_worker_cli.py tests/test_background_job_service.py tests/test_background_worker.py -q` → **127 passed**.
3. PostgreSQL concurrent finalizer, two-feature claim, stale late checkpoint selection → **3 passed, 20 deselected**.
4. PostgreSQL lock compatibility plus 75k retry selection → **3 passed, 17 deselected**.
5. Selected systemic P0/P1 regression suite (multi-ticker checkpoints, changed-source retry rejection, crash/retry manifest order, byte/digest parity, invalidation, commit refresh, cross-Session, same-transaction DML, physical commit, Run 9 checkpoint, shared authority) → **12 passed, 26 deselected**.
6. Ruff on P2-changed Python files → **all checks passed**.
7. Rollback-only lock-mode script → old exclusive reader blocked; shared readers compatible; Core UPDATE and raw DELETE blocked; KEY SHARE rejected; durable row unchanged.
8. Frozen production-sized serial and parallel benchmark → exact semantic parity PASS; performance gate FAIL.

An existing broader-suite decorator expectation involving `WF_CERI_CONSENSUS_ATTACH` predates and is unrelated to P2; it was not changed to make this result pass.

## 17. Failure-injection results

| Scenario | Result |
|---|---|
| A fails while B runs | B may finish its own job; unsuccessful/nonterminal exact membership keeps finalizer blocked. PASS. |
| B fails after A completes | Finalizer rejects the unsuccessful terminal member. PASS. |
| A/B worker crash | Lease recovery assigns a new token; exact durable manifest/checkpoint is retained. Existing crash/retry regression passes. |
| Cancellation during feature work | Current owner checkpoints its own completed tickers, then fences/stops; parent cannot certify partial membership. PASS. |
| Retry one batch | Same manifest ID/order and exact body validation; changed source is rejected. PASS. |
| Stale worker after retry | Real PostgreSQL late-checkpoint/token test rejects the old owner. PASS. |
| Database disconnect | Financial Session rollback prevents partial publication; durable job transition remains token-conditional. Covered by existing worker failure and physical-connection authority regressions, not by a new full process-kill production run. |
| Lock timeout | PostgreSQL `55P03` tests fail/rollback without mutation. PASS. |
| Deadlock | None observed in old or redesigned full-size acquisition; redesigned run had zero lock waits. |
| Duplicate completion/finalizer | Token fencing plus capture request-key uniqueness/coalescing produces one capture. PASS. |
| Cross-run contamination | Workflow-key membership, manifest ID, calculation context, Session-local bundles, and corrected exact-context evidence queries prevent “latest row” crossover; selected systemic regression passes. |

No failure was repaired by manual database editing, and no forensic run was resumed or mutated.

## 18. Remaining risks

1. Performance is dominated by P1 writer-manifest/fingerprint CPU work, not SQL. Two processes contend for the same limited physical CPU and can lengthen both transactions substantially.
2. The worker job-type allowlist is safe in code but has no production supervisor/admission policy yet. Enabling it later must include unique worker identity, health monitoring, connection capacity, memory admission, and rollback instructions.
3. Shared locks intentionally make source writers wait for a long financial transaction. This is the same mutation-protection contract as before but with reader compatibility; a future high-frequency source writer could still experience long waits.
4. Exact parity is rollback-diagnostic parity, not a new durable production certification. That is sufficient for the controlled gate but not a substitute for a canary if hardware changes make performance stable.
5. Database-disconnect behavior relies on established transaction/worker paths and focused regressions; P2 did not execute a destructive OS-level dual-worker kill during a live pipeline.
6. Run 14's later COHR continuation defect remains unresolved by design and must not be attributed to CERI P2.

## 19. Recommendation

Keep the qualified `FOR SHARE` redesign: it is a strict concurrency improvement with the same source-mutation guarantee and better lock targeting.

Keep the job-type-constrained worker capability available but **disabled by default**. Do not add it to the production supervisor on the current two-physical-core host. Its complexity and roughly 664 MB incremental peak RSS buy an unstable result whose exact repeat was slower.

Reconsider enablement only on a host with at least two demonstrably independent physical cores available to CERI after normal services, then repeat the exact frozen benchmark at least three times under representative load. Require exact semantic hashes on every repeat and a stable, meaningful feature saving (not a single favorable sample) before a new production canary. Only after that gate passes should a new 94-ticker pipeline be allowed to reach normal durable CERI `CERTIFIED`.

The next performance target, if desired, is the approximately 558 seconds of writer-manifest work, but it requires its own P3 same-input/same-bytes/same-digest proof and must not be mixed into P2.
