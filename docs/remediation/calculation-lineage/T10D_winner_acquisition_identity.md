# T10D — Winner Acquisition and Source Identity Adoption

## 1. Baselines

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T09A | `2e0487a271d425b30fb61b17a33717562e0abe3b` |
| T09B | `c7b8df7f25fe709b0fa7b20bf2fe3b738afcfb29` |
| T09C | `bbb345b3257d0df572db425242c62ea1447e5d24` |
| T10A | `e08bf42a527c1345883e5587af794a7396443a7e` |
| T10B | `7edef90175de190f4fbfc5ea3fd283ed8c9aa2a9` |
| T10C / T10D starting HEAD | `fc380503508e16a4d58c3ef2a446040e64194d75` |
| Branch | `codex/t10d-winner-acquisition-identity` |
| Final HEAD | The focused T10D commit containing this report; its exact SHA is recorded in the final response because a commit cannot contain its own SHA. |
| Alembic head before/after | `0074_ceri_evidence_quarantine` |
| Python | 3.12.2 from `.venv` |

The branch was created from the exact required T10C base. Before editing there were no
tracked modifications. Pre-existing untracked architecture/audit registry files and IB
historical-probe files were preserved and are not part of T10D.

## 2. Scope

T10D covers Winner prediction acquisition, explicit decision identity, Decision Handoff
validation, independent compatibility of every source Winner reads, durable-job identity
binding, and fail-closed historical/backfill capture.

It does not redesign outcomes, maturation, cohorts, publication, readiness/confidence,
immutable PriceBar evidence, configuration authority, or general replay semantics. It adds
no CERI, SetupSignal, Setup Lifecycle, or direct IBMI dependency to Winner.

## 3. Winner decision identity contract

Canonical capture receives the already frozen `MarketCalculationCutoff` created from the
pipeline's `MarketCalculationContext`, plus the exact Decision Handoff manifest ID when the
pipeline froze one. The capture gate combines that context with the persisted Handoff's run
and pipeline ownership and the raw row's normalized ticker. The resulting T10A identity
requires known run, pipeline, ticker, context ID/fingerprint, as-of session, calculation
cutoff, and calendar identity.

`decision_at` is the frozen cutoff and cannot disagree with it. The pipeline forwards the
same object; the manual durable-job path serializes the context ID, cutoff, session,
calendar, readiness version, pipeline, and Handoff ID, then resolves and revalidates the
persisted context in the handler. It never asks for a latest context after enqueue.

Historical capture must supply the same dimensions per run. The backfill planner classifies
a run without them as `missing_original_decision_identity`; execution resolves the exact
persisted context and passes it to capture. A reconstruction request lacking an explicit
context fails before source loading.

## 4. Compatibility policies

All policies use the shared T10A compatibility validator. No policy permits `UNKNOWN` or
`LEGACY_UNKNOWN` to prove a required dimension.

| Policy | Required compatibility | Intentionally ignored / reuse rule | Mismatch behavior |
|---|---|---|---|
| `WINNER_HANDOFF_COMPATIBILITY` | exact run, pipeline, context ID/fingerprint, session, cutoff, calendar, Handoff contract version, complete manifest fingerprint | ticker/config are N/A at the run-level gate | fail capture before ticker/vector work |
| `WINNER_RAW_COMPATIBILITY` | exact run, ticker, raw artifact ID and semantic hash from Handoff | pipeline/context are proven by Handoff, not present on Raw | fail ticker acquisition |
| `WINNER_FUNDAMENTAL_COMPATIBILITY` | exact run/pipeline/ticker/context/session/cutoff/calendar plus exact Handoff artifact ID/hash | optional under existing Winner semantics | omit source and its feature values |
| `WINNER_TECHNICAL_COMPATIBILITY` | exact run/pipeline/ticker/context/session/cutoff/calendar plus exact Handoff artifact ID/hash | no global reuse | fail ticker acquisition |
| `WINNER_COMBINED_COMPATIBILITY` | exact run/pipeline/ticker/context/session/cutoff/calendar plus exact Handoff artifact ID/hash | no global reuse | fail ticker acquisition |
| `WINNER_RANKING_COMPATIBILITY` | exact run/pipeline/ticker/context/session/cutoff/calendar plus exact Handoff artifact ID/hash | Ranking is optional; profile choice follows compatibility filtering | omit incompatible candidates; deterministic choice among compatible candidates |
| `WINNER_REGIME_COMPATIBILITY` | exact session/cutoff/calendar, current Regime config, calculation/engine versions, and exact Handoff artifact ID/hash | run/pipeline/ticker are intentionally irrelevant; compatible global cross-run reuse is allowed | skip incompatible/latest candidates; omit if none match |
| `WINNER_SECTOR_COMPATIBILITY` | exact run/pipeline/context/session/cutoff/calendar, current Sector config/model/mode, calculation/engine versions, and exact Handoff snapshot/row ID/hash | ticker is represented by the separately matched sector row; optional under existing Winner semantics | omit incompatible Sector evidence |

