# Market Regime Temporal Contract Remediation

Date: 2026-09-25  
Branch: `codex/market-regime-temporal-remediation`  
Starting HEAD: `33a2d967f2966c334642cb4fcc05a5e78f129a0c`  
Implementation HEAD: `49c8b2faad8692e92323b572b436c17e3eb6731a`

This report covers the retained run 164 / pipeline 155 Market Regime failure and deterministic downstream certification. No upload, live pipeline, IB release run, repeat small slice, or 100-symbol final canary was submitted. Retained production evidence was read only.

## 1. Executive verdict

| Decision | Verdict |
| --- | --- |
| ROOT CAUSE IDENTIFIED | YES |
| MARKET REGIME TEMPORAL CONTRACT FIXED | YES |
| TEMPORAL SAFETY PRESERVED | YES |
| NEGATIVE FUTURE-STATE CONTROL PASSED | YES |
| SAME DEFECT FOUND IN OTHER DOWNSTREAM WRITERS | 1 — Sector/ETF market-source loader |
| MARKET REGIME REPLAY PASSED | YES |
| DOWNSTREAM THROUGH WINNER PASSED | YES |
| FROZEN EXACT-10 PIPELINE PASSED | YES |
| TECHNICAL RECOVERY REGRESSED | NO |
| READY FOR REPEAT SMALL LIVE VERTICAL SLICE | YES |
| READY FOR FINAL 100-SYMBOL CANARY | NO |

The next live action is a repeat of the small vertical slice. The final 100-symbol canary remains prohibited until that repeat succeeds.

## 2. Exact root cause

The failure was not caused by computation time, storage time, timezone conversion, Technical output, or a defective temporal validator. Market Regime bounded its benchmark reads by pipeline 155's frozen session and cutoff but did not pass calculation context 17 into the price repository. Consequently, the repository correctly hid SPY/QQQ bars first acquired after the cutoff, even though those bars were explicitly authorized by pipeline 155's immutable acquisition plan/scope/refresh lineage.

Retained read-only reconstruction produced:

```text
artifact:
  type = MarketRegimeSnapshot
  id = none (the transaction rolled back before a durable snapshot was committed)
  temporal_field = as_of_date
  actual = 2026-09-23
  input_as_of_session = 2026-09-24
  calculation_cutoff_at = 2026-09-24T22:43:46.618832Z

authority:
  run = 164
  pipeline = 155
  calculation_context = 17
  cutoff_local = 2026-09-25T00:43:46.618832+02:00
  cutoff_utc = 2026-09-24T22:43:46.618832Z
  business_session = 2026-09-24
  calendar = swinglens-us-equities-v1

validator:
  function = artifact_mutation_context
  rule = artifact native session and cutoff must equal CalculationIdentity.temporal
  expected_session = 2026-09-24
  actual_session = 2026-09-23
  expected_cutoff = 2026-09-24T22:43:46.618832Z
  actual_cutoff = 2026-09-24T22:43:46.618832Z
  failure = MUTATION_ARTIFACT_TEMPORAL_MISMATCH
```

The exact runtime path was:

```text
MarketRegimeCommandCenterService.build_snapshot
  -> MarketRegimeRepository.upsert_snapshot
  -> MarketRegimeRepository._persist_evidence
  -> declare_core_evidence_mutation
  -> artifact_mutation_context
  -> ValueError("MUTATION_ARTIFACT_TEMPORAL_MISMATCH")
```

The retained visibility contrast proves the omission:

| Symbol | Read without context 17 | Read with context 17 | Pipeline-owned provenance |
| --- | --- | --- | --- |
| SPY | latest session 2026-09-23; 809 states | latest session 2026-09-24; 810 states | fetch run 232 / item 64114 |
| QQQ | latest session 2026-09-23 | latest session 2026-09-24 | fetch run 232 / item 64116 |

The prior deterministic recovery gate missed the regression because it replaced Market Regime, Combined, Ranking, CERI, Setup/Lifecycle/Alerts, and Winner with orchestration doubles. Unit loader doubles also did not model a benchmark bar first acquired after the cutoff under pipeline-owned authority.

## 3. Before/after temporal model

```text
BEFORE
frozen max_session = 2026-09-24
frozen as_of cutoff = 2026-09-24T22:43:46.618832Z
calculation_context_id = omitted
  -> normal PIT view excludes post-cutoff insertion
  -> SPY/QQQ effective source session = 2026-09-23
  -> MarketRegimeSnapshot.as_of_date = 2026-09-23
  -> identity.as_of_session = 2026-09-24
  -> validator rejects the real mismatch

AFTER
frozen max_session = 2026-09-24
frozen as_of cutoff = 2026-09-24T22:43:46.618832Z
calculation_context_id = 17
  -> repository admits only the post-cutoff rows owned by context 17's
     immutable acquisition plan/scope/refresh authority
  -> unrelated post-cutoff rows remain invisible
  -> SPY/QQQ effective source session = 2026-09-24
  -> MarketRegimeSnapshot.as_of_date = identity.as_of_session = 2026-09-24
  -> evidence mutation succeeds

computation/storage timestamps remain metadata and may occur after cutoff;
the artifact's domain session and immutable calculation authority still must match.
```

