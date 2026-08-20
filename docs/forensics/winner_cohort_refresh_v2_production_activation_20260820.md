# Controlled Winner Cohort Refresh V2 Production Activation and Certification

Date: 2026-08-20 (Europe/Zurich)

Database: `swinglens` on PostgreSQL 18.3

Schema revision: `0050`

Final decision: **PASS_WITH_FINDINGS**

## Executive conclusion

The current production runtime and database are proven safe to enable automatic cohort refresh with the V2 serving contract, subject to the non-blocking findings documented below. Automatic refresh was not enabled during this work.

The first controlled materialization exposed a real replay-lineage defect: 103 of 390 pre-1.1 replay rows could no longer be reproduced from their recorded price-bar lineage. Certification was stopped, the defect was fixed test-first without weakening reproduction, the cohort algorithm identity was advanced from `cohort-v2` to `cohort-v2.1`, and the complete activation sequence was repeated. The recertified generation contains 287 reproducible evidence rows, all 42 planned groups, a complete L5-to-L0 hierarchy, deterministic manifests, and an atomic published pointer. Its unchanged-evidence rerun was a fast no-op.

The production-like validation used a newly uploaded run 119, not run 118. All twelve durable pipeline stages completed, including Winner capture. It created 180 Winner snapshots and 178 immutable `DECISION_TIME` estimates; the two exclusions were explicit. Three representative fresh estimates reproduced exactly through the service and API/UI paths. No automatic cohort refresh was scheduled.

## 1. Starting controls and worker identity

The environment was verified before mutation and again at final handoff:

```env
WINNER_PROBABILITY_AUTO_MATURATION_ENABLED=true
WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED=false
WINNER_COHORT_REFRESH_V2_ENABLED=true
```

The previously running worker predated the environment change, so it was replaced. A failed-closed first restart attempt correctly refused a duplicate active registry identity until the old heartbeat aged past its fencing window. The final live worker was then restarted again after the production defect fix so it loaded `cohort-v2.1`.

| Attribute | Certified value |
|---|---:|
| Worker ID | `local-worker-1` |
| Host | `NewLaptop` |
| Parent PID | 10504 |
| Worker PID | 8056 |
| Started | 2026-08-20 12:49:51 Europe/Zurich |
| Queues | `interactive`, `broker`, `background` |
| Git SHA | `0e2d7d...` |
| Git dirty | `true` (certification fixes and existing user artifacts) |
| Startup log | `logs/winner_v2_1_cert_worker_20260820T124951.err.log` |
| Loaded flags | maturation `true`; auto refresh `false`; V2 `true` |
| Final heartbeat age | 1.259 seconds |
| Final process state | responding |

The worker startup configuration log was extended to emit all three Winner activation flags. This was covered by a failing-then-passing unit test. At final inspection, `local-worker-1` was the only live registry identity. Older registry rows were beyond the heartbeat timeout and could not claim work.

Before the first refresh there were zero queued or running Winner jobs. No obsolete cohort refresh had an executable lease. Historical refresh attempts remained truthful, including job 30753 `PARTIAL`, jobs 30754 and 30812 `CANCELLED`, and job 30262 `FAILED`. There were no pre-existing `WINNER_LATEST_RESCORE` job rows.

## 2. Pre-refresh production snapshot

Snapshot time: 2026-08-20 10:31:32 UTC.

| Object | Starting count |
|---|---:|
| `winner_cohort_definitions` | 367 |
| `winner_cohort_statistics` | 1,435 |
| `winner_cohort_generations` | 0 |
| `winner_cohort_refresh_state` | 1 |
| `winner_evidence_manifests` | 415 |
| `winner_evidence_manifest_members` | 0 |
| `winner_probability_estimates` | 29,559 |
| `winner_processing_runs` | 121 |
| `background_jobs` | 30,707 |

Legacy cohort-statistic distribution was L0 681, L1 381, L2 179, L3 131, L4 51, and L5 12. There were no V2 statistics.

Immutable estimate baseline:

| Estimate kind | Count | Fingerprint | ID range |
|---|---:|---|---|
| `DECISION_TIME` | 11,257 | `ccc6700509366087824b8350dc3d0a76` | 1-33118 |
| `LATEST_RESCORE` | 18,302 | `a72a3cdd832fb8049f5a6046a1ae4576` | 9011-32940 |

