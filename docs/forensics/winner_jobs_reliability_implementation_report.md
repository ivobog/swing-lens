# Winner Jobs Reliability Implementation Report

Date: 2026-08-19

Baseline commit: `72a45ce31b775a255c390c207dd2a31f73e2f3a6`

Implementation branch: `codex/fix-main-ci-h5-ceri`

## Scope and authority

The SRS was treated as the functional/reliability authority and the SDD as the implementation authority. Both documents were read in full before code changes. The implementation was reconciled against the repository, Alembic history, PostgreSQL behavior, and the pre-change test suite (`230 passed`). No production backfill or current-data cohort rebuild was executed.

## Forensic defect matrix

| Requirement / defect | Baseline evidence | Status before remediation | Implemented remediation | Migration | Tests |
|---|---|---|---|---|---|
| Material evidence identity | `job_handlers.py` constructed refresh identity from `_utcnow()` | Confirmed | Durable monotonic four-part evidence watermark; request clock excluded from identity | refresh state/generation tables | deterministic hash, 0.227-second no-op, PostgreSQL revision tests |
| Refresh rescored all history | `WinnerCohortRefreshService` looped `_eligible_predictions()` and called `create_latest_rescore()` | Confirmed | Refresh is now generation-level cohort materialization; targeted rescore is a separate bounded job | generation FK on estimates | no historical estimate writes during refresh; unique-cohort performance test |
| Partial state could be served | Cohort statistics had no build/publication boundary | Confirmed | `BUILDING -> READY -> PUBLISHED`, with `FAILED/CANCELLED/SUPERSEDED`; atomic pointer publication | generation lifecycle/state | partial publish rejection and cancellation tests |
| Full evidence query per prediction | `EvidenceService.load_evidence()` was entered for each rescore | Confirmed | One canonical frozen evidence universe per generation/slice, then in-memory L0-L5 derivation | source indexes | 15,000-row fixture; L0-L5 parity suite |
| Manifest membership N+1 | Per-member existence query followed by insert | Confirmed | Content-addressed shared manifests and one bulk `ON CONFLICT DO NOTHING` member insert | manifest member table/indexes | 1,000-member one-statement test; exact reproduction integration |
| Active request lost newer demand | Queue coalesced request keys but carried no durable desired version | Confirmed | Stable request key plus independently locked desired watermark; publication reports a newer desired hash and defers continuation | refresh state unique contract | concurrent enqueue/coalescing PostgreSQL tests |
| Redundant clock rebuild | A different refresh cutoff created a different latest-rescore identity | Confirmed | Generation key is contract plus evidence watermark only | generation unique key | 227 ms identity regression test |
| Unbounded worker occupation | Refresh handler ran an unbounded Python loop | Confirmed | Configurable group/wall-clock slices (100 groups/45 s default) with stable checkpoint and `JobDeferred` | generation checkpoint fields | one-group resume and uninterrupted-equivalence scenario |
| Latest rescore coupled to refresh | Latest rescore was the refresh mechanism | Confirmed | Explicit `WINNER_LATEST_RESCORE`, frozen target manifest, 1-500 targets/slice, max 10,000 safety cap, no all-history default | generation identity index | one-row slice/resume/dedup PostgreSQL scenario |
| Unsafe historical self-inclusion | A historical prediction could be rescored against evidence containing itself | Confirmed design gap | Manifest/episode self-inclusion produces explicit insufficient status; no probability is fabricated | shared manifest member episode identity | reproduction and insufficient-probability assertions |
| Maturation database/bar N+1 | Prediction, target-stop, ticker, SPY, and sector data could be loaded per outcome | Confirmed | Batch context loads predictions, target-stop rows, and all bars set-wise; SPY and sector proxies shared; sector config resolved once per unique sector | H5 due/retry index | 3,000-outcome / 3-SQL PostgreSQL test |
| Retryable data indistinguishable from worker death | Pending prerequisites drove ambiguous partial status | Confirmed | `scan_completed`, `deferred_pending_h5`, `unvisited`, `failed`, reason counts, last full scan and last zero backlog | retry/operational columns | 3,439/2,328/1,111 status regression |
| Broad exception swallowing | Per-record loop could hide programming/database failures | Confirmed | Expected invalid/pending results are classified; unexpected exceptions abort and persist a terminal reason code | processing run terminal reason | programming-error propagation test |
| Final-completion-only fencing | Generic completion was fenced, but Winner side effects were not consistently guarded | Partially fixed baseline | Lease guard before/after batch commit, READY/publish, maturation batch, rescore prediction, and checkpoint boundaries | attempt/generation linkage | stale owner cannot publish PostgreSQL test |
| Recovery left misleading RUNNING domain runs | Stale generic jobs did not close `WinnerProcessingRun` | Confirmed | Recovered attempts become `LOST`; new attempts supersede abandoned RUNNING rows without deleting history | attempt/supersession fields | stale recovery PostgreSQL test |
| Outcome revision path not integrated | Revision primitives existed but no bounded registered revision job fed cohort demand | Partially fixed baseline | Registered bounded revision check; material current revisions advance the same watermark | watermark source indexes | current revision/watermark tests |
| Compatibility replay not material demand | Approved eligibility/replay writes did not advance cohort demand | Confirmed | Pre-1.1 approval persistence advances the same deterministic watermark | eligibility/replay maxima in state | idempotent watermark source tests and existing replay suite |
| Serving selected timestamp-latest rows | Latest resolver ordered by cutoff without a published-generation authority | Confirmed | V2 serving resolves the contract's published generation; rollout-off mode serves legacy null-generation estimates only | generation/published indexes | published-state and exact-rescore integration |