For contextual producer rows, their embedded T10 identity proves the policy dimensions while
the Handoff's semantic artifact reference proves the exact artifact. This is deliberately
not a transitive shortcut: Fundamental, Technical, Combined, Ranking, Regime, and Sector are
each inspected independently.

## 5. Winner source compatibility matrix

| Producer | Winner use | Required identity dimensions | Same run required? | Cross-run reuse? | Mismatch / unknown | Policy |
|---|---|---|---|---|---|---|
| Raw | subject/company metadata | run, ticker, exact ID/hash | Yes | No | reject | `WINNER_RAW_COMPATIBILITY` |
| Fundamental | score and coverage | decision context spine, exact ID/hash | Yes | No | optional omission | `WINNER_FUNDAMENTAL_COMPATIBILITY` |
| Technical | score, trigger, quality, eligibility | decision context spine, exact ID/hash | Yes | No | reject; compatible insufficiency is still excluded later | `WINNER_TECHNICAL_COMPATIBILITY` |
| Combined | combined/setup/risk features | decision context spine, exact ID/hash | Yes | No | reject | `WINNER_COMBINED_COMPATIBILITY` |
| Ranking | profile/cohort/setup metadata | decision context spine, exact ID/hash | Yes | No | filter first; optional omission | `WINNER_RANKING_COMPATIBILITY` |
| Market Regime | regime/risk context | session, cutoff, calendar, config, versions, exact ID/hash | No | Yes | skip/omit | `WINNER_REGIME_COMPATIBILITY` |
| Sector Rotation | sector state/rank | run/pipeline/context/session/cutoff/calendar, config/model/mode, versions, exact snapshot/row | Yes | No | optional omission | `WINNER_SECTOR_COMPATIBILITY` |
| Decision Handoff | decision and expected artifact set | run, pipeline, context, session, cutoff, calendar, contract, full fingerprint | Yes | No | reject capture | `WINNER_HANDOFF_COMPATIBILITY` |

## 6. Decision Handoff validation

Before T10D the manifest was **RECORDED** in Winner lineage. After T10D it is
**RECORDED + VALIDATED + ENFORCED** before acquisition. Validation recomputes the complete
manifest fingerprint, checks the contract, persisted/payload run and pipeline, context ID,
run-start anchor, cutoff, completed session, timezone, calendar/readiness versions, and the
per-ticker artifact set.

The Handoff is authoritative for the artifact set it enumerates, but it is not treated as a
standalone proof of every producer's intrinsic compatibility. Winner also validates each
embedded source identity. Therefore this report does not classify the Handoff alone as
fully `AUTHORITATIVE` for Winner compatibility.

## 7. Wall-clock removal

| Use | Classification after T10D |
|---|---|
| real Winner capture decision/session | removed; must equal the explicit frozen cutoff |
| historical/backfill decision/session | removed; missing original identity fails closed |
| feature extractor default clock | not used by identity-enforced capture; the explicit decision is always passed |
| compatibility fallback for non-SQL test doubles | current-mode test seam only; unreachable by a real SQLAlchemy `Session` |
| prediction `captured_at`, processing/job timestamps, elapsed timing | retained as operational timestamps |
| outcome/cohort/publication clocks | out of T10D scope |