The active outcome definition was `T2_5_S2_0_H5_NEXT_OPEN` (database PK 3), feature schema `owpe-features-1.0.0`, calculation version `owpe-calc-1.1.0`, and config hash `218a897655d6c42e19043e1136cb4d578705632f13acf037bc9ce1beef57b527`.

## 3. Watermark and material-refresh decision

The desired watermark was:

```json
{
  "forward_revision_id": 0,
  "target_stop_revision_id": 0,
  "eligibility_decision_id": 8859,
  "training_replay_id": 390
}
```

Its hash was `9450df84e0d68de23f428df4e6974e20947b9c3b5654f57dfb02167e99cad1ba`.

There was no published V2 generation and no published watermark. A material refresh was therefore required; no artificial evidence was created.

## 4. Initial controlled cycle and certification-blocking defect

The supported administrative route queued normal durable job 31052. It followed `QUEUED -> RUNNING` while generation 1 followed `BUILDING -> READY -> PUBLISHED`.

| Generation-1 attribute | Value |
|---|---|
| Generation ID | 1 |
| Generation key | `9d159cf34396b0b0114748f7174a88ca2e50527e3a8f411b838576cfd0170054` |
| Algorithm | `cohort-v2` |
| Watermark hash | `9450df84e0d68de23f428df4e6974e20947b9c3b5654f57dfb02167e99cad1ba` |
| Evidence rows | 390 |
| Planned/completed/failed groups | 42 / 42 / 0 |
| New manifest members | 1,475 |
| Evidence-load time | 11.038 s |
| Materialization slice | 16.453 s |
| Job wall time | 16.873 s |
| READY | 12:35:14.340966 Europe/Zurich |
| PUBLISHED | 12:35:14.362965 Europe/Zurich |

The 22 ms READY-to-PUBLISHED interval occurred only after the 42/42 validation checkpoint. Job 31052 completed with retry count 0, no continuation, and cleared lease/fencing fields. Job 31053 then proved an 89.5 ms no-op with zero table deltas.

A bounded, frozen-target `WINNER_LATEST_RESCORE` job 31054 was intentionally run for prediction IDs 11140-11142 to exercise serving. It added exactly three `LATEST_RESCORE` rows, but reproduction failed closed with `Replay price-bar lineage changed after classification.` This invalidated the initial certification cycle.

Diagnosis proved that the existing evidence funnel admitted pre-1.1 replays whose source cutoff preceded the training cutoff without confirming that their exact recorded bar lineage still existed. Of 390 candidate replays, 103 were no longer reproducible; 287 remained valid. Example replay 2 referenced price bar 1917661 at revision 0/hash `2314...`, while the live bar was revision 1/hash `445f...`; the original exact state was not available as a qualifying revision row. No reproduction check was weakened and no historical row was rewritten.

## 5. Minimal TDD fixes

The following certification-blocking fixes were made:

1. `EvidenceService` now bulk-loads referenced current bars and bar revisions in two bounded set queries, validates replay lineage with the same fail-closed identity rules used by reproduction, and exposes a `replay_lineage_reproducible` funnel stage.
2. The cohort algorithm identity advanced to `cohort-v2.1`, ensuring the unchanged watermark could not no-op against invalid generation 1.
3. API serialization now recovers missing V2 evidence-composition fields from immutable manifest membership using one cached aggregate query per manifest. Existing estimates remain immutable.
4. Worker startup logs now include all three Winner rollout flags.
5. Windows-only external-process test startup waits were increased to 60 seconds after the original 15/30-second harness deadlines repeatedly expired while healthy subprocesses remained alive. Linux deadlines are unchanged.

All fixes were introduced with failing tests first. The patched production evidence probe returned 287 rows with the funnel transition `390 -> 287` at `replay_lineage_reproducible`. The probe issued six SELECTs total including definition and watermark reads; the lineage work itself used bounded native/replay/bar/revision set queries and no N+1 loop.

## 6. Recertified material generation

After restarting the worker with the fixes, the supported administrative route queued job 31055. Generation 2 followed the complete observed lifecycle `BUILDING -> READY -> PUBLISHED`.

