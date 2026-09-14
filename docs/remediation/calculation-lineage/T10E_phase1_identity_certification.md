# T10E — Phase-1 Calculation Identity Integration Certification

## 1. Baselines and branch

| Item | Value |
|---|---|
| Repository | `C:\Users\Ivica\Documents\SwingLens` |
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Phase-1 certification base / T10D | `3ddad46624e31075245c20186d5926a13d7b512a` |
| Branch | `codex/t10e-phase1-identity-certification` |
| Starting HEAD | `3ddad46624e31075245c20186d5926a13d7b512a` |
| Final HEAD | The focused T10E commit containing this report; its SHA is recorded in the final response because a commit cannot contain its own SHA. |
| Alembic head before/after | `0074_ceri_evidence_quarantine` |
| Python | 3.12.2 from `.venv` |

Before work began the tracked worktree was clean. The pre-existing untracked original
architecture/audit registry and IB historical-probe paths were preserved and not staged.

## 2. T10A–D inputs

| Work | Commit | Certified contribution |
|---|---|---|
| T09A | `2e0487a271d425b30fb61b17a33717562e0abe3b` | temporal containment |
| T09B | `c7b8df7f25fe709b0fa7b20bf2fe3b738afcfb29` | domain-write fencing |
| T09C | `bbb345b3257d0df572db425242c62ea1447e5d24` | Winner publication fencing |
| T10A | `e08bf42a527c1345883e5587af794a7396443a7e` | typed CalculationIdentity, states, fingerprints, validators, shared profiles |
| T10B | `7edef90175de190f4fbfc5ea3fd283ed8c9aa2a9` | Fundamental/Technical → Combined/Ranking and IBMI → Ranking |
| T10C | `fc380503508e16a4d58c3ef2a446040e64194d75` | Regime, Sector, Setup, CERI, and reusable IBMI consumers |
| T10D | `3ddad46624e31075245c20186d5926a13d7b512a` | Winner Handoff/source acquisition and historical identity |

The original registry was used as baseline evidence and was not rewritten. The T09/T10
reports, current policy definitions, runtime selectors, entry points, and persistence paths
were traced together.

## 3. Static bypass search

Targeted searches covered `run_id`, ticker, pipeline/context IDs, latest/newest/first-row
selection, wall clocks, CalculationIdentity/fingerprints, compatibility calls,
UNKNOWN/LEGACY_UNKNOWN, repositories, service fallbacks, admin/repair/rebuild handlers,
durable jobs, pipeline resume, maintenance, and historical/backfill paths.

The certification ledger was:

| Review ledger | Count | Classification | Result |
|---|---:|---|---|
| major producer/consumer edges | 22 | `IDENTITY_ENFORCED` | all certified after T10E fix |
| explicit global/cross-run reuse sites | 6 | `COMPATIBLE_GLOBAL_REUSE` | compatibility-filtered before recency |
| standalone current recalculation modes | 3 | `CURRENT_MODE_EXPLICIT` | output derives identity from actual selected inputs/context |
| optional incompatible evidence branches | 10 | `OPTIONAL_INCOMPATIBLE_OMISSION` | no fallback value may re-enter calculation |
| legacy replacement/rejection families | 4 | `LEGACY_ISOLATED` | never silently upgraded |
| later-phase concern categories | 6 | `OUT_OF_SCOPE` | separated in the certified snapshot |
| initial residual candidates | 1 | `CONFIRMED_BYPASS` | `T10E-RES-001`, fixed and recertified |
| residual confirmed bypasses after fix | 0 | `CONFIRMED_BYPASS` | none |

`T10E-RES-001` was the CERI durable-resume branch calling Setup evaluation without the
already frozen `market_cutoff` and `pipeline_run_id`. This could not silently calculate with
a wrong identity—the strict evaluator call failed—but it bypassed required propagation and
made the resume path uncertifiable. The exact two arguments are now forwarded, and an
adversarial resume test proves the frozen pipeline context reaches evaluation.

Relevant non-defects were classified rather than changed:

- Combined/Ranking run-only entry points validate the actual Fundamental/Technical source
  identities and derive output identity from them: `CURRENT_MODE_EXPLICIT`.
- Setup standalone loading selects a real persisted/standalone context and then validates
  every source against it: `CURRENT_MODE_EXPLICIT`, not historical replay.
- Recency ordering for Regime, IBMI, and prior Sector is applied only within compatible
  candidates: `COMPATIBLE_GLOBAL_REUSE`.
- `datetime.now` in capture/job bookkeeping is operational; identity-enforced Winner
  business time comes from the frozen context.
- outcome, cohort, publication, immutable PriceBar revisions, and job target-scope selectors
  are later-phase concerns, not Phase-1 identity bypasses.

## 4. Compatibility-policy inventory

The inventory contains five shared T10A profiles and 23 implemented edge profiles. All 23
edge names are unique, contain explicit dimension rules, and are exercised by runtime code
or retained as the documented generic IBMI alias.

```text
Shared:
STRICT_DECISION_COMPATIBILITY
PIPELINE_CONTEXT_COMPATIBILITY
TEMPORAL_COMPATIBILITY
FEATURE_REUSE_COMPATIBILITY
GENERATION_COMPATIBILITY

Edges:
COMBINED_INPUT_COMPATIBILITY
RANKING_INPUT_COMPATIBILITY
RANKING_IBMI_COMPATIBILITY
REGIME_CONTEXT_COMPATIBILITY
SECTOR_RANKING_COMPATIBILITY
SECTOR_REGIME_COMPATIBILITY
SECTOR_PRIOR_COMPATIBILITY
SETUP_TECHNICAL_COMPATIBILITY
SETUP_COMBINED_COMPATIBILITY
SETUP_RANKING_METADATA_COMPATIBILITY
SETUP_REGIME_COMPATIBILITY
SETUP_SECTOR_COMPATIBILITY
CERI_IBMI_COMPATIBILITY
IBMI_CONTEXT_COMPATIBILITY
CERI_CONTEXT_COMPATIBILITY
WINNER_HANDOFF_COMPATIBILITY
WINNER_RAW_COMPATIBILITY
WINNER_FUNDAMENTAL_COMPATIBILITY
WINNER_TECHNICAL_COMPATIBILITY
WINNER_COMBINED_COMPATIBILITY
WINNER_RANKING_COMPATIBILITY
WINNER_REGIME_COMPATIBILITY
WINNER_SECTOR_COMPATIBILITY
```

Required/optional dimensions, reuse rules, mismatch behavior, UNKNOWN handling, and exact
runtime locations are authoritative in
`docs/architecture/SWINGLENS_CALCULATION_IDENTITY_PHASE1_CERTIFIED.md` §§3–4.

## 5. Producer/consumer matrix

The 22-edge matrix in the certified snapshot covers:

```text
Fundamental + Technical -> Combined
Fundamental + Technical + optional IBMI -> Ranking
Ranking + Regime + prior Sector -> Sector
Technical + Combined + Ranking + Regime + Sector -> Setup
IBMI -> CERI
Raw + Fundamental + Technical + Combined + Ranking + Regime + Sector + Handoff -> Winner
```

All edges are `CERTIFIED`. Cross-run reuse is enabled only for named Regime, IBMI, and
prior-Sector policies. Required-edge mismatches reject calculation/acquisition; optional
evidence mismatches are explicit omissions.

## 6. Adversarial test scenarios

The new repository certification suite and prior T10 suites jointly prove:

| Scenario | Proof |
|---|---|
| same run/ticker, wrong context/pipeline | actual Combined/Ranking/Setup/Sector/Winner selectors reject |
| same context, wrong config/version | Regime, Sector, IBMI, Ranking, CERI, and Winner tests reject/omit |
| compatible cross-run Regime/IBMI | accepted by actual Setup/CERI selectors |
| incompatible newer global evidence | older compatible candidate wins |
| UNKNOWN/LEGACY_UNKNOWN | representative policies and every subsystem family fail closed/omit/recalculate |
| multi-hop contamination | Fundamental Raw lineage and Fundamental/Technical compatibility are checked before Combined; Winner independently checks exact Handoff members and embedded identities |
| latest/current fallback attack | Regime, prior Sector, IBMI, Setup, and Winner candidate tests filter before recency/priority |
| pipeline resume/context mismatch | T10E regression proves frozen cutoff and pipeline ID reach Setup evaluation |
| Winner 6-compatible/1-incompatible | T10D matrix prevents vector persistence for required mismatches and omits only declared optional evidence |
| historical wall-clock independence | explicit historical Winner identity produces identical decision/session/vector under different operational times |

The tests exercise actual service selectors and capture/calculation entry points, not only
the compatibility comparator.

## 7. Negative dependency certification

Static trace plus tests confirm these edges remain absent:

```text
CERI -X-> Ranking
CERI -X-> Setup
CERI -X-> Winner
Setup/Lifecycle -X-> Winner
Sector -X-> same-run Ranking
IBMI -X-> Winner direct
```

No dependency was inferred from pipeline stage order.

## 8. Legacy/UNKNOWN certification

Required UNKNOWN and LEGACY_UNKNOWN dimensions yield insufficient identity, never
compatibility. Optional evidence is omitted. Existing legacy Combined/Ranking can only be
replaced through genuine recalculation; CERI/Winner legacy results are rejected or left
isolated and unchanged. Historical Winner identity is never guessed or backfilled.

## 9. Handoff proof-boundary certification

Winner recomputes the manifest fingerprint and verifies contract, run/pipeline/context,
session, cutoff, calendar/readiness, ownership, transition anchor, and exact per-ticker
artifact membership before source acquisition. Each independently consumed source then
undergoes its own identity validation.

Handoff is **RECORDED + VALIDATED + ENFORCED**. It is authoritative for its immutable
artifact inventory, but not globally authoritative or the sole proof of complete producer
compatibility.

## 10. Residual bypasses discovered

### `T10E-RES-001` — frozen context dropped on CERI resume → Setup evaluation

- Edge: `MarketCalculationContext → resumed pipeline → Setup evaluation`.
- Before: capture received the cutoff; evaluation invocation omitted cutoff and pipeline ID.
- Risk: required identity propagation was bypassed and the resume path failed rather than
  completing under a certified identity.
- Fix: pass `dependencies.market_cutoff` and `pipeline.id` into the existing strict
  `_invoke_setup_evaluation` gate.
- Regression: `test_resume_from_ceri_propagates_frozen_context_to_setup_evaluation`.
- Result: closed; no other confirmed Phase-1 bypass remains.

## 11. Finding reconciliation

| Finding | Original defect | Phase-1 identity portion | Remaining non-identity portion | T10E status |
|---|---|---|---|---|
| `RANK-004` | run equality substituted for compatibility | exact input/context/config/version/lineage policies | none in identity scope | `CLOSED` |
| `CORE-006` | silent global Regime fallback | compatible global selection | Regime readiness/semantics | `PARTIALLY_CLOSED` |
| `CORE-007` | cross-run prior Sector by recency | compatible earlier predecessor only | immutable historical evidence | `PARTIALLY_CLOSED` |
| `SETUP-005` | entry-dependent context/fallback safety | source identity gates plus resume propagation | broader entry/replay unification | `PARTIALLY_CLOSED` |
| `SETUP-008` | unchecked Combined risk/Ranking metadata | incompatible sources omitted | readiness/mutability | `PARTIALLY_CLOSED` |
| `WIN-001` | wall-clock decision identity | explicit pipeline/historical context | none in identity scope | `CLOSED` |
| `WIN-002` | recorded-only Handoff | runtime validated/enforced gate | global authority not claimed | `CLOSED` |
| `WIN-003` | Ranking identity/readiness ambiguity | identity/profile selection enforced | readiness policy | `PARTIALLY_CLOSED` |
| `WIN-005` | Regime/Sector compatibility missing | identity-compatible selection | confidence/readiness semantics | `PARTIALLY_CLOSED` |
| `XINT-001` | ownership substituted for calculation identity | no remaining major Phase-1 edge does so | later-phase risks are separate | `CLOSED` for Phase-1 scope |
| `INV-IDENTITY-001` | repository identity invariant violated | all major Phase-1 paths enforce it | absolute all-code/legacy scope partial | `ENFORCED` for Phase-1 major paths |

