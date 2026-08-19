# Winner Jobs Reliability Certification

Date: 2026-08-19

Baseline: `72a45ce31b775a255c390c207dd2a31f73e2f3a6`

Verdict: **PASS_WITH_FINDINGS**

## Deterministic datasets

| Scenario | Size |
|---|---:|
| Cohort performance evidence | 15,000 historical rows |
| Representative current run | 200 predictions |
| Unique L0-L5 cohort keys | 253 |
| Generated shared memberships | 90,000 |
| Old all-history refresh lower bound | 225,000,000 memberships |
| H5 pending maturation/prefetch | 3,000 outcomes |
| Manifest bulk-insert fixture | 1,000 members |

All database scenarios use disposable PostgreSQL databases upgraded to Alembic head. IDs below are exact within their named fresh fixture database.

## Incident-flow certification

In `test_bounded_generation_resume_coalescing_and_atomic_publication`:

| Artifact | Exact value |
|---|---|
| Upload run | `1` |
| Outcome definition DB ID | `1` |
| Initial forward/target-stop row IDs | `1 / 1` |
| Initial watermark | `{forward: 1, target_stop: 1, eligibility: 0, replay: 0}` |
| Initial watermark hash | `aa5a0e356c539c6ec86e1ef1c6c6c37914cc3c91e1b5d7b6df3b6ebdbec97121` |
| First generation | `1` |
| Revised forward/target-stop row IDs | `2 / 2` |
| Desired watermark while generation 1 runs | `{forward: 2, target_stop: 2, eligibility: 0, replay: 0}` |
| Revised watermark hash | `a2cc523054bfc5cb2b1b0ab90613c73459fca63b4254ee70470b44d8363a72a7` |
| Replacement generation | `2` |
| Targeted rescore background job | `1` |
| Rescore processing attempts | `1`, `2` |
| Cancelled later generation | `3` |

Generation 1 materializes L5 in its first one-group slice. Evidence revision 2 arrives while it is `BUILDING`; desired state advances without replacing or losing the active request identity. Generation 1 completes and publishes safely, reports that desired state is newer, and generation 2 then materializes and atomically becomes published. Generation 1 remains historical as `SUPERSEDED`.

Generation 2 contains exactly one statistic at each level L5, L4, L3, L2, L1, and L0 (six total). The L5 manifest records target-stop revision 2 written exactly at the watermark boundary. No probability estimate is created by either cohort refresh.

## Publication and cancellation proof

The publication validator rejects `BUILDING -> PUBLISHED` and any READY generation whose completed count differs from planned count. In the scenario:

```text
state.published_generation_id = 2
state.published_watermark_hash = generation_2.watermark_hash
generation_1.status = SUPERSEDED
generation_2.status = PUBLISHED
```

A third material revision produces watermark hash `ae2cd085146f393912a0b39ddb877c30e8a7b523ef9c4f9f8f65ee07952a4740` and generation 3. Cancellation changes generation 3 to `CANCELLED`; the pointer remains generation 2. Thus partial/cancelled state is never served. The prior published generation remains the safe serving fallback until a later retry processes desired watermark 3.

## Coalescing and concurrency proof

`test_concurrent_refresh_requests_coalesce_and_preserve_newer_desired_state` opens two PostgreSQL sessions and concurrently inserts the same active refresh request key. The database partial unique index and queue fallback produce one active logical job. Desired watermark state is stored separately and remains at the newer value, so queue coalescing cannot erase demand.

The generation-key test proves that requests 0.227 seconds apart produce one generation when material evidence is identical. Re-observing outcome revision 1 does not advance the watermark; current revision 2 advances it exactly once.

## Checkpoint, recovery, and fencing proof

The first cohort slice commits:

```json
{
  "phase": "MATERIALIZE_GROUPS",
  "last_cohort_level": "L5",
  "last_cohort_key": "L5:<stable digest>",
  "completed_groups": 1,
  "planned_groups": 6
}
```

