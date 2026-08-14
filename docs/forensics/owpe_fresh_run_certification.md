# SwingLens OWPE Fresh-Run Certification

- Date: 2026-08-14 (Europe/Zurich)
- Fresh run: upload run 105; pipeline run 102; background job 30261
- Source file: `money money_2026-08-14.csv`
- Repository baseline: `3e45cf4b898548e7b8869e1413103c20501fdf15` on `codex/ceri-run101-remediation`, with the remediation changes uncommitted
- Database: PostgreSQL 18.3, `swinglens`, server timezone `Europe/Berlin`
- Schema: Alembic `0045_ceri_changes_alerts_semantics`
- Feature schema: `owpe-features-1.0.0`
- Calculation version: `owpe-calc-1.1.0`
Config hash: `218a897655d6c42e19043e1136cb4d578705632f13acf037bc9ce1beef57b527`

## 1. Executive certification verdict

Run 105 proves that the session fix, structured capture policy, evidence funnel, selected-versus-
attempted semantics, empty-evidence presentation, active-definition API selection, and pagination
counts work together. It does not prove a production-eligible non-null probability.

Certification is denied because:

1. The active calculation/config family has zero compatible mature target/stop labels. Every one
   of the 184 decision-time estimates first reaches zero at `compatible_target_stop_label`.
2. All 186 snapshots are dependent episode observations; there is no fresh independent positive
   control.
3. The pipeline produced zero ranking results, so rank/profile provenance is absent everywhere.
4. Primary H5 orchestration is only partially drained: 1,111 due rows remain and no successful
   full-drain timestamp exists.
5. Pipeline 102 ended `PARTIAL` after one IB failure and two prediction exclusions.
6. The scheduler rollout unintentionally matured 2,398 production rows without approval. Those
   rows remain closed to training because they have no revisioned policy classification.

Run 104 remains rejected and its original snapshots/estimates were not rewritten.

## 2. Finding register

| Finding | Classification | Severity | Evidence |
|---|---|---:|---|
| Fresh signal session is 2026-08-14 and next open is 2026-08-17 | `CONFIRMED_EXPECTED_BEHAVIOR` | - | All 186 snapshots; approved U.S. calendar regression tests |
| Active 1.1 evidence reaches zero at exact label compatibility | `DATA_QUALITY_ISSUE` | P0 | All 184 estimate funnels |
| No fresh independent episode representative is available | `DATA_QUALITY_ISSUE` | P0 | 186/186 `dependent_episode=true` |
| Ranking context is absent | `CONFIRMED_DEFECT` | P0 | 0/186 `ranking_result_id`; warning on every UI/API row |
| Oldest-primary API resolution hid fresh estimates | `CONFIRMED_DEFECT` | P0 | Pre-fix API selected inactive definition 1; fixed resolver selects active definition 3 |
| Empty estimates expose no ghost interval/model/calibration/cohort | `CONFIRMED_EXPECTED_BEHAVIOR` | - | 184/184 API estimates have null interval and selected identities |
| Internal L5 prior interval remains persisted for diagnostics | `CONFIRMED_EXPECTED_BEHAVIOR` | P2 | cohort statistic 1, n=0, interval 0.438270; not served as an estimate |
| Primary H5 queue has not converged to zero | `CONFIRMED_DEFECT` | P0 | processing run 110: 1,111 pending; full-drain timestamp null |
| Fresh pipeline is partial | `DATA_QUALITY_ISSUE` | P1 | pipeline 102; one IB failure; MIAX and MOG.A excluded |
| Unauthorized H5 production maturation occurred during rollout | `CONFIRMED_DEFECT` | P0 | job 30260; processing runs 109/110; 2,398 rows |

## 3. Fresh-run execution

Run 105 contains 186 uploaded rows. Pipeline 102 started at
`2026-08-14 21:56:31.710965+02` and ended at `23:01:17.169447+02`. Lease-safe recovery resumed
the persisted pipeline after the original worker stalled. The recovery disabled technical process
pooling and overlap only for that worker process; it did not alter persisted inputs or scoring
rules.

Persisted result:

```text
fundamental_scores                 186
technical_scores                   186
combined_results                   186
winner snapshots                   186
decision-time estimates            184
prediction exclusions                2
pending outcomes                  1840
ranking results                       0
IB failures                           1
pipeline status                 PARTIAL
```

The two excluded primary keys are prediction 8907 (MIAX) and 8984 (MOG.A), both with
`insufficient_completed_bars`. No estimate was created for either.

## 4. Session, timezone, and cutoff certification

Every snapshot has the same decision-time identity:

```text
source_data_cutoff_at   2026-08-14 22:34:33.408271 Europe/Zurich
                       2026-08-14 16:34:33.408271 America/New_York
prediction_as_of_date  2026-08-14
planned_entry_session  2026-08-17
```

At 16:34 New York time the Friday regular session had completed. The approved U.S. calendar
therefore returns Friday 2026-08-14 as the latest completed session, and the next regular session
is Monday 2026-08-17. This is the correct executable `NEXT_OPEN` date.

Database checks show one distinct cutoff, one distinct signal date, and one distinct entry date
across all 186 rows. All 186 have `point_in_time_validated=true`; all 186 have an empty
`source_quality_flags` array. The deterministic session suite covers this exact pre/post-close
boundary plus weekend, holiday, and DST cases (`tests/test_us_market_calendar.py`).

## 5. Eligibility and episode assignment

Run-level counts:

```text
prediction eligible                  184
prediction excluded                    2
capture_training_candidate=true      184
evidence_training_eligible=true         0
dependent_episode=true               186
```

The 184 eligible rows persist `capture_training_candidate=true` and the sole evidence rejection
reason `DEPENDENT_EPISODE`. The two excluded rows persist both `PREDICTION_NOT_ELIGIBLE` and
`DEPENDENT_EPISODE`. This demonstrates the intended split: capture candidacy does not silently
become evidence eligibility.

Representative episode joins are internally consistent. For example, fresh FRO prediction 8876
belongs to episode 3178 (six observations, representative prediction 7506); CVX 8937 belongs to
episode 2984 (seven, representative 7095); FHI 8913 belongs to episode 3303 (four,
representative 8107); CNS 8906 belongs to episode 3256 (four, representative 7702); and TRIN 8919
belongs to episode 2881 (eleven, representative 6612). All episodes started before the fresh
observation and remain within their five-session cooldown windows. No independent fresh control
exists, so the requested production positive-control proof is unmet.

## 6. Historical evidence funnel and first broken stage

The exact funnel persisted on every fresh estimate is:

| Stage | Count after predicate |
|---|---:|
| historical predictions before cutoff | 15,664 |
| full horizon matured before cutoff | 2,673 |
| current forward revision visible at cutoff | 2,673 |
| compatible `T2_5_S2_0_H5_NEXT_OPEN` target/stop label | 0 |
| current target/stop revision visible at cutoff | 0 |
| prediction eligible | 0 |
| point-in-time validated | 0 |
| native capture | 0 |
| production-training eligible | 0 |
| feature-schema compatible | 0 |
| calculation-version compatible | 0 |
| config compatible | 0 |
| outcome-definition compatible | 0 |
| quality gates | 0 |
| rolling-window eligible | 0 |
| no revised-after-cutoff leakage | 0 |
| cohort match | 0 |
| independent episode | 0 |
| one representative per episode | 0 |

The first broken stage is exact label compatibility. The 2,673 visible mature forward revisions
belong to legacy calculation/config identities; there are no mature current target/stop revisions
for active outcome-definition row 3 (`owpe-calc-1.1.0`, new config hash). The service correctly
does not reinterpret or reclassify the old labels.

Reproducible SQL core:

```sql
SELECT count(*) AS estimate_total,
       count(*) FILTER (WHERE point_probability IS NOT NULL) AS probability_total,
       min(metadata_json->>'first_zero_stage') AS first_zero,
       max(metadata_json->>'first_zero_stage') AS last_zero
FROM winner_probability_estimates e
JOIN winner_prediction_snapshots p ON p.id = e.prediction_id
WHERE p.run_id = 105
  AND e.outcome_definition_id = 3
  AND e.estimate_kind = 'DECISION_TIME';
-- 184, 0, compatible_target_stop_label, compatible_target_stop_label
```