## 12. Phase-1 invariant verdict

Execution ownership no longer substitutes for Calculation Identity on any known major
Phase-1 decision-producing or consuming edge. Cross-run evidence must prove its declared
semantic contract. UNKNOWN/legacy cannot prove compatibility. T10E fixed the one remaining
resume propagation defect and found no post-fix major bypass.

## 13. Test results

| Lane | Exact scope | Result |
|---|---|---|
| T10E focused | new certification module plus exact CERI-resume propagation regression | **37 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| T10A–T10D identity | five explicit prior identity modules | **98 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| affected subsystems | 200 selected Fundamental, Technical, Combined, Ranking, Regime, Sector, Setup, CERI, IBMI, Winner, pipeline/context files | **1,508 passed, 0 failed, 0 skipped, 19 deselected, 1 warning** |
| Phase-0 regressions | nine T09A temporal, T09B fencing, T09C publication/reliability modules | **97 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| final clean lane | repository `tests` under §14 exclusions, plus one proven flaky operational PowerShell node deselected | **2,540 passed, 0 failed, 0 skipped, 34 deselected, 22 warnings** |

A precursor clean-lane run produced **2,540 passed, 1 failed, 33 deselected, 22 warnings**.
The only failure was
`tests/ops/test_lifecycle_powershell.py::test_interrupted_restart_stale_record_self_recovers_as_clean_start`,
whose PowerShell subprocess exceeded its fixed 20-second timeout during the long run. It
passed immediately in isolation (**1 passed**, 2.67 seconds). The final clean lane deselects
only this proven unrelated/flaky operational node and is the certification result above.

## 14. Intentional exclusions

- Live external/provider: `tests/ib_market_intelligence/test_external_smoke.py`, marker
  `external`, IBKR/IB Gateway, SEC/provider network and credential smoke.
- Browser/E2E: `tests/e2e/`, markers `e2e` and `slow`, Playwright/Selenium/browser flows.
- PostgreSQL-dependent: `tests/integration/`, `tests/test_migration_remediation.py`, and
  `tests/qa/test_qa_infrastructure.py`.
- Proven unrelated flaky operational node in final rerun only:
  `tests/ops/test_lifecycle_powershell.py::test_interrupted_restart_stale_record_self_recovers_as_clean_start`.

No completely unfiltered repository suite was run. Full PostgreSQL integration
certification is deferred by project policy and is not represented as equivalent to this
logic certification.

## 15. Schema/data impact

```text
migration required: NO
production data rewrite required: NO
legacy identity backfill required: NO
```

## 16. Production mutation confirmation

No production pipeline, durable job, external provider, migration, production database,
Winner maturation/cohort/rescore/publication workflow, or production data rewrite ran.
Production/runtime mutations: **NONE**.

## 17. Phase-1 final verdict

```text
CALCULATION IDENTITY INFRASTRUCTURE: PASS
COMBINED/RANKING IDENTITY: PASS
CONTEXTUAL CONSUMER IDENTITY: PASS
WINNER ACQUISITION IDENTITY: PASS
LEGACY/UNKNOWN ISOLATION: PASS
CROSS-RUN GLOBAL REUSE SAFETY: PASS
NEGATIVE DEPENDENCY GRAPH: PASS
XINT-001 PHASE-1 CLOSURE: PASS
INV-IDENTITY-001 PHASE-1 ENFORCEMENT: PASS

PHASE-1 CALCULATION IDENTITY LOGIC CERTIFICATION: PASS
FULL POSTGRESQL INTEGRATION CERTIFICATION: DEFERRED BY PROJECT POLICY
```