Resume begins after that stable cohort identity and produces the same six-statistic published result as an uninterrupted build. Targeted rescore freezes two prediction IDs, processes one per attempt, and advances `cursor_prediction_id`; attempt 2 completes with no permanent RUNNING processing row.

In `test_stale_lease_owner_cannot_publish_generation`, background job ID 1 is claimed with execution token A, recovered, then reclaimed with token B. The old session's heartbeat/fence raises `JobLeaseLost`. Its generation remains READY and refresh state has no published pointer. In the stale-recovery test, abandoned Winner processing run ID 1 becomes `LOST` with terminal reason `LEASE_EXPIRED_SUPERSEDED` rather than remaining RUNNING.

## Latest rescore and reproduction proof

The explicit rescore uses generation 2 and no historical evidence query. It derives the target's six keys, loads only generation statistics, and produces an insufficient estimate because the one-row fixture does not meet unchanged evidence thresholds. Assertions prove:

- `point_probability IS NULL` (no fabricated 0%/50% value);
- `cohort_generation_id = 2`;
- an exact shared evidence manifest is referenced;
- zero `winner_estimate_evidence_members` copies are inserted;
- reproduction from `winner_evidence_manifest_members` matches exactly;
- rerun returns the same estimate identity;
- DECISION_TIME row count is unchanged.

## SQL and runtime evidence

| Measurement | Result |
|---|---:|
| 15,000-row grouping time | 1.914897 s |
| Unique cohort statistics | 253 |
| Membership reduction | 225,000,000 -> 90,000 (2,500:1) |
| H5 context SQL statements for 3,000 outcomes | 3 |
| H5 PostgreSQL test total time, including migration/fixture | 12.18 s |
| Statements for 1,000 shared manifest members | 1 |
| Per-member existence selects | 0 |

The regression threshold requires generation membership work to remain below one-thousandth of the old all-history cross product and grouping under eight seconds on this machine.

## Final-state verification queries

The certification test asserts the equivalent of:

```sql
SELECT id, status, watermark_hash, completed_group_count, planned_group_count
FROM winner_cohort_generations
ORDER BY id;

SELECT published_generation_id, published_watermark_hash, desired_watermark_hash
FROM winner_cohort_refresh_state
WHERE id = 1;

SELECT metadata_json->>'cohort_level' AS level, count(*)
FROM winner_cohort_statistics
WHERE generation_id = 2
GROUP BY 1
ORDER BY 1;

SELECT count(*)
FROM winner_probability_estimates
WHERE estimate_kind = 'DECISION_TIME';

SELECT count(*)
FROM winner_estimate_evidence_members
WHERE estimate_id IN (
  SELECT id FROM winner_probability_estimates WHERE cohort_generation_id = 2
);

SELECT id, attempt_no, status, terminal_reason_code
FROM winner_processing_runs
WHERE background_job_id = 1
ORDER BY attempt_no;
```

Expected results at the caught-up checkpoint are generation 1 SUPERSEDED, generation 2 PUBLISHED, pointer/hash equal to generation 2/desired, one statistic per L0-L5, unchanged DECISION_TIME count, zero copied estimate members, and two terminal rescore attempts. The later cancellation subscenario changes only desired watermark and adds CANCELLED generation 3; it does not move the published pointer.

## Automated verification

- Winner subsystem: `198 passed`
- background worker/job/queue: `42 passed`
- PostgreSQL reliability/concurrency/performance: `6 passed`
- clean PostgreSQL migration: `1 passed`
- 15,000-row performance regression: `1 passed`
- full repository suite: `1,733 passed, 9 skipped, 14 warnings` in `974.33 s`
- Ruff: passed
- diff whitespace check: passed

Findings are limited to the deliberate rollout gate and production-only observation: automatic v2 refresh/serving remains off, no production backfill was run, peak memory is not instrumented, and production `EXPLAIN (ANALYZE, BUFFERS)` remains an operator-controlled preview step. No correctness gate or evidence threshold was weakened.