## 8. Historical/backfill behavior

Each requested run needs an explicit mapping containing pipeline ID, Handoff ID, persisted
MarketCalculationContext ID, cutoff, decision session, calendar version, and bar-readiness
version. The mapping is checked against the persisted context. Missing, incomplete,
timezone-naive, or mismatched identity is skipped by planning or rejected by execution; the
current clock is never substituted. T10D does not claim to reconstruct identities that were
never recorded.

## 9. Pipeline propagation

```text
MarketCalculationContext
  -> frozen MarketCalculationCutoff
  -> Decision Handoff (exact context + artifact set)
  -> pipeline call or durable job payload (exact context/Handoff IDs)
  -> Winner Handoff/source acquisition gate
  -> Winner prediction identity + frozen feature vector
```

The pipeline passes its frozen context object and the manifest ID produced by its Handoff
step. The durable handler resolves the referenced context rather than selecting a latest
row. Capture validates Handoff before iterating tickers and validates sources before feature
extraction and hashing.

## 10. Legacy handling

Missing/unknown/legacy Handoffs cannot authorize new canonical capture. An existing Winner
prediction without a T10 calculation identity is left unchanged and cannot be silently
upgraded, reinterpreted, or assigned fabricated lineage by an identity-aware duplicate
capture. No historical vectors are rewritten or backfilled.

## 11. Vector immutability

`feature_json` continues to be frozen by value and `feature_vector_hash` is still computed
from that canonical vector. T10D adds validation before those operations and embeds the
accepted source references plus vector hash into existing `lineage_json`. Rescore/read paths
remain unchanged and continue to use the frozen prediction rather than current producer
tables. Rejecting optional Fundamental evidence now also suppresses the copied Fundamental
value in Combined so an unvalidated source cannot re-enter the vector indirectly.

## 12. Findings

| Finding | Status | Basis |
|---|---|---|
| `WIN-001` | **CLOSED** | canonical and historical business time is explicit and frozen; historical missing identity fails closed |
| `WIN-002` | **CLOSED** | Handoff fingerprint, decision context, ownership, anchor, and artifact membership are runtime gates |
| `WIN-003` | **PARTIALLY_CLOSED** | Ranking identity and compatibility-before-priority selection are fixed; Ranking readiness policy remains excluded |
| `WIN-005` | **PARTIALLY_CLOSED** | Regime/Sector identity selection is fixed; broader semantic/readiness policy remains excluded |
| `XINT-001` | **PARTIALLY_CLOSED** | the full canonical path through Winner now uses calculation identity; repository-wide certification remains T10E |
| `INV-IDENTITY-001` repository-wide | **PARTIALLY_CLOSED** | all Winner acquisition edges enforce it, but repository-wide closure requires the final Phase-1 certification |

## 13. Tests

| Critical proof | Focused coverage |
|---|---|
| canonical compatible capture and exact source IDs | `test_canonical_capture_validates_sources_then_freezes_identity` |
| same-run wrong Technical/Combined/Ranking identity | `test_same_run_ticker_wrong_source_identity_never_drives_winner` |
| Fundamental omission and Raw rejection | `test_fundamental_identity_mismatch_is_optional_omission`, `test_raw_row_mismatch_fails_before_vector_freeze` |
| global Regime reuse/newer incompatible skip | `test_compatible_cross_run_regime_is_accepted_after_newer_incompatible_candidate` |
| Sector omission | `test_incompatible_sector_is_omitted_from_frozen_vector` |
| Ranking filter before priority | `test_ranking_filters_identity_before_priority_selection` |
| exact Handoff and one-field mismatches | canonical test plus `test_handoff_mismatch_fails_closed` |
| missing/legacy Handoff and historical missing identity | `test_missing_handoff_and_historical_capture_without_identity_fail_closed` |
| wall-clock invariance/current pipeline context | `test_historical_business_identity_is_wall_clock_invariant`, `test_execute_full_pipeline_propagates_winner_control_and_progress_contract` |
| upstream mutation cannot change frozen vector; legacy non-upgrade | `test_historical_source_mutation_does_not_change_existing_snapshot_hash`, `test_legacy_prediction_is_not_silently_upgraded_or_rewritten` |
| compatible Technical insufficiency | `test_identity_compatible_technical_insufficiency_remains_excluded` |
| no CERI/Setup/Lifecycle/direct IBMI edges | `test_no_negative_winner_edges_were_introduced` |

