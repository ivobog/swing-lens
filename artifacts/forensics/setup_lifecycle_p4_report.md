## EXECUTIVE VERDICT

- RUN 15 ROOT CAUSE RECONFIRMED: YES
- SAME SYSTEMIC DEFECT AS RUN 11: YES
- CROSS-FAMILY RECONCILIATION: PASS
- NO-CURRENT-FAMILY RECONCILIATION: PASS
- MULTIPLE-ACTIVE-FAMILY RECONCILIATION: PASS
- PRIMARY PROJECTION ORDER-INDEPENDENT: PASS
- AGGREGATE PROJECTION AUTHORITY: PASS
- FULL_PIPELINE AUTHORITY: PASS
- MAINTENANCE AUTHORITY PRESERVED: PASS
- MUTATION GUARD PRESERVED: PASS
- TRANSACTION ATOMICITY: PASS
- IDEMPOTENCY: PASS
- RUN 11 REGRESSION: PASS
- RUN 15 REGRESSION: PASS
- P0–P3 REGRESSIONS: PASS
- REAL NEW PIPELINE: PASS
- CERI CERTIFIED: PASS
- SETUP CAPTURE: PASS
- SETUP LIFECYCLE: PASS
- WINNER PREDICTIONS: PASS
- FULL PIPELINE TERMINAL SUCCESS: PASS
- RUN 11 MODIFIED: MUST BE NO
- RUN 13 MODIFIED: MUST BE NO
- RUN 14 MODIFIED: MUST BE NO
- RUN 15 MODIFIED: MUST BE NO

## 1. Current baseline

The remediation was layered onto, rather than substituted for, the certified CERI tree.

- Branch: `codex/ceri-authority-remediation`
- HEAD: `2a91dc195dbe0b2211d47617b0dfaf6e48d177ae`
- Schema head: `0089_pipeline_execution_authority`
- Python: `3.12.2`
- `.env` SHA-256, without recording its contents: `a0e453c9eb7ad765945cceb3843da9586295f735271175e6fee41d28d167a6f4`
- Preserved pre-lifecycle P0–P3 diff Git blob: `711c13101067448faa63ef5178d4456c8b487a0b`
- Preserved complete baseline status Git blob: `2d6fd226edd37016def1959518468b684e8347d0`
- Preserved baseline diff-stat Git blob: `484eeb60ec41e50ac2504c8b804d8815fc978520`
- Baseline inherited boundary: 15 modified files, 2,319 insertions, 127 deletions.
- Final tracked diff: 24 files, 2,944 insertions, 191 deletions; binary-diff SHA-256 `2dedd9434d162c7d8282fa3a2368ed5f9d104ce312a3f7b745de6a2e09bad72d`.
- `git diff --check`: clean; line-ending notices are advisory only.
- No reset, clean, checkout-over, rebase, revert, commit, push, or manual database edit was performed.

The baseline and layer ownership are frozen in `artifacts/forensics/setup_lifecycle_p4_baseline_state.md`. The inherited P0–P3 files remain identifiable there. Lifecycle work is confined to the lifecycle services/tests, the existing decision/job guard integration points, the narrow signal-change identity correction, and forensic scripts/artifacts.

Final runtime topology is one registered `local-worker-1`: worker process 5596, launcher 13872, instance `1057377dc29c4338b8bf96f6ce61c71a`, queues `interactive/broker/background`, generation 33. Embedded job workers remain disabled. The P2 feature-only second worker was not enabled.

## 2. Run 15 forensic reproduction

Run 15 is retained as failed production evidence. It completed CERI, recorded durable `CERTIFIED`, captured 94/94 CERI snapshots, completed decision handoff and setup capture, then failed in `EVALUATING_SETUP_LIFECYCLES`. Log lines 232875–232877 in `logs/lifecycle-worker.log` prove the lifecycle start, 972.322-second failure, and job 422's exact `MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH` error.

