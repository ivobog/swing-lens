# OWPE Post-Activation Fresh-Run Certification

## Certification verdict

Fresh Run 106 demonstrates a working production Ranking stage and real probability estimates whose evidence comes exclusively from the reviewed pre-1.1 replay bridge. Exact samples, probabilities, intervals, decision/replay identities, and manifests reproduce. The run is certified with nonblocking operational findings because Interactive Brokers was unavailable and the full pipeline therefore ended `PARTIAL`; persisted bars allowed all core Ranking and OWPE steps to complete correctly.

## Environment and run identity

| Field | Value |
|---|---|
| Branch | `codex/ceri-run101-remediation` |
| Activation base commit | `04684a145b62daefb54eebf106b870591b8b02dc` |
| Implementation/report commit | `8081ba3f70e6d947456c44d36df4853b69d485e2` |
| Schema | `0046_owpe_pre11_training_compatibility` |
| Database | PostgreSQL 18.3 / `swinglens` |
| Active config hash | `218a897655d6c42e19043e1136cb4d578705632f13acf037bc9ce1beef57b527` |
| Feature schema | `owpe-features-1.0.0` |
| Calculation version | `owpe-calc-1.1.0` |
| Upload run | 106 |
| Pipeline run | 103 |
| Durable job | 30316 |
| Source file | `money money_2026-08-14.csv` |
| Uploaded rows | 186 |
| Pipeline terminal state | PARTIAL |

## Session and cutoff proof

Run 106 Winner source cutoff was `2026-08-15T01:22:15.946130+02:00`, equivalent to `2026-08-14T19:22:15.946130-04:00` in New York. The approved U.S. market-calendar utility returns:

```text
latest completed U.S. signal session = 2026-08-14
next regular NEXT_OPEN session        = 2026-08-17
H5 entry-inclusive due session        = 2026-08-21
```

All 186 snapshots persist prediction-as-of `2026-08-14` and planned entry `2026-08-17`. Finding: `CONFIRMED_EXPECTED_BEHAVIOR` (P0).

## Durable pipeline and Ranking

All 12 durable steps completed, including `RANKING_PROFILES` between combine and downstream capture. Production counts:

| Artifact | Count |
|---|---:|
| CombinedResult | 186 |
| Ranking profiles | 5 |
| RankingResult | 930 |
| Winner snapshots | 186 |
| Winner `DECISION_TIME` estimates | 184 |
| Excluded current predictions | 2 |
| Winner snapshots with ranking provenance | 186 |

The two missing estimates are intentional exclusions (`MIAX` prediction 9093 and `MOG.A` prediction 9170) for insufficient completed bars. They are represented as missing estimates, not fake zeros or priors.

The pipeline is `PARTIAL` because IB connectivity failed, one combined row was incomplete, and warning rows were present. `RANKING_PROFILES`, setup capture/evaluation, and Winner capture all completed. This is a `DATA_QUALITY_ISSUE` (P2), not an OWPE integrity failure.

## Probability/evidence population

All 184 eligible current predictions received non-null cohort estimates. No estimate has ghost interval/model/calibration/cohort semantics:

- source = `COHORT`;
- baseline lifecycle label = `BASELINE`, not a fake trained-model status;
- calibration status = `cohort_baseline`, not a model calibration claim;
- selected cohort definition and level are non-null;
- probability, bounds, width, n, effective n, and manifest are populated together;
- composition explicitly states native 1.1 = 0 and pre-1.1 replay = sample n.

Selected-level distribution:

| Level / grade | Count |
|---|---:|
| L3 / High | 110 |
| L3 / Medium | 37 |
| L3 / Low | 17 |
| L4 / Medium | 4 |
| L4 / Low | 16 |

Fresh predictions correctly use the most-specific display-eligible cohort. The separately controlled L5 estimate 9011 proves the full 390-member global fallback with visible replay composition.

## Representative traces and exact reproduction