| Generation-2 attribute | Value |
|---|---|
| Generation ID | 2 |
| Generation key | `00b735e9a1582f7a1f0cab9c37c128e3a07395fae084a4ba6f680892eef73ca1` |
| Algorithm | `cohort-v2.1` |
| Watermark hash | `9450df84e0d68de23f428df4e6974e20947b9c3b5654f57dfb02167e99cad1ba` |
| Training cutoff | 2026-08-20 12:51:25.230712 Europe/Zurich |
| Evidence rows | 287 |
| Planned/completed/failed groups | 42 / 42 / 0 |
| New manifest-member inserts | 1,075 |
| Evidence-load time | 4.149 s |
| Materialization slice | 7.984 s |
| Job wall time | 8.385 s |
| READY | 12:51:33.257712 Europe/Zurich |
| PUBLISHED | 12:51:33.279708 Europe/Zurich |

Again, the pointer moved 22 ms after READY and only after 42/42 groups were validated. The refresh-state row for `cohort-v2.1` points to generation 2, and its desired and published watermark hashes are identical. The older generation remains historically truthful under the old algorithm contract; current V2 serving resolves through the `cohort-v2.1` refresh-state identity and generation 2.

Job 31055 completed on its first attempt, with no retries, continuation, rescore work, or uncleared lease fields. Peak working set observed specifically during refresh was 492,769,280 bytes (approximately 470 MiB); it returned to approximately 196 MiB afterward. The worker heartbeat remained current throughout.

## 7. L5-to-L0 hierarchy and manifest integrity

Generation 2 materialized in strict L5-to-L0 statistic-ID order:

| Level | Groups | Statistic IDs | Sum of group sample counts |
|---|---:|---|---:|
| L5 | 1 | 1644 | 287 |
| L4 | 5 | 1645-1649 | 287 |
| L3 | 8 | 1650-1657 | 287 |
| L2 | 8 | 1658-1665 | 287 |
| L1 | 8 | 1666-1673 | 287 |
| L0 | 12 | 1674-1685 | 287 |

All levels used the same frozen 287-row evidence universe. There were 24 unique immutable manifests. Across those unique manifests, declared and persisted member totals both equal 1,081; six memberships were reused from an identical existing manifest and 1,075 were newly inserted. There were zero member-count mismatches, zero ordinal gaps, and zero duplicate member hashes. The materializer performed one evidence load and group planning pass, not a per-prediction historical rebuild.

No unexpected Winner jobs or rescore fan-out appeared. Queue size did not grow, no continuation chain was created, and there were no hundreds/thousands of latest-rescore jobs. The refresh completed in seconds, not hours.

## 8. DECISION_TIME immutability and LATEST_RESCORE accounting

Across both material generation jobs and both no-op checks, the immutable baseline remained:

```text
DECISION_TIME count:       11,257 -> 11,257
DECISION_TIME fingerprint: ccc6700509366087824b8350dc3d0a76
                           -> ccc6700509366087824b8350dc3d0a76
```

No `DECISION_TIME` estimate was updated by cohort refresh.

`LATEST_RESCORE` accounting before the fresh pipeline was:

```text
18,302 baseline
+    3 generation-1 diagnostic targets (job 31054; reproduction failed closed)
+    3 generation-2 certification targets (job 31057; exact reproduction)
=18,308 final
```

Both jobs used the supported bounded `WINNER_LATEST_RESCORE` path with the frozen target manifest `[11140, 11141, 11142]`. No other latest-rescore row or job was created. The three invalid diagnostic rows remain immutable historical evidence; the newer generation-2 rows are selected for those predictions.

## 9. Recertified no-op

Supported route job 31056 repeated the refresh without changing evidence. It completed in 107.2 ms wall time with:

```text
no_op: true
generation_id: 2
evidence_rows_loaded: 0
groups_in_slice: 0
manifest_members_inserted: 0
continuation_required: false
retry_count: 0
```

Generation/statistic/manifest/member counts and both estimate-kind counts had zero deltas. No logically duplicate generation was created.

## 10. V2 serving certification

Targeted job 31057 produced estimates 33122-33124 for MNST, MAX, and ARQT from generation 2. All three selected cohort statistic 1664 / definition 143 at L2 with sample/effective N 119, 41 wins, point probability 0.366906, lower bound 0.286782, upper bound 0.447030, interval width 0.160248, evidence grade High, and manifest hash `8754d59ea12f6374f53d5bec4cd95f338b10d1ce1f45cb43e442d88d71ddb0a8`.