## Architecture

Before:

```text
maturation -> utcnow cutoff -> every eligible historical prediction
           -> repeated full evidence load -> latest rescore + copied members
```

After:

```text
material outcome / approved replay
  -> locked desired evidence watermark
  -> coalesced WINNER_COHORT_REFRESH
  -> frozen generation (contract + watermark + cutoff)
  -> one evidence universe
  -> L5, L4 ... L0 unique cohort statistics
  -> validate
  -> atomic published-generation pointer
  -> optional bounded WINNER_LATEST_RESCORE for explicit current targets
```

## Schema and index changes

Migration `20260819_0049_winner_jobs_reliability.py` is additive. It creates refresh state, cohort generations, shared manifest members, generation links, attempt/recovery metadata, and maturation retry metadata. Historical estimates and outcome revisions are not rewritten.

Indexes were added only for exercised queries:

- H5 current pending/due/retry selection;
- current/config-compatible prediction selection;
- generation source target-stop selection and forward join;
- unique refresh contract, generation key, generation/statistic key, and latest published lookup;
- content-addressed manifest/member identities and exact revision lookup;
- generation-aware latest-rescore identity;
- processing-run generation/attempt lookup.

The existing active background request-key partial unique index and `IntegrityError` fallback were retained and verified with concurrent PostgreSQL enqueue attempts.

## Watermark and generation contracts

The watermark is the monotonic tuple:

```text
(forward revision row id,
 target-stop revision row id,
 eligibility decision row id,
 training replay row id)
```

Only material matured/current labels and versioned approved compatibility artifacts contribute. Re-observing the same rows produces the same hash. Generation identity is the canonical hash of the full training contract plus this tuple; timestamps are metadata. The training cutoff is anchored one microsecond after the material observation boundary while revision IDs remain the exclusion fence. The legacy decision-time path retains strict `< cutoff` behavior.

Generation publication locks refresh state and changes the previous published generation to `SUPERSEDED`, the replacement to `PUBLISHED`, and the serving pointer in one transaction. A cancelled/failed replacement cannot move the pointer.

## Queue, checkpoint, fencing, and recovery