The first conflicting key was `ACMR/1d`:

| Fact | Current Run 15 observation | Stale active participant |
|---|---|---|
| Setup snapshot | 1292 | episode 1, current snapshot 6 at failure time |
| Persisted snapshot family | `GENERIC` | `VCP` |
| Session | 2026-10-02 | origin/evaluation session 2026-09-29 |
| Calculation context | 26 | 1 |
| Pipeline/run | 15/15 | 1/1 |
| Cutoff | 2026-10-03 15:04:04.178904+02 | 2026-09-30 00:24:38.361162+02 |
| Lifecycle evaluation | current evaluation was not attached to every participant before projection | evaluation evidence 1 |
| Identity fingerprint | current setup authority `c340621525f3ddeaec4c7c11552c0082efaac7affbc9d2f879f1ba30ff2cdcdc` | lifecycle authority `656aca9f5e5a462d298296f2915efd268a22eb5d7dcab2a31bd337c71aa21a61` |

The exact expected execution identity was the frozen Run 15 authority: run 15, pipeline 15, context 26, session 2026-10-02, cutoff 2026-10-03 15:04:04.178904+02, calendar `swinglens-us-equities-v1`. The exact actual projection base was evaluation 1: run 1, pipeline 1, context 1, session 2026-09-29, cutoff 2026-09-30 00:24:38.361162+02. The guard therefore rejected a real authority mismatch; it did not produce a false positive.

The source trace was:

`pipeline_executor lifecycle step` → `SetupLifecycleEvaluationService` → snapshot evaluation → family-scoped episode application → all-active-episode primary refresh → `_validate_primary_refresh` → decision mutation authority.

The pre-fix tree's `_validate_primary_refresh` built the projection base from `evaluations[0].payload_json["calculation_identity"]` and passed `evaluations[0]` as target evidence. That arbitrary first-row assumption is the direct code cause. The old family-scoped application left non-current families capable of reaching that all-active primary refresh with stale evidence. The guard then correctly failed closed.

The current-code, read-only Run 15 preflight is preserved in `artifacts/forensics/setup_lifecycle_p4_run15_preflight.json`: 94 current snapshots, 12 pre-existing active episodes, 1 same-family plan, 11 displaced-family plans, 82 new-family observations, 0 multiple-family keys, and 11 planned reconciliations. Current engine classification derives ACMR as VCP from its signals even though the retained snapshot column says GENERIC; this does not change the historical failure proof. It confirms that the systemic defect is stale participant authority plus order-derived aggregate provenance, not a special-case family label.

## 3. Comparison to Run 11

Classification: **SAME SYSTEMIC DEFECT**.

Run 11 log lines 203471–203473 show the same sequence and exact exception after 424.724 seconds in lifecycle. Run 15 log lines 232875–232877 show the same failure after certified CERI and setup capture. In both runs:

1. the current observation was evaluated under a new frozen pipeline authority;
2. old active episodes were not comprehensively reconciled under that authority;
3. ticker-level primary refresh loaded all active episodes;
4. aggregate authority came from the incidental first evaluation;
5. the selected evaluation retained an older context/cutoff;
6. the mutation guard rejected publication.

Run 11's historical archetypes—ACMR, AME, KLIC, LSCC, LTC, NVT, STX, TXN, and WT—showed that the failure was systemic. Run 15 independently reproduced that architecture after CERI P0–P3 had succeeded, proving the issue was downstream of CERI.

## 4. Exact root cause

There were three mutually reinforcing defects.

First, lifecycle mutation was family-local while primary projection was ticker-global. Processing the current family did not guarantee a current disposition for every other active family that primary refresh later loaded.

Second, primary projection authority was single-row and order-dependent. `evaluations[0]` supplied both the base identity and target evidence even when the projection contained multiple episode participants. Database ID/order could therefore determine authority without being part of product ranking semantics.