All three reproduced exactly with no mismatches. API reads selected estimate IDs 33122-33124 and generation 2; reproduction endpoints returned HTTP 200 / `matches=true`; the Winner UI returned HTTP 200. Manifest-backed composition reported 0 native plus 119 compatible/reconstructed rows, matching `sample_n=119`, policy `owpe-pre11-eligibility-1.0.0`, and evidence dates 2026-08-04 through 2026-08-06.

Serving contracts resolve the refresh state by algorithm/version and require generation status `PUBLISHED`. Generation 2 is the current `cohort-v2.1` pointer. BUILDING, READY, FAILED, and CANCELLED states are excluded by contract and route tests. No probability is synthesized for insufficient evidence; existing fail-closed behavior and tests remain intact.

## 11. Fresh production-like pipeline

The stored source CSV was uploaded again through the normal `/uploads` route, creating run 119. Run 118 was not reused. The normal `/runs/119/pipeline` route created durable pipeline 111 / background job 31058.

IB Gateway was initially running with API login/2FA incomplete, so the supported `ALLOW_CACHE_FALLBACK` policy was selected. IB became READY during route preflight, and the pipeline performed a real HMDS refresh rather than using cache fallback.

| Pipeline observation | Value |
|---|---:|
| Status | `PARTIAL` |
| Durable job status/retries | `COMPLETED` / 0 |
| Wall time | 1,474,813.637 ms |
| Raw/fundamental/technical/combined rows | 180 / 180 / 180 / 180 |
| IB planned/executed/success | 362 / 362 / 362 |
| IB failures | 2 |
| Bars fetched | 2,896 |
| Inserted/updated/revised/unchanged | 362 / 71 / 71 / 2,463 |
| Ranking results/profiles | 900 / 5 |
| Winner snapshots | 180 |
| Winner `DECISION_TIME` estimates | 178 |
| Explicit Winner exclusions | 2 |
| Winner capture failures | 0 |

All twelve pipeline steps completed: validation, fundamentals, market data, technicals, market regime, combining, ranking, sector rotation, CERI provider ingest, setup capture, setup lifecycle evaluation, and Winner prediction capture.

The partial classification is fully explained by two pre-existing `MOG.A` IB contract-resolution failures (`ADJUSTED_LAST` and `TRADES`) and one incomplete combined row. The two Winner exclusions were explicit `insufficient_completed_bars`; the remaining 178 predictions were eligible and received immutable decision-time estimates.

The fresh estimate distribution was 106 High/sample 119, 45 Medium/sample 44-52, and 27 Low/sample 15-23. Representative estimates reproduced exactly:

| Estimate | Prediction/ticker | Grade/sample | Probability | Reproduction |
|---:|---|---|---:|---|
| 33125 | 11320 / MNST | High / 119 | 0.366906 | exact |
| 33128 | 11323 / LLY | Medium / 44 | 0.390625 | exact |
| 33129 | 11324 / JNJ | Low / 23 | 0.511628 | exact |

For all three, API and UI returned HTTP 200, selected the correct persisted estimate ID, exposed composition equal to sample N, and the reproduction API returned `matches=true`.

The pipeline added exactly 178 new `DECISION_TIME` rows, so the final count is 11,435. This is expected fresh-run append-only behavior and is separate from the refresh immutability comparison. It created no `LATEST_RESCORE` rows. No `WINNER_COHORT_REFRESH` job was automatically scheduled; the count of refresh jobs after job 31056 is zero.

## 12. Final queue, lease, and database state

Final counts after run 119:

| Object | Final count |
|---|---:|
| `winner_cohort_definitions` | 367 |
| `winner_cohort_statistics` | 1,598 |
| `winner_cohort_generations` | 2 |
| `winner_cohort_refresh_state` | 2 |
| `winner_evidence_manifests` | 436 |
| `winner_evidence_manifest_members` | 2,550 |
| `DECISION_TIME` estimates | 11,435 |
| `LATEST_RESCORE` estimates | 18,308 |
| `winner_processing_runs` | 127 |
| `background_jobs` | 30,770 |

