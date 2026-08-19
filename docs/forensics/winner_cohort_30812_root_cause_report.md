# Winner cohort incident 30812 root-cause report

## Conclusion

Job 30812 did not execute the Winner reliability remediation. The live worker had imported the pre-remediation handler, whose `WINNER_COHORT_REFRESH` implementation iterated every eligible prediction and called `create_latest_rescore()` for each one. Each rescore rebuilt and filtered the all-history evidence universe. That is an all-history rescore workload, not the reported 390-evidence / 42-cohort generation workload.

The worker started at 2026-08-18 19:49:30+02:00. Job 30812 was created at 2026-08-18 00:08:04+02:00, first claimed at 19:49:32, and claimed for attempt 2 at 2026-08-19 12:42:04+02:00. The reliability commit `2438e6e17979dbfec4b41189fcba2e7a14570b87` was committed at 12:42:20, 16 seconds after attempt 2 began. The already-running Python worker could not hot-reload it.

The remediation also contained a separate latent SQLAlchemy bug: generation evidence remained as ORM objects while the same domain `Session` was committed by heartbeats between groups. With the default `expire_on_commit=True`, this produced a deterministic lazy-reload storm. The live job could not be an execution of that new materializer because it created no refresh-state or generation row, but the latent bug would have reproduced the same query signature after rollout. It has been fixed before the v2 worker was activated.

## Incident timeline

| Time (Europe/Zurich) | Event |
|---|---|
| 2026-08-18 00:08:04 | Legacy-payload job 30812 created. |
| 2026-08-18 19:49:30 | `local-worker-1` process started. |
| 2026-08-18 19:49:32 | Attempt 1 claimed and processing row 120 started. |
| 2026-08-19 12:39:09 | Attempt 1 ended as retrying after 60,577,819 ms; its processing row was incorrectly left `RUNNING`. |
| 2026-08-19 12:42:04 | Attempt 2 claimed and processing row 124 started. |
| 2026-08-19 12:42:20 | Winner reliability remediation committed, after attempt 2 was already executing. |
| 2026-08-19 14:12:47 | Run 118 / pipeline 110 job 30993 queued behind the stuck worker. |
| 2026-08-19 15:14-15:16 | Read-only forensic capture recorded job, attempts, empty generation tables, deployment identity, PostgreSQL activity, CPU, and memory. |
| 2026-08-19 15:17:09 | Audited cooperative cancellation completed; job 30812 became `CANCELLED` and attempt 124 became `CANCELLED`. |
| 2026-08-19 15:17:09 | Worker normally claimed job 30993; pipeline 110 began `VALIDATING_RUN`. |
| 2026-08-19 15:18:02 | Orphaned pre-remediation processing row 120 reconciled truthfully to `LOST`; attempt 124 received the explicit incident reason. |
| 2026-08-19 15:39-15:41 | Pipeline 110 progressed through eight normal stages, then failed at the unrelated CERI readiness gate. Normal job retry semantics requeued job 30993. |
| 2026-08-19 15:45 | Stale worker stopped; fresh worker started from current SHA/schema with automatic cohort refresh disabled. |
| 2026-08-19 16:03 | Fresh worker recovered the abandoned 30993 lease through `recover_stale_jobs` and claimed it with a new token. |
| 2026-08-19 16:20:45 | Job 30993 exhausted normal retries and became terminal `FAILED` at the unrelated CERI readiness gate; pipeline 110 is truthfully `FAILED`. |

## Exact incident state

The complete pre-mutation query results are in [winner_cohort_job_30812_incident_before.md](winner_cohort_job_30812_incident_before.md).

Key facts:

- Job 30812: `RUNNING`, retry 1/3, requested-cancel false, owner/lease `local-worker-1`, attempt count 2.
- Processing rows: 120 and 124 both `RUNNING`, both with null `attempt_no`, null `cohort_generation_id`, empty counts, and empty checkpoints.
- `winner_cohort_refresh_state`: zero rows.
- `winner_cohort_generations`: zero rows.
- Legacy cohort statistics: 1,356 total (L0 646, L1 360, L2 170, L3 122, L4 47, L5 11).
- Immutable `DECISION_TIME` estimates: 11,079.
- Registered worker PID 1772: 55,527.58 CPU seconds, 866,684,928-byte working set, 915,996,672-byte private memory.
- PostgreSQL showed no lock wait. Its long-lived worker backend repeatedly executed a full-row `winner_prediction_snapshots` SELECT.
- Database migration version: `0049_winner_jobs_reliability`.
- Repository HEAD at diagnosis: `52e7e80f98bae5cb957b368e042e7cc3727ddb22`.

## Exact hot path

The pre-remediation handler was:

```text
_eligible_predictions(db)
  -> for every eligible prediction
     -> heartbeat/cancel check (same Session commit)
     -> ProbabilityEstimator.create_latest_rescore(...)
        -> EvidenceService.diagnostic_funnel(...)
           -> load the full prediction/forward/target-stop evidence join
           -> filter the full universe for this prediction/cohort
        -> calculate/persist a latest-rescore estimate
```

This is approximately prediction-count multiplied by all-history evidence work. It explains why a 390-row compatible cohort universe was irrelevant to the live runtime, why no generation/checkpoint was visible, and why `winner_prediction_snapshots` was repeatedly selected. The job payload only contained `outcome_definition_id` and `training_cutoff_at`; it was created before the generation architecture and was therefore a legacy payload.

## Controlled SQLAlchemy experiments

All experiments used a disposable PostgreSQL database with exactly 390 evidence rows, 42 cohort groups (L0 12, L1 8, L2 8, L3 8, L4 5, L5 1), and 2,340 memberships.

| Experiment | Evidence representation / heartbeat | Prediction SELECTs | Total SELECTs | Elapsed | Result |
|---|---|---:|---:|---:|---|
| A | ORM objects retained; domain Session committed between groups | 2,342 | 7,263 | 13.18 s | Reload storm reproduced. |
| B | Same ORM objects; heartbeat commit disabled in disposable test | 2 | 192 | 1.54 s | Reload storm disappeared. Not a production fix. |
| C | Frozen detached DTOs; real domain Session commits retained | 2 | 243 | 2.61 s | Fixed path; 42 groups published. |
| D | ORM objects; heartbeat done in a separate Session | 2 | 240 | 1.72 s | Reloads disappeared, but domain transaction was not committed/fenced. Rejected as a standalone design. |

Experiment A's 2,342 prediction SELECTs are exactly the two evidence-load queries plus one lazy reload per evidence membership (2,340). This proves expiration-on-commit caused the repeated member reads. Experiment B isolates the heartbeat commit as the trigger. Experiment C proves detached immutable evidence eliminates the storm without weakening the existing execution-token compare-and-swap and domain commit. Experiment D shows why merely moving heartbeats is insufficient: a separate heartbeat transaction can remain healthy while stale domain work later commits unless every domain write/publication is independently fenced.

## Evidence loading and the 45-second bound

Before this fix, the wall-clock guard ran only between cohort groups. `load_generation_evidence()` could run before the first time check, and one expensive group could also exceed the soft wall limit.

The fix now:

1. persists and commits `phase=LOAD_EVIDENCE` before the evidence query;
2. records evidence-load start/completion timestamps and elapsed seconds;
3. runs PostgreSQL evidence loading in a separate read transaction with a statement timeout equal to the configured slice wall limit;
4. records `LOAD_EVIDENCE_TIMED_OUT` and returns a deferred continuation without aborting the generation/checkpoint transaction;
5. performs cooperative lease/cancel checks between evidence funnel phases;
6. persists the current cohort level/key and elapsed time before each group;
7. checks wall time before the first group as well as between subsequent groups.

The 45-second setting is therefore a hard database-statement ceiling for the evidence-load phase and a safe yield boundary for group work; it is no longer falsely silent until after an unbounded load.

## Code changes