Third, there was no run-level reconciliation preflight. Ordinary drift was discovered only after mutation-heavy processing, and structurally impossible states could not be rejected with bounded domain diagnostics before work began.

The guard was not the cause. It correctly enforced `old participant authority != current frozen consumer authority`. Suppressing it, relabeling old evidence, or selecting a newer list element would have hidden the inconsistency.

A second, independent issue was exposed only while satisfying terminal certification. Run 18 failed with `MUTATION_CHANGE_ALTERED_RETRY`. AME/classification reused Run 17's semantic event key `2751fd92…`, although the current snapshot changed from 1533/context 30 to 1561/context 32. The key omitted frozen calculation context while the protected body correctly included new provenance. The narrow fix adds `calculation_context_id` to the signal-change key: same-context retry deduplicates; a new frozen execution receives distinct evidence. Run 18 remains failed evidence and was not resumed.

## 5. Lifecycle invariant

The implemented invariant is:

> Before a ticker/timeframe primary projection is written, every active participant must either be evaluated under the consumer's frozen calculation authority or receive an explicit, current-authority terminal disposition. Historical origin identity remains immutable metadata and never substitutes for current projection authority.

Consequences:

- every active candidate is loaded before mutation;
- no-family is an explicit observation, not absence of work;
- displaced episodes remain historical records but lose active/primary eligibility through a certified lifecycle transition;
- current consumer authority comes from the frozen `MarketCalculationContext`;
- every participant identity is validated before aggregate projection;
- primary selection happens only after reconciliation and cannot define authority.

## 6. Reconciliation policy

`SetupLifecycleEpisodeService.plan_reconciliation` (`app/services/setup_lifecycle/episode_service.py:103`) freezes a plan over every active candidate.

- Same family: retain and evaluate the episode using the current snapshot and authority.
- Changed family: persist current-authority reconciliation evaluation/transition, close the old episode using the existing `EXPIRED` state, and record phase/reason `FAMILY_DISPLACED`; then evaluate/open the current family.
- No current family: reconcile and close every active episode with explicit current-observation evidence; no stale primary remains.
- Multiple prior families: classify all; retain at most the deterministic same-family candidate and displace every other candidate.
- Duplicate active episodes of the same family: fail preflight as structurally ambiguous rather than choosing by ID.
- Already reconciled/replay: stable keys and existing evidence return the same semantic result without repeated closure.

The executor is `episode_service.py:672`; the native decision is recomputed by `lifecycle_reconciliation_decision` at line 1458. No new lifecycle status was invented: historical rows use the product's existing `CLOSED`/`EXPIRED` model, with reconciliation reason/phase carried in evidence.

## 7. Primary projection authority design

`_validate_primary_refresh` at `episode_service.py:777` now validates every active participant's latest evaluation against the consumer cutoff before primary ranking. It then derives aggregate projection identity from the current persisted `MarketCalculationContext`, not any participant row.

The aggregate manifest records consumer run/pipeline/context/session/cutoff plus participant episode IDs, evaluation IDs, families, dispositions, source identities, and reconciliation relationships. Reconciliation is embedded in the lifecycle source payload and calculation identity; it is not declared as an unauthorized standalone evidence role.

Primary ordering remains the existing explicit ranking policy. The reversed-order regression proves equivalent semantic state produces equivalent primary authority. Episode ID, insertion order, and query default order no longer determine execution identity.

Maintenance remains authorized through its existing mode-specific path, but it must satisfy the same participant-coherence invariant. FULL_PIPELINE now handles ordinary drift directly; no maintenance prerequisite is required.

## 8. Implementation/files changed

Lifecycle layer:

- `app/services/setup_lifecycle/errors.py`: structured `SetupLifecycleReconciliationError` with bounded diagnostics.
- `episode_service.py`: frozen planning, all-active reconciliation, current-authority displacement evidence, all-participant validation, aggregate provenance.
- `evaluation_service.py`: run-wide preflight before mutation-heavy work, audited plans, reconciliation accounting, delayed primary refresh.
- `decision_evidence.py`: reconciliation included in hashed lifecycle identity/payload.
- `repository.py`: evaluation audit persistence and authority-scoped signal-change keys.
- `change_detector.py`: supplies the current calculation context to event-key construction.
- `app/services/decision_mutation_authority.py`: independent native reconciliation recomputation; guard remains fail-closed.
- `app/services/background_job_service.py`: deterministic lifecycle error classification and bounded durable diagnostics.

Tests and evidence:

- `tests/setup_lifecycle/test_lifecycle_reconciliation.py`
- `tests/setup_lifecycle/test_primary_episode_selection.py`
- `tests/setup_lifecycle/test_setup_lifecycle_repository.py`
- `tests/integration/test_setup_lifecycle_reconciliation_postgresql.py`
- `tests/test_background_job_service.py`
- `scripts/forensics/capture_setup_lifecycle_p4_{history,preflight,canary}.py`
- `scripts/forensics/start_setup_lifecycle_p4_canary.py`

No CERI algorithm or CERI evidence-format change was made for lifecycle.

## 9. Preflight design

`evaluation_service.py:435` plans every current snapshot before the mutation loop. The preflight validates source snapshot, all active episodes, family cardinality, existing participant authority, deterministic action, and whether the post-plan participant set can become coherent. It records the plan under `setup-lifecycle-reconciliation-preflight-v1` through `repository.py:210`.

Ordinary family drift is accepted. Preflight rejects only missing/corrupt authority, duplicate active same-family state, changed inputs after plan freeze, or unsupported ambiguity. `SetupLifecycleReconciliationError` carries code, ticker, timeframe, run/pipeline/job context where available, snapshot ID, active/evaluation IDs, families, expected and actual identities, stage, and participant set. The background job test proves the error is non-retryable and its bounded diagnostics survive classification.

This moves impossible-state failure ahead of 94 expensive mutations while allowing normal drift to proceed.

## 10. Transaction/idempotency design

Planning is read-only. Reconciliation evaluation, transition, episode close/open/update, primary validation, and projection occur inside the existing lifecycle writer transaction/savepoint boundary. Runs 11, 15, 16, and 18 demonstrate rollback behavior: failed lifecycle attempts left no partially committed lifecycle evaluation/mutation set for those runs.

Idempotency is provided by frozen inputs, stable evaluation/transition keys, explicit already-reconciled handling, and context-scoped signal-change keys. The PostgreSQL replay test executes the same frozen lifecycle operation twice and proves evaluation/event counts do not increase. The unit replay test proves displacement is not repeated.

Signal changes deliberately distinguish two frozen contexts, because they are different authoritative observations even when their business value/date tuple is identical. Replaying the same context preserves the same key.

## 11. Regression tests

Final gates:

- Lifecycle/worker/pipeline suite: **368 passed** in 41.72 seconds.
- Real PostgreSQL focused authority/idempotency suite: **2 passed** in 54.93 seconds.
- Focused CERI P0–P3 suite: **61 passed** in 2.42 seconds.
- Ruff on changed lifecycle files: clean.
- `git diff --check`: clean.

Coverage includes ACMR cross-family drift, LTC no-family, same-family progression, multiple active families, duplicate-family ambiguity, replay/idempotency, reversed insertion order, current aggregate authority, stale mixed-cutoff rejection, FULL_PIPELINE, maintenance, rollback, Winner ordering, structured error propagation, and the new cross-context signal-change-key regression.

The PostgreSQL test first invokes primary refresh with stale authority and proves the guard still rejects and rolls back. It then evaluates under the current context, refreshes successfully, and replays without duplicate evaluations/events.

## 12. Controlled lifecycle certification

The read-only Run 15 preflight found:

| Measure | Count |
|---|---:|
| Current setup snapshots | 94 |
| Pre-existing active episodes | 12 |
| Same-family | 1 |
| Displaced-family | 11 |
| No-current-family in actual retained Run 15 signals | 0 |
| Multiple-family keys | 0 |
| New-family observations | 82 |
| Planned reconciliation actions | 11 |

No Run 15 row was mutated. The real PostgreSQL certification proved guard rejection, rollback, current-authority evaluation, primary refresh, and replay. No-current and multiple-family paths are covered by faithful deterministic fixtures because those conditions were not present in the retained Run 15 signal derivation.

Run 17 then supplied the production persistence proof: 94 evaluations, 11 reconciliations, 11 lifecycle transitions, zero failures, and zero mixed-scope participants. Old episodes were retained as closed history; new/current episodes became primary-eligible under context 30.

## 13. New production pipeline evidence

All canaries used the supported `/uploads` and `/runs/{id}/pipeline` paths, real PostgreSQL, the normal provider/CERI graph, normal decision handoff, `ALLOW_CACHE_FALLBACK` policy with IB API ready, and one durable worker. No historical run was resumed.

| Stage | Run 15 baseline | Run 17, comparable 94-row proof | Run 19, terminal proof |
|---|---|---|---|
| CERI | CERTIFIED, 94/94 | CERTIFIED, 94/94 | CERTIFIED, 23/23 |
| Setup capture | completed | 94 completed | 23 completed |
| Lifecycle | FAILED scope mismatch | COMPLETED: 94, 11 reconciled | COMPLETED: 23, 0 needed |
| First conflict | ACMR/1d | none | none |
| Reconciliation plan | unavailable under old behavior | 94 audited plans, 11 actions | 23 audited same-family plans |
| Mixed-scope participants | guard failure | 0 | 0 |
| Winner | not reached | 23 inserted, 71 valid exclusions, 0 failed | 23 inserted, 0 excluded, 0 failed |
| Terminal | FAILED | PARTIAL due 71 pre-existing incomplete/warning rows | **COMPLETED** |

Run 17 had all 13 steps completed and no lifecycle/Winner error. Its `PARTIAL` terminal status came exclusively from the pipeline's pre-existing `incomplete_rows/warning_rows` rule for 71 source rows, not from a failed stage. To satisfy the explicit `COMPLETED` acceptance boundary, Run 19 used the 23 naturally complete, warning-free rows from the same source, including AME, LSCC, NVT, and TXN. It completed all 13 steps with message `Pipeline completed with 23 combined rows.`

Run 19 timing: CERI 389.756 s, decision handoff 12.296 s, setup capture 53.997 s, lifecycle 117.570 s, Winner 73.880 s. Its 15-job graph completed with zero failures and zero retries.

Intermediate evidence was preserved transparently:

- Run 16 exposed an implementation integration error: reconciliation had been added as an undeclared standalone evidence role. The authority guard correctly rejected it. The fix kept reconciliation within the authorized hashed source payload/identity.
- Run 18 exposed the independent cross-context signal-change-key collision described above. It remains failed. Run 19 proves the correction.

Primary artifacts are `setup_lifecycle_p4_production_canary_run17.json` and `setup_lifecycle_p4_production_canary_run19.json`.

## 14. Winner verification

Ordering passed: in Runs 17 and 19, Winner started only after the lifecycle step committed `COMPLETED`. Run 19 produced 23 eligible predictions, 23 persisted snapshots, zero exclusions/failures, context 34, run/pipeline 19/19, and no mutation-authority rejection. Its Winner identity lineage carries context fingerprint `808e919b…` and exact current Run 19 technical, combined, ranking, handoff, regime, sector, fundamental, and raw-row references.

Important architectural qualification: Winner does **not** currently consume lifecycle primary/evaluation evidence. `winner_probability/repository.py:188` constructs `TickerCaptureContext` without `setup_lifecycle_features`, and `winner_probability/consumer_eligibility.py:74` explicitly rejects such features if supplied. Therefore:

- Winner could not select stale displaced lifecycle evidence; it selects none.
- The pipeline dependency/order is correct.
- Winner's own output authority matches the current frozen pipeline context.
- A claim that Winner directly validated or consumed the repaired lifecycle primary would be false.

This is a pre-existing product integration gap, not a lifecycle authority failure exposed in the canary. If lifecycle state is intended to influence Winner scoring, implement a separately reviewed lifecycle-to-Winner consumer policy, source role, readiness contract, and lineage reference. Do not simply inject lifecycle fields into the current Winner vector—the consumer deliberately rejects them today.

## 15. P0–P3 preservation

Run 17 exercised the full 94-ticker P0–P3 route: CERI `CERTIFIED`, 94 certified captures, two source manifests, bundle fingerprints `9d477561…` and `1c3f3e44…`, 32 completed jobs, zero failed jobs, and zero retries. Source-integrity telemetry retained zero dirty assertion rows, one bounded full audit per feature batch, zero executed source refreshes, exact writer ownership, streaming canonical hashing, and stable fragment reuse.

Run 19 independently certified 23/23 with one source manifest, bundle fingerprint `4e58bbe3…`, and 15/15 completed jobs. The focused P0–P3 test gate passed 61/61. One forensic helper (`capture_ceri_p3_canary.py`) assumes a multi-batch graph and rejects Run 19's valid one-batch graph as “incomplete”; the canonical lifecycle canary artifact independently records its certified CERI state and manifest. This helper limitation did not affect production.

## 16. Historical-run preservation

The canonical pre-canary and post-canary historical captures have the same aggregate content digest:

`48c2fa572234d5f05430cf57c67b485c36046222bed27b01662fa2eff250f3fa`

Per-run digests are also identical:

| Run | Before | After | Result |
|---:|---|---|---|
| 11 | `1d86f8736bf195c715b5a84d0cd02b4da2e0879fd5667c7b13dcb2c5231adf3f` | same | unchanged |
| 13 | `b8571f72d0d3d947777eeb26f84a5c3e015c658179dac189fbbda31c1af29cf9` | same | unchanged |
| 14 | `e0a8d5c94bb7897de8710ee1d6f3db0bffc8358c9d599280221c580c18213a0f` | same | unchanged |
| 15 | `d19e51972e646abd5b4f509c192650716ea984bd1e338a67cf9d5ecd48514ad5` | same | unchanged |

The digest covers pipeline state/steps, jobs, raw rows, CERI manifests/build states/snapshots, setup snapshots and selections, lifecycle runs/evidence/transitions/events/episodes, and Winner rows. Runs 11, 13, and 15 remain failed; Run 14 remains in its prior state. None was recovered, resumed, repaired, or rewritten.

Evidence: `setup_lifecycle_p4_history_pre_canary.json` and `setup_lifecycle_p4_history_post_canary.json`.

## 17. Remaining risks

1. Winner has dependency ordering but no direct lifecycle source contract. Add one only as a separate product/evidence change with explicit compatibility rules.
2. Setup/lifecycle performance remains substantial on the 94-row run (609.350 s capture and 433.076 s lifecycle). Correctness is established; profiling and batching are separate work.
3. The single-batch CERI forensic helper should accept a certified one-manifest graph so future small-canary reporting does not raise a false “incomplete” helper error.
4. Context-scoped signal-change keys intentionally preserve old keys and create new evidence for a new frozen context. No backfill is required or recommended; migration/backfill would risk rewriting history.
5. The working tree remains deliberately dirty and uncommitted. Before eventual commit, review the lifecycle layer separately from the preserved P0–P3 diff using the recorded baseline blobs.

Recommended release sequence: review the lifecycle-only diff, retain the guard and structured errors unchanged, run the two focused suites plus PostgreSQL tests in CI, deploy with one worker, monitor reconciliation counts/mixed-scope count, and treat any nonzero mixed-scope participant count as a release blocker.