Final queue inspection found zero queued or running jobs of any type. Jobs 31055, 31056, 31057, and 31058 all have null lease owner, execution token, heartbeat, and lease expiry after completion. There were no unexpected retries or continuation jobs. Worker PID 8056 had a 1.259-second-old heartbeat and was responding.

Five old `winner_processing_runs` still say `RUNNING`: cohort-refresh runs 51, 53, and 58, and maturation runs 56 and 109. These are historical audit inconsistencies, not executable work: their owning background jobs 127, 130, and 132 are `COMPLETED`, and job 30260 is `PARTIAL`; every owning job has a cleared worker, token, and lease. They predate this activation and were not rewritten or deleted. Historical failed/lost/cancelled attempts remain intact.

The worker's process-lifetime peak working set later reached 2,227,806,208 bytes while executing the full production pipeline and test-era workload; the refresh-specific observed peak was approximately 493 MB. Final working set at the closing snapshot was approximately 455 MiB.

## 13. Verification results

| Check | Result |
|---|---|
| Focused modified-area regression set | 42 passed in 3.52 s |
| Required Winner/worker/job/PostgreSQL/settings/pipeline matrix | 306 passed in 111.63 s |
| First full-suite observation | 1,769 passed, 9 skipped, 2 timing failures in 29:29 |
| Isolated original failures | performance guardrail passed; external subprocess missed original Windows startup deadline |
| TDD harness verification | healthy worker registered/stopped with larger Windows deadline |
| Previously failing tests after fix | 2 passed in 22.90 s |
| Final full repository suite | **1,771 passed, 9 skipped, 13 warnings in 565.37 s (9:25)** |
| `python -m ruff check app scripts tests` | passed after correcting one introduced import-order issue |
| `python -m compileall app scripts tests` | passed |
| `git diff --check` | passed |

The 13 final warnings are existing Python 3.12 sqlite datetime-adapter and Alembic `path_separator` deprecation warnings. No test failed in the final full run.

## 14. Findings and risk disposition

1. **Replay-lineage admission defect — fixed and recertified.** The initial `cohort-v2` generation admitted 103 unreproducible pre-1.1 replays. `cohort-v2.1` filters them using bounded exact-lineage validation. Generation 2, targeted rescores, fresh decision estimates, and the full suite all pass.
2. **V2 API composition omission — fixed.** Immutable generation estimates did not persist composition fields. The API now derives them from the immutable manifest with one cached aggregate per manifest; live API/UI reconciliation is exact.
3. **Legacy processing audit debt — non-executable.** Five old processing rows retain `RUNNING` even though their durable jobs are terminal with cleared fencing. This is visible audit debt, not a stuck job or claimant.
4. **Transient PostgreSQL connection establishment timeouts — observed.** Several independent read-only probes encountered connection timeouts and succeeded on retry. No active job lost its lease; the production pipeline and final checks completed.
5. **Fresh pipeline partial — explained and non-Winner-blocking.** Two `MOG.A` symbol-resolution failures and one incomplete combined row caused `PARTIAL`. Every stage, including Winner capture, completed with zero Winner failures.
6. **Windows subprocess test deadline — fixed.** Healthy worker/web subprocess startup exceeded the original 15/30-second Windows-only test waits under load. Windows waits are now 60 seconds; other platforms are unchanged, and the final full suite passes.

## 15. Certification decision

**PASS_WITH_FINDINGS**

The production database/runtime is proven safe to set `WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED=true` while keeping V2 enabled. The findings above are either fixed and recertified or explicitly non-executable operational/audit debt. The most important safety properties are proven: exact V2 flags in the live worker, bounded reproducible evidence, a validated 42/42 L5-to-L0 published generation, atomic pointer movement, immutable pre-existing decision estimates, no historical rescore fan-out, a fast no-op, correct PUBLISHED-only serving, healthy fencing/heartbeat, a fresh normal pipeline with exact Winner reproduction, no automatic refresh while disabled, and a clean final full test suite.

Recommended final environment for the operator's separate rollout decision:

```env
WINNER_PROBABILITY_AUTO_MATURATION_ENABLED=true
WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED=true
WINNER_COHORT_REFRESH_V2_ENABLED=true
```

This task did **not** change `WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED`; the live `.env` remains `false` at handoff.
