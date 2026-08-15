# OWPE L5 Production Reproduction Report

## Result

The active global L5 cohort was built from persisted compatibility decisions and replay rows, not from manually inserted expected statistics. It contains the exact reviewed 390 members and reproduces without consulting mutable historical feature values.

Finding: `CONFIRMED_EXPECTED_BEHAVIOR` (P0). L5 is statistically and contractually reproducible from append-only artifacts.

Branch: `codex/ceri-run101-remediation`. Implementation/report commit: `8081ba3f70e6d947456c44d36df4853b69d485e2`. Schema: `0046_owpe_pre11_training_compatibility`.

Implementation anchors are `Pre11L5ActivationService.activate` at `app/services/winner_probability/pre11_activation_service.py:72`, canonical manifest decimal serialization at `app/services/winner_probability/evidence_manifest_service.py:135`, and replay-lineage verification at `app/services/winner_probability/reproduction_service.py:137`.

## Persisted identities

| Artifact | Identity |
|---|---|
| Outcome definition | PK 3, `T2_5_S2_0_H5_NEXT_OPEN` |
| L5 cohort definition | PK 1, `L5:aa19550e1b53b14d29315fdbe5204f23bb9c5dd03c89dbfd8313b3988b0c04ba` |
| L5 cohort statistic | PK 47 |
| Controlled prediction | PK 8913, ticker FHI, Run 105 |
| Controlled `LATEST_RESCORE` | PK 9011 |
| Training cutoff | `2026-08-15T01:00:00+02:00` |
| Reviewed projection hash | `dda5048538702f6eb9ae42f2aebefc86f19b988e3f2aa494e34900f27d462f54` |
| Full evidence manifest hash | `6608cc85d87a9cf7957d21e78fdeef3c129b742a6403924e01e16958044be414` |
| Evidence members | 390 |
| Origins | native 1.1 = 0; pre-1.1 replay = 390 |

The reviewed projection hash binds prediction ID, source snapshot manifest hash, and replay bar-lineage hash under the exact reviewed request. The full evidence hash additionally binds the persisted decision, replay, forward-outcome, target/stop, and inclusion identities used by the estimate.

## Statistical reproduction

| Quantity | Persisted and reproduced |
|---|---:|
| sample n | 390 |
| effective n | 390.000000 |
| wins | 145.000000 |
| raw rate | 0.371795 |
| prior | Beta(10, 10) |
| posterior | Beta(155, 255) |
| point probability | 0.378049 |
| lower bound | 0.331112 |
| upper bound | 0.424986 |
| interval width | 0.093874 |
| evidence grade | High |
| median return | 0.768126% |
| median MFE | 2.850947% |
| median MAE | -2.405736% |

`ReproductionService.reproduce_estimate(estimate_id=9011)` returned `matches=true` and an empty mismatch list for n, effective n, wins, posterior, both interval bounds, width, config hash, exact member identities, and manifest hash.

## Replay lineage enforcement

Each replay member is resolved through its persisted eligibility decision and replay ID. Reproduction verifies:

- forward outcome PK and exact revision;
- replay PK and exact revision;
- every PriceBar or PriceBarRevision PK;
- price-bar hash and revision number;
- revision observation at or before the replay source cutoff;
- the complete bar-lineage hash.

A changed unrevisioned bar or a revision observed after the cutoff fails closed. Targeted regression tests exercise both cases.

## L0-L5 semantics and serving

The activation phase intentionally materialized L5 first. The controlled FHI trace at the activation cutoff was:

| Level | Raw/effective n | Result |
|---|---:|---|
| L0 | 0 / 0 | missing ranking/profile combination |
| L1 | 0 / 0 | no exact feature combination |
| L2 | 0 / 0 | no exact feature combination |
| L3 | 18 / 18 | display eligible |
| L4 | 18 / 18 | display eligible |
| L5 | 390 / 390 | explicitly materialized controlled L5 proof |

Estimate 9011 has selected level L5, selected definition PK 1, attempted level/key L5, and no model or calibration fiction. It is a separate `LATEST_RESCORE`; the original Run 105 `DECISION_TIME` estimates remain immutable.

`CONTRACT_AMBIGUITY` (P2): the product request called for a controlled L5 serving proof while the ordinary most-specific backoff algorithm would select eligible L3 for FHI. The activation command therefore records an explicit controlled L5 rescore rather than representing it as the ordinary most-specific decision-time selection. Fresh Run 106 correctly selected L3/L4 where those cohorts met the minimum.

## Additional exact reproductions

Five fresh-run estimates spanning dependence states, setup families, cohort levels, and the Lagging sector outlier also reproduced exactly:

| Estimate | Ticker | Level | n | wins | p | Manifest prefix |
|---:|---|---|---:|---:|---:|---|
| 9012 | AMN | L3 | 173 | 69 | 0.409326 | `96309ec64103` |
| 9019 | STNG | L4 | 18 | 7 | 0.447368 | `6dc4c43dc804` |
| 9020 | SLDE | L4 | 30 | 16 | 0.520000 | `65c8b9c8c641` |
| 9033 | FTI | L3 | 67 | 15 | 0.287356 | `eb26df18405c` |
| 9089 | CSWC | L3 | 63 | 24 | 0.409639 | `743aae0e0127` |

All have native/replay composition 0/n and compatibility policy `owpe-pre11-eligibility-1.0.0`.

Certification state: `PASS_WITH_NONBLOCKING_FINDINGS`