## 7. L5 and L0-L5 trace

The active version materialized L5 first, then L4 through L0. Persisted definition/statistic
counts are:

| Level | Definitions | Aggregate n | Aggregate effective n | Aggregate wins |
|---|---:|---:|---:|---:|
| L5 | 1 | 0 | 0 | 0 |
| L4 | 4 | 0 | 0 | 0 |
| L3 | 6 | 0 | 0 | 0 |
| L2 | 6 | 0 | 0 | 0 |
| L1 | 15 | 0 | 0 | 0 |
| L0 | 13 | 0 | 0 | 0 |

Exact L5 state:

```text
cohort_definition_id  1
statistic_id           1
cohort_key             L5:aa19550e1b53b14d29315fdbe5204f23bb9c5dd03c89dbfd8313b3988b0c04ba
dimensions             {"global":"all"}
training_cutoff        2026-08-14 22:34:33.408271+02
n / effective n / wins 0 / 0 / 0
raw rate               null
prior/posterior mean   0.5
lower / upper          0.280865 / 0.719135
internal interval      0.438270
grade                  Insufficient
manifest hash          6375508cf89fca1184992a7df1925296aa53eca766c56d31f66bb87e3b778620
materialization order  L5_TO_L0
```

The internal statistic records the Beta(10,10) prior and its interval for diagnostics. It is not
a selected cohort: all 184 estimates have null `cohort_definition_id`, null
`selected_cohort_level`, null `selected_cohort_key`, `attempted_cohort_level=L5`, and the L5 key
above as `attempted_cohort_key`. The API/UI do not expose the internal 0.438270 as an estimate
interval.

## 8. Probability and evidence reproduction

Fresh estimates:

```text
decision-time estimates             184
non-null probabilities                0
non-null estimate intervals            0
selected cohort IDs                    0
model IDs                              0
distinct manifest hashes               1
materialized evidence members          0
```

All 184 estimates reference manifest row 1, hash
`6375508cf89fca1184992a7df1925296aa53eca766c56d31f66bb87e3b778620`, with
`member_count=0` and payload `{"members":[]}`. Estimate 8998 reproduction returns
`matches=true`, no mismatches, n=0, null probability, and the same hash.

The production run cannot exercise the non-null reproduction contract because no observation
passes the exact-label gate. Deterministic tests do exercise a positive sample at and above the
minimum and assert stored versus recomputed probability, lower/upper interval, interval width,
n, effective n, wins, member IDs, both outcome revisions, and manifest hash. That test proof is
necessary but not a substitute for fresh production evidence.

## 9. Representative fresh traces

| Ticker | Prediction | Estimate | Setup family | Score | Sector | Episode | Result |
|---|---:|---:|---|---:|---|---:|---|
| FHI | 8913 | 8878 | Strong candidate | 8.7086 | Risk-off | 3303 | n=0, withheld |
| CVX | 8937 | 8902 | Candidate | 7.9914 | Risk-off | 2984 | n=0, withheld |
| CNS | 8906 | 8872 | Watchlist | 6.7239 | Risk-off | 3256 | n=0, withheld |
| FRO | 8876 | 8842 | Avoid | 6.1482 | Risk-off | 3178 | n=0, withheld |
| TRIN | 8919 | 8884 | Avoid | 4.7993 | Lagging | 2881 | n=0, withheld |
| MOG.A | 8984 | - | Incomplete data | 6.3879 | Risk-off | 3185 | excluded; no estimate |

For the five estimated rows: capture candidacy is true, evidence eligibility false, rejection is
`DEPENDENT_EPISODE`, selected cohort/model IDs are null, interval is null, grade is Insufficient,
first zero stage is `compatible_target_stop_label`, and the manifest is the exact empty manifest.
These traces span four setup families, multiple score bands, and both observed sector-state values.

## 10. API, DB, UI, and pagination reconciliation

After fixing active outcome-definition resolution, the default endpoint selects definition row 3:

```text
GET /api/winner-probability/run/105?page_size=100
outcome definition  T2_5_S2_0_H5_NEXT_OPEN / owpe-calc-1.1.0
page 1 rows          100
next cursor          8965

GET /api/winner-probability/run/105?page_size=100&cursor=8965
page 2 rows          86
next cursor          null
unique prediction IDs across pages 186
```

DB, API summary, and rendered UI agree:

| Semantic count | DB | API | UI page 1 | UI page 2 |
|---|---:|---:|---:|---:|
| page rows | - | 100 / 86 | 100 | 86 |
| all filtered rows | 186 | 186 | 186 | 186 |
| run total | 186 | 186 | 186 | 186 |
| estimate total | 184 | 184 | 184 | 184 |
| calibrated total | 0 | 0 | 0 | 0 |
| insufficient total | 184 | 184 | 184 | 184 |
| missing estimate total | 2 | 2 | 2 | 2 |

The rendered first page has a next-page link; the rendered second page has 86 rows and no next
link. Summary cards use run/filter totals, not page length.

UI contract matrix:

| Contract field | Run 105 UI/API result |
|---|---|
| rank / score | Column present; explicit `ranking unavailable`; warning present |
| setup | Present |
| regime / sector | Present |
| probability / interval | Present and explicitly withheld; no ghost interval |
| grade / effective n | Present (`Insufficient`, 0) |
| return / MFE / MAE / target-first | Columns present; blank when no evidence |
| model / calibration | `None` / `not_applicable`; no fake lifecycle status |
| outcome definition | Active identity shown above table and in API |
| cohort identity | Selected identity null; attempted L5 retained in estimate metadata |
| sample / cutoff | Present |
| warnings / no-evidence reason | `missing_ranking_result` and `no_eligible_cohort` present |
| exact evidence | Detail/reproduction endpoint resolves the empty manifest exactly |

## 11. H5 backlog and orchestration state

The implementation is durable, bounded, resumable, H5-first, and tested with a backlog larger
than one batch. Current production state is not healthy:

```text
due_h5_next_open             3439
processed_h5                3439
matured_h5                  2328
target_stop_matured         2328
pending_h5_after_cycle      1111
excluded_h5                    0
failed_h5                      0
oldest_due_h5_session       2026-08-07
last_successful_full_drain  null
```

The persisted `oldest_due_h5_age=6` was produced before the final metric correction and represents
calendar days; current code computes four completed U.S. sessions for the same interval. Automatic
maturation is now separately gated by
`WINNER_PROBABILITY_AUTO_MATURATION_ENABLED=false`. No additional drain was run.

## 12. Tests and verification commands

```text
python -m pytest tests/test_settings.py tests/test_us_market_calendar.py \
  tests/winner_probability tests/test_background_worker.py \
  tests/test_background_job_service.py -q
217 passed in 13.60s

python -m ruff check app tests
All checks passed

git diff --check
No whitespace errors; Windows line-ending notices only
```

The focused suite covers multi-batch drain, H5 priority, retry/idempotency, partial resume,
missing-bar continuation, cohort-refresh enqueue, each eligibility reason, each evidence gate,
future/revision leakage, positive n=15 evidence, L5-first materialization, L0-L5 backoff,
14/15/40/100 thresholds, Beta posterior/interval/member/hash reproduction, no interval when
withheld, active outcome-definition resolution, session boundaries, API counts, and UI semantics.

## 13. Required next actions

No production repair was executed beyond the documented unintended maturation. Before another
certification attempt:

1. Obtain governance approval for a revisioned, append-only eligibility classification of legacy
   native snapshots; never mass-update the old boolean.
2. Resolve the active-definition historical-compatibility plan. Either accumulate native 1.1
   outcomes prospectively or approve an audited, revisioned reconstruction; do not relabel 1.0
   outcomes in place.
3. Investigate why the full pipeline creates no ranking results and restore persisted rank/profile
   provenance without changing SwingLens scores.
4. After approval, drain the primary H5 queue to zero through the durable orchestrator and verify
   a non-null full-drain timestamp.
5. Disposition retryable job 30262 through audited job control.
6. Execute another fresh full run containing at least one independent episode representative and
   reproduce any non-null estimate from exact persisted members.

## 14. Final certification status

REJECT