- Added immutable `FrozenEvidencePrediction`, `FrozenEvidenceForwardOutcome`, `FrozenEvidenceTargetStopOutcome`, and `FrozenEvidenceMember` values. Only IDs, cohort features, outcome statistics fields, revision identity, replay/eligibility identity, origin, episode, and inclusion weight escape the load transaction.
- Generation grouping, statistics, manifest hashing, and manifest-member persistence now consume detached value data.
- Captured a minimal detached outcome-definition identity so per-group heartbeats cannot expire it.
- Removed the redundant second heartbeat commit from each materializer cancel check; fencing still occurs immediately before the check.
- Linked `WinnerProcessingRun.cohort_generation_id` and its first checkpoint immediately after generation capture, instead of only after `refresh_cohorts()` returns.
- Added durable phase/checkpoint/elapsed observability around evidence loading, planning, current group, yielding, and readiness.
- Added a separate, timeout-bounded PostgreSQL read Session for evidence loading while retaining same-domain-Session fencing for writes and publication.
- Corrected PostgreSQL manifest-member inserted counts by using `RETURNING`, avoiding psycopg's `rowcount=-1` artifact.
- Disabled both local rollout flags pending deliberate operator activation.

No schema migration was required for this fix.

## Regression coverage

- Exact 390-row / 42-cohort / 2,340-membership PostgreSQL query-count regression with real heartbeat commits.
- Existing multi-slice resume test continues to prove same generation, stable checkpoint, no duplicate publication, and atomic pointer movement.
- Cancellation was moved from pre-load to an in-load progress checkpoint; it proves the replacement generation is `CANCELLED`, the prior published pointer is unchanged, and `DECISION_TIME` count is unchanged.
- Existing stale-owner and lease-recovery tests prove stale attempts become `LOST`, old execution tokens cannot publish, and a replacement owner receives a new token.
- Existing queue-fairness PostgreSQL coverage proves an interactive `FULL_PIPELINE` claim preempts a large background backlog; the new evidence statement timeout adds the missing bounded yield for a currently executing refresh.
- Existing 15,000-row performance fixture remains green and preserves the greater-than-1,000:1 membership-work reduction relative to the old design.

## Why the previous certification missed this

The prior certification tested the committed v2 code in fresh test processes. It did not verify the identity or restart age of the already-running local worker, and the local `.env` had both rollout flags enabled despite disabled checked-in defaults. The performance fixture measured grouping/membership complexity but did not commit the SQLAlchemy Session between cohort groups, so ORM expiration was absent. The existing PostgreSQL resume test used real commits but only one evidence member, making the extra loads too small to fail a query-count assertion. There was no strict 390/42 SQL-count regression.

## Explicit answers

- **Was heartbeat `db.commit()` expiring ORM entities?** Yes. Experiment A versus B proves it.
- **Did this cause repeated `WinnerPredictionSnapshot` loads?** Yes in the v2 reproduction: 2,342 versus 2. The live pre-v2 job also reloaded snapshots because its algorithm rebuilt all-history evidence once per prediction.
- **Was evidence loading itself unbounded?** Yes before the fix. It is now phase-visible, cooperatively checked, and PostgreSQL-statement-timeout bounded.
- **Was the worker running the expected new code?** No. It started before the remediation commit.
- **Was job 30812 created before or after remediation deployment?** Before.
- **Was its payload legacy?** Yes; it had only `outcome_definition_id` and `training_cutoff_at`.
- **Did generation creation occur even though `WinnerProcessingRun` showed no generation ID?** No. Direct generation and refresh-state queries both returned zero rows. The fixed handler now links the processing row immediately when creation does occur.

## Remaining risks

- PostgreSQL statement timeout bounds a single evidence-load attempt, but repeated timeouts still require query/index investigation; they now yield rather than monopolize a worker.
- A source-code deployment must still restart long-lived Python workers. The current runtime is corrected and automatic refresh is disabled, but deployment orchestration remains responsible for replacing processes when code changes.
- The incident working-set number came from a long-lived production-like process. Disposable-test working-set measurement was not reliable enough for a like-for-like numeric comparison; the fixed run ended with a SQLAlchemy identity map of two objects and frozen evidence proportional to 390 rows.