The validator was not weakened. A snapshot claiming 2026-09-25 against the 2026-09-24 authority still raises `MUTATION_ARTIFACT_TEMPORAL_MISMATCH`.

## 4. Caller sweep

`artifact_mutation_context` selects the first native domain field present from `as_of_session`, `as_of_date`, and `data_as_of_date`, and the native cutoff from `cutoff_at` or `calculation_cutoff_at`. Every production caller of `persist_core_evidence` / `declare_core_evidence_mutation`, plus the requested later decision stages, was assessed.

| Stage/artifact | Temporal field | Authority used | Classification | Action |
| --- | --- | --- | --- | --- |
| FundamentalScore | no native session/cutoff columns; Calculation Identity from frozen pipeline/raw source | frozen pipeline context and effective configuration | CORRECT | None |
| TechnicalScore | identity session; native `calculation_cutoff_at`; `input_as_of_session` is lineage metadata | frozen pipeline context plus canonical source manifest | CORRECT | None; recovery unchanged |
| MarketRegimeSnapshot | native `as_of_date`, `calculation_cutoff_at`; source `input_as_of_session` | frozen pipeline context 17 | SAME_DEFECT | Pass `calculation_context_id` to both benchmark reads |
| SectorRotationSnapshot / ETF context | native `as_of_date`, `calculation_cutoff_at`; ETF source sessions in evidence | snapshot date already frozen; market reads lacked pipeline acquisition visibility | SAME_DEFECT | Pass `calculation_context_id` to benchmark/proxy reads |
| CombinedResult | no native temporal column; identity derived from pinned Fundamental + Technical evidence | source compatibility plus frozen pipeline context | CORRECT | None |
| RankingResult | no native temporal column; identity derived from pinned inputs/profile | source compatibility plus frozen pipeline context | CORRECT | None |
| CeriScoreSnapshot | native `as_of_session`, `cutoff_at` | explicit pipeline CERI ownership/context | CORRECT | None |
| SetupSignalSnapshot | native `data_as_of_date`, `calculation_cutoff_at` | setup identity and frozen source bundle | CORRECT | None |
| Lifecycle evaluation/transition | `decision_session` / `effective_session` derived from setup `data_as_of_date` | dedicated decision-mutation authority and predecessor chain | DIFFERENT_CONTRACT | None |
| Setup alerts | effective decision/session data in immutable alert evidence | dedicated alert authority and lifecycle/setup evidence | DIFFERENT_CONTRACT | None |
| CERI alerts | CERI change/evidence authority | dedicated CERI alert contract | DIFFERENT_CONTRACT | None |
| Winner predictions | `prediction_as_of_date`, `source_data_cutoff_at` | Winner-specific episode, decision-time, scope and temporal-validity contracts | DIFFERENT_CONTRACT | None |
| IBIntelligenceFeature | native `as_of_session`, `calculation_cutoff_at` | explicit IBMI feature identity and constituent manifest | CORRECT | None |

The count of other downstream paths with the same missing acquisition-visibility propagation is **one**: Sector/ETF rotation. Its persisted snapshot date was already forced to the frozen session, so it did not raise the same exception, but it could silently omit an authorized pipeline-owned benchmark/proxy row. No correct caller was refactored for symmetry.

## 5. Implementation

The production change is limited to two source-loader boundaries:

| File | Function | Change |
| --- | --- | --- |
| `app/services/market_regime_command_center.py` | `_load_bounded_market_frames` | Forward `market_cutoff.context_id` as `calculation_context_id` with the existing max-session and cutoff bounds. |
| `app/services/sector_etf_rotation_service.py` | `_load_preferred_bounded` | Apply the same frozen calculation-context visibility to benchmark and sector-proxy reads. |

Certification changes:

| File | Purpose |
| --- | --- |
| `tests/integration/test_market_regime_temporal_remediation_postgresql.py` | Reproduce the retained mismatch through the real PostgreSQL writer/validator; prove positive context visibility and negative future-session rejection. |
| `tests/test_sector_etf_rotation_service.py` | Assert exact session, cutoff, and context propagation for the sibling loader. |
| `tests/recovery/test_whole_application_recovery.py` | Use the exact live-slice membership for the 10-symbol leg; run real Fundamental, Technical, Market Regime, Combined, Ranking, and Sector stages; retain deterministic provider-bound later hooks and 25/100 Technical checks. |
| `tests/test_t14a_mutation_inventory.py` | Record the intentional Market Regime writer source as a current-release stale historical pin; historical certifications remain unchanged and fail closed. |

No migration, schema change, validator relaxation, timestamp backdating, run-specific special case, or historical evidence mutation was made.

## 6. Tests