## 14. Verification

| Lane | Exact command/scope | Result |
|---|---|---|
| T10D focused | `.venv\Scripts\python.exe -m pytest tests/winner_probability/test_winner_calculation_identity_adoption.py -q` | **18 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Identity infrastructure (T10A–T10D) | five explicit T10 identity test files | **98 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Winner subsystem | `tests/winner_probability -m "not external and not e2e and not slow" -q` | **301 passed, 0 failed, 0 skipped, 1 deselected, 1 warning** |
| Affected upstream | 24 explicit Fundamental, Technical, Ranking, Regime, Sector, T10B/T10C, and pipeline files | **247 passed, 0 failed, 0 skipped, 1 deselected, 1 warning** |
| Phase-0 regressions | nine explicit T09A temporal, T09B fence, and T09C publication/reliability files | **97 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Final affected regression | T10D, pipeline, Winner job handler, and admin route files | **69 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Broader clean lane | `tests` with the exact ignores/markers in §15 | **2,504 passed, 0 failed, 0 skipped, 33 deselected, 22 warnings** |

Ruff passed for all changed Python files. `compileall` passed for changed service/test scope,
and `git diff --check` passed apart from informational Git line-ending notices. The repeated
focused warning is the existing Starlette/httpx deprecation; 21 additional broad-lane
warnings are the existing Python 3.12 SQLite datetime-adapter deprecation.

## 15. Intentional exclusions

- Live external/provider: `tests/ib_market_intelligence/test_external_smoke.py`, marker
  `external`, IBKR/IB Gateway, SEC/provider/network/credential tests. No provider was called.
- Browser/E2E: `tests/e2e/`, markers `e2e` and `slow`, Playwright/Selenium/browser flows.
- PostgreSQL-dependent: `tests/integration/`, `tests/test_migration_remediation.py`, and
  `tests/qa/test_qa_infrastructure.py`. No PostgreSQL service was started or contacted.

The exact broader command was:

```text
.venv\Scripts\python.exe -m pytest tests --ignore=tests/e2e --ignore=tests/integration --ignore=tests/ib_market_intelligence/test_external_smoke.py --ignore=tests/test_migration_remediation.py --ignore=tests/qa/test_qa_infrastructure.py -m "not external and not e2e and not slow" -q
```

## 16. Schema/data impact

```text
migration required: NO
production rewrite required: NO
legacy identity backfill required: NO
```

Winner identity and accepted source references fit in the existing prediction
`lineage_json`. No migration ran and no production/runtime data was mutated.

## 17. Residual Phase-1 identity risks

T10E still needs repository-wide Phase-1 certification, including evidence that no other
major persisted calculation boundary substitutes ownership identity for calculation
identity. Legacy Handoffs/predictions remain explicitly unprovable, and the intentionally
excluded PostgreSQL integration lane still needs the dedicated integration-certification
environment. Ranking/readiness, Regime/Sector semantic policy, configuration authority,
and full historical replay remain their already assigned later phases.

## 18. Final verdict

```text
WINNER DECISION IDENTITY: PASS
HANDOFF VALIDATION: PASS
WINNER SOURCE IDENTITY COMPATIBILITY: PASS
HISTORICAL WINNER CAPTURE IDENTITY: PASS
WINNER VECTOR IMMUTABILITY PRESERVED: PASS

T10D OVERALL: PASS
```