| Prediction / estimate | Ticker | Setup | Sector | Dependence | Selected | n / wins | p | Reproduction |
|---|---|---|---|---|---|---|---|---|
| 9046 / 9012 | AMN | Avoid / Distribution risk | Risk-off | dependent | L3 | 173 / 69 | 0.409326 | exact |
| 9053 / 9019 | STNG | Strong candidate / No trade | Risk-off | independent | L4 | 18 / 7 | 0.447368 | exact |
| 9054 / 9020 | SLDE | Watchlist / Momentum continuation | Risk-off | independent | L4 | 30 / 16 | 0.520000 | exact |
| 9067 / 9033 | FTI | Candidate / Momentum continuation | Risk-off | dependent | L3 | 67 / 15 | 0.287356 | exact |
| 9124 / 9089 | CSWC | Avoid / Extended momentum | Lagging | dependent | L3 | 63 / 24 | 0.409639 | exact |
| 8913 / 9011 | FHI controlled rescore | Strong candidate | Risk-off | dependent | L5 | 390 / 145 | 0.378049 | exact |

These traces prove that a dependent current prediction can receive a probability from independent historical evidence. Dependence affects future training membership; it does not suppress serving.

For each reproduced estimate, `ReproductionService` returned `matches=true`, no mismatches, and exact n, effective n, wins, posterior, interval, full manifest hash, decision IDs, replay IDs, forward identities, and bar lineage.

## API, UI, and pagination reconciliation

Read-only TestClient verification against the running application contract produced:

| Semantic count | Value |
|---|---:|
| Run total | 186 |
| Filtered total | 186 |
| Filtered estimate total | 184 |
| Estimate total | 184 |
| Calibrated/served total | 184 |
| Insufficient estimate total | 0 |
| Missing estimate total | 2 |

Page 1 returned 100 unique rows and a non-null cursor. Page 2 returned 86 additional unique rows and a null cursor. The union is exactly 186 with no overlap. Both pages report identical run-level totals, proving cards are not using page length.

The HTML page returned HTTP 200 and rendered 100 first-page prediction links. It displayed replay composition for the 98 first-page rows with estimates and preserved the two intentional missing-estimate rows. API ranking profile/source IDs match DB rows; all 186 snapshot-to-ranking joins have matching run, ticker, and profile.

The run API resolves the referenced RankingResult for list rows. Representative AEIS prediction 9220 exposes final rank 112, defensive-quality profile rank 107, profile score 4.3306, and RankingResult PK 49656; none of these values is inferred or fabricated.

## Immutability and safety reconciliation

- Run 104 snapshot checksum and 184-row decision-time estimate checksum are unchanged.
- Run 105 snapshot checksum and 184-row original decision-time estimate checksum are unchanged.
- Estimate 9011 is an appended `LATEST_RESCORE`, not a rewrite of Run 105 history.
- The protected pending H5 population through 2026-08-13 remains exactly 1,111.
- No maturation job was created by bridge activation or fresh certification.
- Automatic maturation remains false.
- No legacy outcome was relabeled and no historical eligibility Boolean was mass-updated.

## Tests and residual findings

- Focused OWPE/pipeline/background/settings suite: 269 passed.
- Final Ranking/pipeline/reproduction/template slice: 47 passed.
- Complete non-destructive repository suite after all corrections: 1,659 passed and 9 skipped. A separate destructive E2E harness run failed because it expects Winner auto-maturation and CERI alerts; those expectations conflict with the mandated `winner_probability_auto_maturation_enabled=false` scope and are not evidence of a bridge, Ranking, or reproduction defect.
- Replay-lineage regression tests prove changed and revised-after-cutoff bars fail closed.

`CONTRACT_AMBIGUITY` (P2): the fresh run naturally selected eligible L3/L4 cohorts rather than L5. The exact L5 proof therefore remains the explicit controlled rescore, clearly labeled and separate from immutable decision-time selection.

`DATA_QUALITY_ISSUE` (P2): IB was unavailable during fresh execution. A later certification with live broker connectivity can remove the operational `PARTIAL` state, but it is not required to reconstruct the persisted OWPE evidence.

PASS_WITH_NONBLOCKING_FINDINGS