| Verification | Result |
| --- | --- |
| Failing-before retained-value PostgreSQL regression | FAIL before fix at the real writer/validator with `MUTATION_ARTIFACT_TEMPORAL_MISMATCH` and artifact session 2026-09-23 |
| Positive temporal control / Market Regime replay | PASS; snapshot/evidence committed at session 2026-09-24 under context 17 |
| Negative temporal control | PASS; future artifact session 2026-09-25 rejected with the same guardrail |
| Sector context propagation | PASS |
| Focused unit suites | 396 passed, 0 failed |
| Focused PostgreSQL authority/temporal/recovery suites | 62 passed, 0 failed, 1 deselected (the separately executed full gate) |
| Real downstream single-run certification | 1 passed, 0 failed in 222.82s |
| Exact 10/25/100 recovery gate | 1 passed, 0 failed in 310.60s; repeated failure recheck also passed |
| One broader suite run | 4,106 passed, 4 failed, 10 skipped in 5,834.04s |
| Failed-node recheck after current-release expectation update | 4 passed, 0 failed in 448.73s |
| Ruff lint / formatting / diff checks on changed files | PASS |

The broader suite was run exactly once. Three failures were the expected fail-closed T14A stale-source set missing the newly changed Market Regime file; the expectation was updated and all three nodes passed. The fourth was the 10/25/100 recovery test only in broad-suite order. That identical gate had passed before the broad run and passed again with the three failed inventory nodes afterward. It is not reproducible standalone; no application exception or failed stage was found. The broad suite was not rerun to manufacture a clean aggregate.

Exact-10 retained membership:

```text
BHE, BLLN, KLIC, LSCC, PDFS, ACMR, RDVT, AVT, DVN, JNJ
```

Exact-10 metrics from the passing isolated gate:

| Metric | Value |
| --- | ---: |
| Pipeline status | COMPLETED |
| Pipeline duration | 29.730124s |
| Technical duration | 13.514067s |
| Visible / scored | 10 / 10 |
| Source-validation queries | 31 |
| Canonical Technical manifest | 1; 693,947 bytes; 7,360 states |
| Maximum checkpoint gap | 6.007053s |
| Lease expired | false |
| Incomplete / warning rows | 0 / 0 |
| Market Regime confidence | normal |

The 100-symbol regression leg completed in 101.828496s; its pure-kernel retry was 47.312065s, 100/100 retry rows remained idempotent, source-validation queries were 203, and the maximum checkpoint gap was 49.736143s.

## 7. Stage replay

The isolated real-writer downstream replay retained production orchestration/database semantics and froze external IB/provider boundaries. Alerts are evaluated inside the Lifecycle stage and therefore share its measured stage duration.

| Stage | Result | Duration | Temporal authority |
| --- | --- | ---: | --- |
| Market Regime | PASS | 1.699s | frozen pipeline context/session/cutoff; context-aware benchmark visibility |
| Combined | PASS | 1.857s | pinned Fundamental + Technical evidence under the frozen context |
| Ranking | PASS | 8.037s | pinned compatible source identities and frozen profiles/context |
| CERI | PASS | 7.708s | pipeline-owned CERI context and immutable source evidence |
| Setup | PASS | 20.536s | frozen decision handoff and setup source bundle |
| Lifecycle | PASS | 10.696s | setup data session plus immutable predecessor/decision authority |
| Alerts | PASS | included in 10.696s | lifecycle/setup decision evidence; dedicated alert authority |
| Winner | PASS | 4.876s | frozen handoff, episode/source cutoff, and Winner scope authority |

The exact-10 gate separately completed every orchestration stage from SEC through Winner. It used real current-head persistence through Sector and deterministic provider-bound CERI/Setup/Lifecycle/Alerts/Winner hooks. The real later-stage writers were exercised by the downstream replay above.

## 8. Remaining findings

No genuine remaining P0 or P1 application blocker was found.

The broad-only recovery test marker is recorded as a test-order/environment observation, not hidden: the identical gate passed twice standalone with complete metrics and no application failure. It does not supply evidence of a Market Regime, downstream writer, Technical, lease, cancellation, or temporal-integrity regression.

## 9. Git and operational state

| Item | State |
| --- | --- |
| Starting HEAD | `33a2d967f2966c334642cb4fcc05a5e78f129a0c` |
| Branch | `codex/market-regime-temporal-remediation` |
| Implementation commit | `49c8b2faad8692e92323b572b436c17e3eb6731a` (`fix: bind downstream market reads to pipeline context`) |
| Final code HEAD before this report-only commit | `49c8b2faad8692e92323b572b436c17e3eb6731a` |
| Migration head / local revision | `0084_technical_recovery` / `0084_technical_recovery` |
| Active SwingLens jobs | 0 |
| Port 8000 listeners | 0 |
| Retained run 164 | `COMPLETED`, 10 rows; unchanged |
| Retained pipeline 155 | `FAILED` at `MARKET_REGIME_SNAPSHOT`; unchanged |
| Retained root job 43319 | `FAILED`, no execution token or worker owner; unchanged |
| Pushed | NO |

The only pre-existing untracked workspace file is `docs/remediation/recovery/WHOLE_APPLICATION_RECOVERY_AUDIT.md`; it was not modified or staged.
