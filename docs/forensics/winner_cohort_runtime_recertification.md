# Winner cohort runtime recertification

## Certification result

The generation materializer is recertified for the incident shape. It performs one bounded evidence-universe load into detached frozen values, plans L5 to L0 once, retains execution-token fencing, publishes atomically, and does not modify immutable `DECISION_TIME` estimates.

## Runtime comparison

| Metric | Incident | After fix |
|---|---:|---:|
| evidence rows | ~390 compatible rows (legacy job did not use this bound) | 390 |
| unique cohorts | 42 intended (legacy job created no plan) | 42 |
| memberships | 2,340 intended | 2,340 |
| `WinnerPredictionSnapshot` SELECTs | repeated live; 2,342 in exact ORM reproduction | 2 |
| total SQL SELECT statements | 7,263 in exact ORM reproduction | 243 materialization-only (245 including two diagnostic assertions) |
| time to generation row + commit | no generation | 0.019 s |
| time to first durable checkpoint | none | 0.023 s |
| evidence load time | not phase-visible | 0.131 s |
| total materialization time | attempts ran ~16h50m and ~2h35m before cancellation | 2.61 s representative; 3.31 s independent strict-test run |
| observed working memory | 866,684,928-byte working set at diagnosis (~826.5 MiB) | like-for-like OS RSS not reliably captured; identity map size 2 after completion |
| published generation integrity | no generation pointer existed; 1,356 legacy statistics preserved | complete disposable generation atomically `PUBLISHED`; no partial pointer movement |
| `DECISION_TIME` changes | 0 expected; count 11,079 before and after recovery | 0 |

Timing is intentionally a representative observation, not a machine-independent pass threshold. The hard regression assertions are query scaling, exact group/membership identity, a 45-second ceiling for this fixture, and publication/cancellation invariants.

## SQL experiments

| Experiment | Prediction SELECTs | Total SELECTs | Elapsed |
|---|---:|---:|---:|
| ORM evidence + heartbeat domain commits | 2,342 | 7,263 | 13.18 s |
| ORM evidence + heartbeat commit disabled | 2 | 192 | 1.54 s |
| Frozen evidence + real heartbeat domain commits | 2 | 243 | 2.61 s |
| ORM evidence + separate heartbeat Session | 2 | 240 | 1.72 s |

The separate-session result was not adopted for domain writes: it did not commit or fence the materialization transaction. Frozen evidence retains the repository's proven same-transaction execution-token fencing.

## Operational preservation proof

Pre-cancellation and post-cancellation counts matched exactly:

```text
winner_cohort_definitions       360
winner_cohort_statistics      1,356
winner_cohort_generations         0
winner_cohort_refresh_state       0
winner_evidence_manifests       415
DECISION_TIME estimates       11,079
LATEST_RESCORE estimates      18,295
```

Job 30812 reached audited `CANCELLED`; owner, lease, lock, heartbeat, and execution token were all cleared. Processing row 124 is `CANCELLED`; orphaned prior row 120 is truthfully `LOST`; both have terminal reason `OPERATOR_CANCELLED_STUCK_COHORT_REFRESH`. No incident generation existed to cancel or publish.

Run 118 was not manually mutated. Job 30993 was claimed through the normal queue at 15:17:09, and pipeline 110 left its queued state and completed eight normal stages before encountering an unrelated CERI readiness failure. After the stale worker exited during a retry race, the fresh worker waited for lease expiry, logged `job.stale_recovered`, reclaimed job 30993 with a new token, and exhausted its normal retries. Final job 30993 state is terminal `FAILED` (retry count 4, max retries 3, completed 16:20:45); owner, lease, lock, heartbeat, and execution token are all clear. Pipeline 110 is truthfully `FAILED` at `CERI_PROVIDER_INGEST`.

The restarted worker reports:

```text
worker ID                  local-worker-1
repository SHA             52e7e80f98bae5cb957b368e042e7cc3727ddb22
database schema revision   0049_winner_jobs_reliability
SEC incremental mode       ACTIVE
process code state          current fixed worktree
```

Local rollout state after recovery:

```text
WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED=false
WINNER_COHORT_REFRESH_V2_ENABLED=false
```

Final production-like integrity snapshot:

```text
winner_cohort_definitions       360
winner_cohort_statistics      1,356  (L0 646, L1 360, L2 170, L3 122, L4 47, L5 11)
winner_cohort_generations         0
winner_cohort_refresh_state       0
winner_evidence_manifests       415
DECISION_TIME estimates       11,079  (unchanged)
new cohort refresh jobs            0
```

## Test certification

Commands and results:

```text
python -m pytest tests/winner_probability -q
198 passed

python -m pytest tests/test_background_worker.py tests/test_background_job_service.py -q
33 passed

python -m pytest tests/integration/test_winner_jobs_reliability_postgresql.py -q
7 passed

python -m pytest tests/winner_probability/test_evidence_manifest_service.py \
  tests/integration/test_winner_jobs_reliability_postgresql.py -q
10 passed

python -m ruff check app tests
All checks passed

git diff --check
Passed (line-ending notices only)
```

```text
python -m pytest -q
1,737 passed, 9 skipped, 14 warnings in 1,289.37 s
```

## Acceptance assertions

- Query count does not scale as groups multiplied by evidence members.
- Real heartbeat commits return exactly the same 390-row, 42-group, 2,340-membership result.
- Multi-slice resume retains a stable generation and atomic publication.
- Cancellation during evidence loading leaves the prior published pointer and `DECISION_TIME` count unchanged.
- Lease recovery makes prior attempts `LOST`; stale execution tokens cannot publish.
- Evidence loading has an early durable checkpoint and a PostgreSQL statement timeout equal to the configured slice ceiling.
- The existing 15,000-row performance regression remains green.
- Automatic cohort refresh remains disabled pending explicit operator rollout.