- Refresh demand is durable even if an older stable request key is active.
- Cohort checkpoint identity is generation ID plus last cohort level/key and validated counts, never a Python list offset.
- Rescore freezes explicit prediction IDs into the job payload and checkpoints the last stable prediction ID.
- Heartbeat fencing commits the preceding domain batch and checkpoint together. A failed execution-token compare rolls back uncommitted Winner writes.
- Processing runs distinguish logical job, execution attempt, and cohort generation. Lost attempts are retained as `LOST`/`SUPERSEDED` with reason codes.
- Fatal invariant, database, lease, schema, programming, publication, and identity errors leave the per-record continuation path and fail the attempt.

## Maturation changes

H5-first deterministic ordering remains. Retryable missing entry/horizon/due bars remain `PENDING` with an explicit reason and retry time; invalid OHLC/entry inputs are `EXCLUDED`. Missing SPY/sector comparison data remains a warning, with comparison fields and beat flags left null. It does not fabricate comparison results or block an otherwise valid target/stop label.

## Probability and audit invariants

The cohort statistics service and thresholds were not changed. Existing eligibility, point-in-time, compatibility, quality, rolling-window, and episode-independence stages remain in the evidence funnel. Fundamental, technical, combined, and ranking scores are untouched. Decision-time estimates remain immutable. V2 estimates reference shared immutable manifest members with exact forward and target-stop revision IDs; reproduction falls back to those shared members.

## Performance evidence

Deterministic cohort fixture:

| Metric | Previous design lower bound | Generation design |
|---|---:|---:|
| Historical evidence | 15,000 | 15,000 |
| Current run predictions | 200 | 200 |
| Historical refresh membership work | 225,000,000 | 90,000 |
| Unique cohort statistics | per prediction | 253 total |
| Cohort grouping runtime | not retained | 1.914897 s |
| Materialization order | repeated | L5 first |

The membership-work ratio is 2,500:1. The regression threshold requires at least 1,000:1 and would fail the old all-history-per-prediction design.

The PostgreSQL H5 fixture contains 3,000 current pending outcomes. Its batch context uses exactly three SQL statements regardless of row count: predictions, current target-stop rows, and all ticker/SPY/sector bars. The test completed in 12.18 s including clean migration and 6,000 fixture inserts. Shared membership persistence uses one bulk statement for 1,000 members and zero per-member existence queries.

Peak memory is not instrumented by current project tooling. Production `EXPLAIN (ANALYZE, BUFFERS)` was intentionally not run against uncontrolled data.

## Verification

- `python -m pytest tests/winner_probability -q`: **198 passed**
- background worker/job/queue suite: **42 passed**
- PostgreSQL reliability/concurrency/performance suite: **6 passed**
- clean PostgreSQL Alembic upgrade: **1 passed**
- 15,000-row performance regression: **1 passed**
- full repository suite: **1,733 passed, 9 skipped**
- `python -m ruff check app tests`: **passed**
- `git diff --check`: **passed**

Warnings are the existing Starlette/httpx, sqlite datetime-adapter, and Alembic path-separator deprecations. No test failure is being hidden as pre-existing.

## Rollout and remaining risks

1. Automatic cohort refresh and v2 serving default **off**, as required by SRS 5.23/SDD phase 0. Startup rejects auto-refresh without the v2 generation gate. Enabling remains an explicit operator action after review.
2. No production data was scanned, explained, backfilled, or rebuilt. A production preview/EXPLAIN and observed memory profile remain operational rollout work.
3. Historical targeted rescore with leave-one-episode-out is deliberately not implemented. Such targets fail closed as insufficient; `AS_OF_REPLAY` remains the supported retrospective workflow.
4. The default 45-second slice is a wall-clock guard between groups. An exceptionally expensive single unique cohort must be diagnosed rather than transactionally split.

Verdict: **PASS_WITH_FINDINGS**. The reliability architecture and automated certification pass; findings are explicit rollout/production-observation gates, not weakened correctness behavior.
