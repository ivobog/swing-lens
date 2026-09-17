# T12E — Phase-3 Readiness / Eligibility Integration Certification

## 1. Executive verdict

**T12E VERDICT: PASS. Implementation certified: YES. PostgreSQL runtime
certified: YES. Overall certified: YES.** All thirteen required lanes pass.
T12E found and fixed material readiness defects:
sparse native-NORMAL Regime classification could authorize use, Fundamental and
Combined had uncovered consumers, and five material Sector dependencies lacked
permission fences. Existing scoring formulas and native thresholds are preserved.
The final graph contains 23 named edges and two frozen propagation edges.

The certificate applies to supported producer/consumer services and immutable
evidence, not arbitrary forged DTOs or privileged SQL. No Phase-4 work, production
rewrite, legacy backfill or migration is included. The final commit is the commit
containing this report; the exact delivery SHA is recorded in the final response.

## 2. Git baseline and ancestry

Repository: `C:\Users\Ivica\Documents\SwingLens`. Starting branch:
`codex/t12d-winner-readiness-enforcement`. Starting HEAD:
`3dfac7a62f9701875705f3483afd00cbea5ea075`. Tracked and untracked worktree state was
clean before T12E modifications. Branch created:
`codex/t12e-phase3-readiness-certification`.

| Reference | Exact SHA |
|---|---|
| Original audited baseline | 3a9d47063be996908b7d5d1cc5769e0bbd033546 |
| Phase-2 certified baseline | 7b015124807b8f895911d7e790fcbf01949ff85f |
| Local main, recorded origin/main | febe376be67f01589199cd3ba55af98fd54001ed |

`git merge-base --is-ancestor` returned success for the original audited baseline,
Phase-2 and every Phase-3 chain commit against starting HEAD. No history rewrite,
fetch, merge or push was performed. Python: CPython 3.12.2 in repository `.venv`.
Docker was available; disposable PostgreSQL capability was established using a
task-owned PostgreSQL 16.13 container on loopback port 55434. Single Alembic head:
`0079_setup_lifecycle_alert_ev`. Monitoring containers and two unrelated performance
worktrees were preserved. The temporary baseline checkout is task-owned and is
removed after parity verification.

## 3. Phase-3 implementation chain

| Task | SHA | Scope |
|---|---|---|
| T12A | 4c8eb3402b951482d590890e726195a3c445d786 | Typed readiness, eligibility and frozen provenance |
| T12B | b5af43ca8690cb65980be79c4a453ee1d4f7739e | Technical → Combined/Ranking/Setup, actionability propagation |
| T12C | 40f7f1bfa54fe2914f1f9f73ffb8c84026fedf01 | IBMI → Ranking/CERI; Regime/Sector → Setup |
| T12D | 3dfac7a62f9701875705f3483afd00cbea5ea075 | Technical/Ranking/Regime/Sector → Winner |
| T12E | Commit containing this report | Whole-graph certification and narrowly required defect fixes |

All authoritative Phase-1/2 certificates, the original registry, synthesis and
T12A–D reports remain audit references. T12E does not rewrite the original registry
to conceal its audited defects.

## 4. Certified readiness contract

Seven producer states and three consumer decisions remain typed, distinct from
Lifecycle trading states. Only ELIGIBLE permits consumption. DEGRADED, UNKNOWN
and LEGACY_UNKNOWN abstain; ERROR, STALE, INSUFFICIENT_EVIDENCE or any blocking
reason reject permission. Missing decisions do not default to ELIGIBLE.

The writer freezes readiness before payload/evidence hashing. Canonical readiness
identity binds producer, Calculation Identity fingerprint, status, typed reasons,
versions, native metrics and recorded business anchors. Database evidence ID is
excluded from that canonical identity to avoid circular hashing, then exposed in
the addressed DTO and consumer decision. The hash does not attest unrecorded raw
facts, complete replay, physical database identity or semantic truth of a forged
producer result. Native metric units and decimal values are retained.

## 5. Policy inventory

The canonical inventory is the 23-row matrix in
[the certified architecture](../../architecture/SWINGLENS_READINESS_PHASE3_CERTIFIED.md#4-certified-policy-matrix).
All policy IDs are unique. There are 12 current v1 and 11 current v2 versions.
The original five context/compound v1 versions superseded prospectively are
Regime/Sector → Setup and Ranking/Regime/Sector → Winner. Their frozen historical
decisions remain valid records, without reinterpretation.

Eleven v2 policies require the corresponding Regime/Combined/Ranking/Sector v2
producer envelope. An old v1 READY envelope cannot pass a new corrected consumer:
POLICY_UNDECIDED / PRODUCER_READINESS_VERSION_UNSUPPORTED. Otherwise a new mapping
alone would leave previously misclassified or unfenced v1 evidence actionable.
Fundamental and Technical retain existing native readiness contracts and v1
consumer versions; no coverage or confidence threshold is invented.

## 6. Policy-state matrix

All 23 policies have the canonical sequence
`ELIGIBLE / POLICY_UNDECIDED / INELIGIBLE / INELIGIBLE / INELIGIBLE /
POLICY_UNDECIDED / POLICY_UNDECIDED` for READY, DEGRADED, INSUFFICIENT_EVIDENCE,
STALE, ERROR, UNKNOWN and LEGACY_UNKNOWN. READY requires a matching producer
version and exact source binding. The matrix executes 161 cases. An AST inventory
check compares every declared policy constructor with the canonical inventory;
the same test verifies every documented architecture row against actual policy
evaluation. There is no undocumented degraded override or identity collision.

### Complete certification matrix

| Producer | Consumer | Policy | Producer readiness | Consumer eligibility | Ineligible treatment | Immutable decision stored | READY parity | Runtime certified | Status |
|---|---|---|---|---|---|---|---|---|---|
| Technical | Combined | technical-to-combined-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Core frozen debug/evidence payload | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Technical | Ranking | technical-to-ranking-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Core frozen debug/evidence payload | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Technical | Setup | technical-to-setup-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Setup frozen source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| IBMI liquidity | Ranking | ibmi-liquidity-to-ranking-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Core frozen debug/evidence payload | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| IBMI volatility | CERI | ibmi-volatility-to-ceri-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: CERI frozen evidence/source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| IBMI short pressure | CERI | ibmi-short-pressure-to-ceri-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: CERI frozen evidence/source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Regime | Setup | regime-to-setup-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Setup frozen source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Sector | Setup | sector-to-setup-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Setup frozen source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Technical | Winner | technical-to-winner-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Atomic capture rejection | Yes: Winner reserved lineage + feature JSON | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Ranking | Winner | ranking-to-winner-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Atomic capture rejection | Yes: Winner reserved lineage + feature JSON | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Regime | Winner | regime-to-winner-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Winner reserved lineage + feature JSON | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Sector | Winner | sector-to-winner-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Winner reserved lineage + feature JSON | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Fundamental | Combined | fundamental-to-combined-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Core frozen debug/evidence payload | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Fundamental | Ranking | fundamental-to-ranking-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Core frozen debug/evidence payload | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Fundamental | Setup | fundamental-to-setup-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Setup frozen source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Combined | Setup | combined-to-setup-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Setup frozen source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Fundamental | Winner | fundamental-to-winner-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Winner reserved lineage + feature JSON | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Combined | Winner | combined-to-winner-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Atomic capture rejection | Yes: Winner reserved lineage + feature JSON | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Technical | Sector | technical-to-sector-v1 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Sector frozen payload/source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Combined | Sector | combined-to-sector-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Sector frozen payload/source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Ranking | Sector | ranking-to-sector-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Sector frozen payload/source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Regime | Sector | regime-to-sector-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Sector frozen payload/source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |
| Prior Sector | Sector | prior-sector-to-sector-v2 | Seven states; exact/version-bound | E/U/I/I/I/U/U | Omit; native missing-input path | Yes: Sector frozen payload/source lineage | PASS | PASS: numeric + native evidence lane | CERTIFIED |

In this table, READY means a matching, correctly bound producer envelope. The
state mapping is the complete seven-state mapping above. Runtime certification
combines actual numeric stage tests with native PostgreSQL evidence/pin/history
tests; it does not claim every Cartesian state permutation ran in PostgreSQL.
READY parity includes exact business captures and the prior Sector math case.

## 7. Consumer graph certification

The original twelve edges are retained. Eleven missed material edges are added:
Fundamental → Combined, Ranking, Setup, Winner; Combined → Setup, Winner, Sector;
Technical → Sector; Ranking → Sector; Regime → Sector; Prior Sector → Sector.

Evidence for materiality: Fundamental feeds weighted components and Setup
liquidity; Combined feeds Setup earnings risk and Winner features; Technical,
Combined and Ranking feed Sector scores, candidate counts and population; Regime
feeds Sector posture; prior Sector feeds rank/score changes. Each boundary now
projects eligible frozen values or omits/rejects before native math. Omitted
considered-source decisions remain in immutable ledgers. Two downstream
propagation edges retain frozen Setup permission: Setup → Lifecycle → Alerts.

## 8. Negative graph certification

All nine forbidden dependencies remain absent: CERI → Ranking/Setup/Lifecycle/
Winner; Setup → Winner; Lifecycle → Winner; Alert → Winner; IBMI → Winner direct;
Sector → same-run Ranking. The Phase-1 negative dependency AST regression and
Winner injection/source-selection tests pass. Test orchestration across several
subsystems does not introduce an application dependency between them.

## 9. Producer-gap adjudication

| Producer | Known gap | Incorrect READY possible now? | Affected consumers | Consumer can detect? | Material risk? | Phase-3 blocker? | Disposition |
|---|---|---|---|---|---|---|---|
| Regime | Sparse native-NORMAL benchmark | No under corrected contract; old v1 cannot authorize new consumers | Setup, Winner, Sector | v2 binds native history/missing flags; missing proof abstains | Corrected | No remaining blocker | Existing feature flag overrides readiness; scoring retained |
| Regime | Stale bullish diagnostic gate/sizing | No: native stale warning maps STALE | Setup, Winner, Sector | Yes, typed STALE despite diagnostic values | Blocked | No | Diagnostic gap retained separately |
| Fundamental | Numeric remnants with sparse/missing/unknown quality | No native-known sparse state passes the declared contract | Combined, Ranking, Setup, Winner | Existing flags/penalties → DEGRADED; UNKNOWN/legacy/error abstain/block | Corrected at consumer boundaries | No remaining blocker | All four material consumers fenced; Winner nullable |
| Combined | Completeness-only gate misses broader states | Old v1 cannot authorize corrected consumers; complete non-ready v2 is rejected | Setup, Winner, Sector | Exact frozen envelope; native completeness also retained | Corrected | No remaining blocker | Typed mandatory Winner gate plus optional downstream omission |
| CERI | Own event-risk stale penalty disconnected | Correct native stale envelope is non-ready even if risk diagnostics omit penalty | No downstream CERI decision edge in this graph | Producer freshness ledger blocks; no claimed correction to penalty math | No Phase-3 propagation | No | CERI-007 remains PARTIAL/OPEN for producer math |

Residual producer-gap count is **2 diagnostic gaps**, not four unresolved
material blockers. Incorrect actionable READY producer-gap count is **0** within
the declared native contract and supported services. Full producer sufficiency
redesign and configuration authority are not inferred from that statement.

Native Regime tests use the actual feature engine and classifier with 2, 25, 100
and 400 benchmark bars. All reproduce native NORMAL confidence; insufficient
history becomes typed INSUFFICIENT_EVIDENCE with REGIME_INSUFFICIENT_PRIMARY.
The 400-bar sufficient case remains READY. Active sparse/missing proxy blocks;
missing primary/proxy proof abstains; disabled proxy does not add a dependency.

## 10. Fundamental/Combined Winner adjudication

Both uncovered edges were material; T12D's limitations could not merely be
deferred. Fundamental score and coverage are nullable in the existing feature
registry. New typed permission omits them together, preserving nulls and avoiding
copied Combined/Fundamental denormalization as a fallback. Diagnostic columns may
remain populated without re-entering feature JSON.

Fundamental's native coverage service derives a 0–10 score, missing-field
penalties, and its existing configured sparse-coverage warning. Native sparse,
missing and V2 warnings degrade readiness. The real golden pipeline now correctly
omits a native-degraded Fundamental result rather than manufacturing READY.
This verifies existing native warning authority, not a newly selected threshold.

Combined completeness alone cannot distinguish a complete-but-ERROR/UNKNOWN/
DEGRADED artifact. Combined → Winner now requires exact typed permission and v2
producer contract as a mandatory source. Rejection occurs before any Winner
record creation; native completeness remains an additional existing gate.

## 11. End-to-end Technical attack

The evolving fixture uses actual Combined and Ranking engines, converts their
DTOs to frozen consumer-source models, builds Setup from the same Raw/Technical/
Fundamental/Combined/Regime/Sector world, runs Lifecycle/actionability and actual
Winner capture. C1 contains permitted positive Technical values and W1 freezes a
real feature JSON/hash. T2 retains the same attractive numerics but becomes
insufficient; Combined and Ranking omit it, Ranking population cannot promote it,
Setup promoted values become null, Lifecycle actionability is BLOCKED and actual
alert rules create no actionable alert. W2 is atomically excluded; W1 remains
unchanged. The separate native PostgreSQL Technical lane verifies real persisted
Lifecycle/Alert propagation and absence of ACTIONABLE alerts.

## 12. Contextual attack

I2 retains attractive IBMI features while native availability becomes FAILED.
Ranking's enabled liquidity overlay contributes nothing; CERI point-in-time
selectors omit volatility and short pressure before their real four-output math.
The evolving fixture includes a fully rated CERI1 with native complete revision,
guidance, catalyst, confidence and feed-freshness inputs. CERI2 retains its own
valid evidence but loses the invalid IBMI influence. Its zero risk contribution
is the native missing-module maximum, not a zero-valued valid IBMI observation.

M2 remains bullish but is STALE; S2 retains rank 1 but is non-ready. Setup context
and Winner optional context are null/omitted. R2 retains attractive Ranking
values under UNKNOWN and is mandatory-rejected at Winner. Sector also omits each
newly fenced input before native population/score/history math.

## 13. Same-value/different-readiness campaign

The numeric campaign covers 23 policies × 6 non-READY states = 138 actual consumer
paths. Eleven additional old-v1 READY attacks exercise every corrected consumer.
No policy evaluation is mocked. I/O fixtures provide explicitly selected frozen
evidence; real business engines run. Excluded output is compared with explicitly
absent-source output, including components, penalties, population, Setup signals,
CERI four-output payloads, Sector metrics and nullable Winner feature JSON.
Consumer calls do not mutate the presented frozen source payload, including an
old v1 READY envelope. Native history flags and the evolving capture fixture add
producer and integration regressions, for 157 T12E adversarial-module cases.

## 14. POLICY_UNDECIDED campaign

All 69 DEGRADED / UNKNOWN / LEGACY_UNKNOWN edge-state combinations execute actual
numeric consumption boundaries and abstain. There is no selected subset. The
additional eleven unsupported-v1 READY cases also abstain. Optional sources are
missing; mandatory Winner sources reject; native Combined/Ranking missing-source
rules apply once. Policy_UNDECIDED is never converted to permission by a truthy
numeric, missing decision, sentinel or implicit default.

## 15. Error/stale/legacy campaign

All producer families with policies are attacked with ERROR remnants, frozen
STALE and positive legacy/unknown evidence. Native staleness is supported for
IBMI, Regime, CERI and Setup; it is NOT_APPLICABLE as an invented age policy for
Technical, Fundamental, Combined, Ranking and Sector. Their policies nevertheless
defensively reject a supplied frozen STALE envelope. Legacy artifacts are not
normalized under today's policy or promoted through current projection reads.

## 16. Fake-zero audit

The static search covers score, rank, confidence, coverage, freshness, stale,
insufficient_data, readiness, eligibility, actionable, gate, sizing, posture and
feature_json across services, routers and models. Omission occurs before model
math. Winner optional feature fields remain null; Setup signals/promoted values
remain missing; absent overlays do not become valid poor grades/rank zero.
Native model defaults after omission are separately identified: CERI's maximum
of no optional risk contributions and existing Combined/Ranking missing-source
normalization. These do not authorize excluded source numerics.

## 17. Double-penalty audit

The 138 state attacks compare actual business outputs with an absent input,
including penalty ledgers and warnings. Combined retains exactly its existing
missing-source penalty; Ranking adds no invalid liquidity overlay penalty;
CERI does not add an eligibility-derived stale/poor-value penalty; Setup uses
its native missing-context/data-quality rules; Winner omits nullable features
or rejects before math. A spurious Combined diagnostic missing-Fundamental flag
was corrected when permitted Fundamental remained present and Technical was
omitted. No scoring formula or penalty parameter changed.

## 18. Current/global fallback attacks

Exact selected non-ready evidence cannot cause substitution with a newer READY
projection. Technical/Ranking native PostgreSQL tests create distinct READY
current evidence while the immutable handoff points to blocked evidence and
prove rejection with no partial records. IBMI selectors retain selected scoped
evidence; Regime/Sector selectors retain existing compatibility/cutoff ordering.
The original T12B/C/D fallback and phase-identity adversarial cases pass, including
newer incompatible global Regime. Newly fenced Sector dependencies apply
permission after selection, without a second selector after rejection.

## 19. Cross-run Regime certification

READY immutable context-compatible Regime can be reused across runs in Setup and
Winner. Native Winner seeds run 7 with compatible Regime run 99. Native contextual
Setup and phase-identity tests preserve this reuse. A newer incompatible global
READY Regime is not substituted. No same-run requirement or relaxed calendar/
cutoff/configuration compatibility was introduced.

## 20. Immutable eligibility evidence

Combined, Ranking, CERI, Sector, Setup and Winner record source evidence IDs,
readiness fingerprints, producer versions, consumer versions, typed reasons and
inclusion/omission. The shared evidence writer rejects injected reserved
readiness, hashes the normalized envelope and preserves Phase-2 immutable rows.
Historical C1 remains while E2/R2/C2 advances the scoped current projection.
Native Sector service tests persist C1/C2 and deterministic retry after all three
universe sources and Regime become invalid, retaining every C1 permission.

Sector's universe payload freezes Technical/Combined/Ranking permissions and
exact evidence addresses; its existing relational source ledger retains Ranking,
Regime and prior Sector roles. Readiness/evidence hashing does not claim a new
relational source FK for every universe fact or full original-context replay.

## 21. Winner frozen semantics

Native PostgreSQL successful captures are followed by current Technical,
Ranking, Regime and Sector advancement. Both acquisition entry aliases and
current Technical/Contextual policy evaluation are made to raise if called.
Historical reads, actual SQL rescore and actual outcome maturation still succeed
using W1. Feature JSON and reserved eligibility lineage remain unchanged;
operational outcome lineage may legitimately advance. Changing the reserved
permission document is rejected by ORM immutability hooks. Prospective policy
changes neither overwrite W1 nor silently reuse its old capture under a new
identity; retry conflicts remain explicit.

## 22. Winner atomicity

Native Technical, Ranking and Combined rejection cases leave zero prediction,
episode, forward outcome, target/stop outcome and probability-estimate rows.
All six decisions are available in rejection diagnostics. Retry is identical and
also creates nothing. Optional invalid Fundamental/Regime/Sector may produce a
valid capture with null features, preserving considered-source provenance.
Successful retry creates no additional rows. The evolving fixture proves W2
rejection does not damage previously frozen W1.

## 23. READY parity

Earliest safe pre-enforcement checkout: T12A
`4c8eb3402b951482d590890e726195a3c445d786` (contract foundation before consumer
enforcement). Baseline application/math code is unchanged; identical explicitly
healthy native inputs and opt-in observational probes are copied into a detached
task-owned checkout. Generic ORM constructors remain unsealed for legacy attacks.

Both trees pass 22 shared scenarios and produce **64 byte-identical captures**:
Combined 3; Ranking 10; Setup 6; Lifecycle 9; actionability 6; CERI 2;
Winner features 6; Winner snapshots 6; Winner estimates 6; Winner probability 6;
Sector universe 2; Sector snapshots 2. SHA-256:
`b42081324a8bf4b889823b9d17a83cbf1f6ffb34b5199376ec8b94f89755137b`.
There is no tolerance, numeric normalization after capture, difference allowlist
or business-output suppression. Newly expected permission/provenance metadata is
outside business parity. CERI includes one existing native-unrated control and
one fully rated native-READY case; no claim that the control's producer is READY.
Winner's real estimator uses a representative frozen cohort I/O fixture and
produces rated point probability 0.5 in each six-mode comparison.

An initial Setup parity fixture used an object sentinel as a supposedly present
market source. It did not satisfy READY preconditions; its positive comparison
now supplies actual native-sufficient Regime evidence in both checkouts. Legacy
sentinel abstention remains covered separately. This was fixture correction, not
an approved behavior difference. Final unexpected READY differences: zero.

## 24. Performance/query sanity

Ranking and CERI source cohorts retain bounded IBMI/evidence preloads. Native
contextual SQL instrumentation verifies preloaded scoped features and repeated
Ranking evaluation add zero SELECTs; CERI's cohort IBMI loader uses select-in
evidence reads, with no per-source permission query after preload. Setup adds
Fundamental/Combined evidence preloads, and native repeated builder evaluation
checks zero evidence SELECTs. Winner adds two bounded evidence preloads, and
seven repeated acquisitions after the actual repository loader make zero
SELECTs. Sector loads each material source cohort and evidence in bounded
queries before filtering. No per-ticker readiness lookup is introduced in these
batch entry points. This is query sanity, not a throughput/latency SLA or a new
performance-remediation project.

## 25. Static consumer re-audit

Classification units below are named consumer/service families, not raw `rg`
match counts, and may share a service containing both display and material fields.
The audit inspected repository-wide producer model references and the required
decision vocabulary. Detailed evidence is retained in task-owned QA search logs.

| Classification | Count / scope | Evidence and disposition |
|---|---|---|
| PHASE3_ENFORCED | 23 policy edges + 2 propagation edges | Canonical matrix and real numeric stage tests |
| ALREADY_SAFE | 4 native/trusted interface families | Raw producer validation; native feature engines; post-permission pure model DTOs; native probability/cohort validity |
| DISPLAY_ONLY | 7 families | Market Participation; Sector Leadership; Sector Fundamental average; Setup score/rank labels; CERI alignment context; Winner diagnostic denormalization; routers/exports/current views |
| LEGACY_CURRENT | 1 compatibility-view family | Unpointed legacy current projections remain diagnostic, never certify new use |
| FROZEN_HISTORICAL_READ | 6 families | Combined, Ranking, CERI, Sector, Setup/Lifecycle/Alerts, Winner read exact stored evidence |
| PRODUCER_GAP | 2 diagnostic families | Regime stale gate/sizing; CERI own stale-risk penalty |
| OUT_OF_SCOPE | 7 deferred workstreams | Listed in §33; no newly introduced material graph edge |
| POTENTIAL_BYPASS | 0 material in-scope edges | Every candidate adjudicated or fenced |
| CONFIRMED_BYPASS | 0 remaining material in-scope edges | Newly confirmed defects fixed and regressed |

Market Participation and Sector Leadership are calculated after Regime score,
policy and gate, then stored/exported; no decision selector consumes their
averages. Sector's Fundamental average is absent from its scoring formula.
CERI alignment flags/context are display ledgers; posture derives solely from
opportunity, event-risk and native confidence outputs. Ranking metadata in Setup
does not influence actionability; Combined earnings risk and Fundamental liquidity
do, and those specific material fields are newly fenced.

## 26. PostgreSQL certification

Task-owned container `swinglens-t12e-pg-20260916`, PostgreSQL 16.13, loopback
55434; fixture-created `swinglens_pytest_*` databases are guard-verified before
Alembic and dropped after each case. Eleven evidence/Winner integration modules
pass 71 cases, comprising 63 inherited Phase-2 cases and 8 native expanded Winner/
Sector cases. Integration modules contain some SQLite/DDL tests; 71 is not
misrepresented as 71 PostgreSQL-only cases. The complete Phase-0 lane separately
includes 36 native PostgreSQL tests. The union of lane case IDs is 107 distinct
evidence/safety cases, with native PG coverage identified by the real fixtures.

Persistence, exact evidence pins, idempotency, policy preservation, historical
immutability, current advancement, legacy abstention, successful capture, atomic
rejection, historical Winner read, rescore and actual maturation are certified.
Native maturation seeds valid TRADES bars for MSFT/SPY/XLK, resolves a real IB
contract and satisfies actual market-data obligations before maturing H5
NEXT_OPEN forward and target/stop outcomes. Native close return is 3%; no
maturation/evidence-service bypass is used to fake success.

Fresh upgrade/downgrade, no-backfill and schema/constraint/index evidence retain
the Phase-2 certification lane. Head remains 0079. Cleanup verifies zero owned
test databases before removing the task container. Authoritative databases,
monitoring containers and unrelated worktrees are untouched.

## 27. Phase-2 regression

63 inherited evidence tests pass, plus 8 native expanded Winner/Sector tests.
Core, Regime/Sector, CERI, IBMI, Setup/Lifecycle/Alert evidence; historical-read
enforcement; certification, migration, schema and readiness/consumer tests remain
intact. ORM changes add only view-only lazy-raise evidence relationships on
Fundamental and Combined, reusing existing evidence_id FKs. No column, constraint,
table, index or migration change is introduced. AST schema comparison against
starting HEAD and native database inspection gates pass.

## 28. Phase-1 regression

135 passing cases across six identity/adoption/certification modules. Exact
calculation cutoff, subject/owner, calendar, algorithm/configuration component
compatibility and negative dependencies remain enforced. Readiness does not
replace Calculation Identity or permit source identity substitution.

## 29. Phase-0 regression

141 passing cases across twelve fence/session/preflight/Winner/publication/CERI/
IBMI modules, including 36 native PG cases. The final lane is run after the last
production mapping correction. No permission fix weakens decision clocks,
source availability, temporal boundaries, write fences, generation or publication
rules. Existing 40 warnings are reported; no readiness-related skips/deselections.

## 30. Failure/flakiness analysis

Every observed non-green development event was inspected and rerun in its exact
or nearby lane. No failure is dismissed as unrelated or called flaky without
evidence. All final required lanes are green; no readiness failure remains unexplained.

| Event | Classification | Cause / correction | Verification |
|---|---|---|---|
| Newly found sparse Regime and uncovered Fundamental/Combined/Sector edges | PHASE3_DEFECT | Narrow native mapping and exact permission boundaries | Whole graph, native SQL, original lanes and broad suite |
| Old-v1 READY compatibility loophole | PHASE3_DEFECT | Required v2 producer versions; preserve old envelopes | Eleven actual numeric attacks and frozen-history tests |
| Spurious Combined missing-Fundamental diagnostic | PHASE3_DEFECT | Retain permitted Fundamental when deriving diagnostic warnings | Absent-input equality/penalty campaign |
| First focused run: 636 pass / 2 fail | TEST_INFRASTRUCTURE | Warning-bearing Sector input was not READY; prospective Winner version fixture changed only four policies | Healthy positive preconditions; all six policy versions tested |
| First broad run: 2,974 pass / 11 fail / 7 deselected | TEST_INFRASTRUCTURE | Unsealed/stale-pointer positive factories; native-degraded golden expectations; legacy previous-Sector fixture | Exact failing families, nearby lanes, full fresh broad lane |
| First native consumer run: 4 pass / 2 fail | TEST_INFRASTRUCTURE | Unit synthetic evidence IDs leaked into PG fixtures, violating real FKs | Native writers allocate evidence; preload rerun passes |
| Native contextual v2 rerun: 62 pass / 1 fail | TEST_INFRASTRUCTURE | Positive Regime fixture lacked native primary sufficiency proof | Explicit SPY proof; exact native case and full 71-case lane pass |
| Initial T12E numeric fixtures | TEST_INFRASTRUCTURE | Wrong native config keyword, comparing ORM object identity, CERI DTO comparison shape | Real config/DTO canonical comparison; full 160-case lane |
| Initial evolving fixture | TEST_INFRASTRUCTURE | Copied updated_at on models without that column; incomplete native positive inputs | Actual DTO models, shared Raw inputs, native-rated CERI and capture/alert integration |
| Initial native maturation | TEST_INFRASTRUCTURE | Missing resolved contract and satisfied obligation prerequisite | Actual contract/obligation services; real outcomes mature |
| Native Sector fixture | TEST_INFRASTRUCTURE | Technical metadata has decision status, not included field; queried seeded row instead of service revision; generic vs write DTO payload shape | Actual latest service revision/DTO payload; native C1/C2/retry case passes |
| READY precondition/probe fixture errors | TEST_INFRASTRUCTURE | Sentinel market source; incorrect module import/freshness enum; missing accepted guidance/earnings context; one stale node selector | Exact healthy inputs in both unchanged application trees; 64 byte-identical captures |
| Affected-lane argument inventory | TEST_INFRASTRUCTURE | File discovery included six CERI JSON/HTML fixtures; pytest exit 4 before running cases | Restrict discovery to test_*.py; corrected 1,731-case lane passes |
| Strengthened active-IBMI evolving fixture: 159 pass / 1 fail | TEST_INFRASTRUCTURE | A real poor-liquidity penalty also natively degrades Ranking, violating W1 READY preconditions | GOOD liquidity for W1; separate all-state active 0.75 penalty attacks remain; final 160 and combined 563 pass |
| Ruff failures during edits | TEST_INFRASTRUCTURE | Import ordering and line length | Format/import corrections; final Ruff gate |
| PRE_EXISTING_UNRELATED | 0 failures claimed | Existing warnings/external exclusions are disclosed separately | No unrelated-failure suppression |
| ENVIRONMENT_BLOCKER | 0 remaining | Disposable Docker PG established; initial missing capability resolved | Actual guarded native tests |
| FLAKY_TEST_CONFIRMED | 0 | No unexplained race/isolation/order-dependent readiness failure | Exact/nearby/full reruns; no suppressed tests |

The golden fixture retains its pre-Phase-3 expected output explicitly. Real native
Fundamental remains 7.7563; it is degraded and omitted. Combined becomes 7.14,
Incomplete data / Wait, with the existing single missing-source penalty. This is
an expected permission behavior change, not a READY model rewrite or weakened
assertion. The READY golden comparison uses genuinely READY inputs instead.

### Final lane accounting

| Lane | Passed / selected | Failed | Skipped | Deselected | Warnings | Evidence |
|---|---|---|---|---|---|---|
| 1 T12E adversarial + shared READY | 160 / 160 (157 + 3) | 0 | 0 | 0 | 1 | t12e-adversarial-certified.json/log; final strengthened active IBMI checks |
| 2 Cross-consumer state matrix | 161 / 161 | 0 | 0 | 0 | Shared 1 | t12e-focused-certified.json/log |
| 3 T12D Winner + frozen + READY | 74 / 74 (61 + 7 + 6) | 0 | 0 | 0 | Shared 1 | Same focused run |
| 4 T12C contextual + CERI + READY | 82 / 82 (59 + 14 + 9) | 0 | 0 | 0 | Shared 1 | Same focused run |
| 5 T12B core + READY | 41 / 41 (37 + 4) | 0 | 0 | 0 | Shared 1 | Same focused run |
| 6 T12A foundation | 45 / 45 | 0 | 0 | 0 | Shared 1 | Same focused run; combined lanes 1–6 = 563 passes |
| 7 Affected safe subsystems | 1,731 / 1,731 | 0 | 0 | 0 | 1 | t12e-affected-certified.json/log; explicit test-module inventory |
| 8 Phase-2 inherited evidence | 63 / 63 | 0 | 0 | 0 | Shared 28 | Inherited cases in the 71-case evidence/native-PG run |
| 9 Phase-1 | 135 / 135 | 0 | 0 | 0 | 1 | t12e-phase1-final.json/log |
| 10 Phase-0 | 141 / 141 | 0 | 0 | 0 | 40 | t12e-phase0-final.log; includes 36 native PG cases |
| 11 Evidence/native PostgreSQL | 71 / 71 (63 inherited + 8 expanded) | 0 | 0 | 0 | 28 | t12e-postgres-certified.json/log; native vs SQLite/DDL scope disclosed in §26 |
| 12 Broader safe repository | 3,138 / 3,138 (3,145 collected) | 0 | 0 | 7 | 22 | t12e-broad-certified.json/log; exact exclusions below |
| 13 Static gates | All PASS | 0 | 0 | 0 | No pytest run count | Ruff, compileall, diff check, single head, schema AST, architecture/policy inventory |
| Follow-up native Winner/Setup query checks | 8 / 8 | 0 | 0 | 0 | 9 | t12e-winner-query-certified.json/log; overlapping lane-11 case IDs |
| Shared READY baseline and current comparison | 22 / 22 in each tree | 0 | 0 | 0 | 1 each | t12e-baseline-parity-complete.log / t12e-current-parity-complete.log; 64 byte-identical captures |

QA JSON/log artifacts are retained in repository-local ignored `.qa_work` for this
task. JSON reports retain complete command arguments and exact case IDs. Lanes
overlap and must not be summed as distinct tests. The one focused-run warning is
shared across its six module subsets; likewise the Phase-2 subset shares the
28-warning evidence/native-PG run. Final strengthened IBMI assertions changed
only test preconditions; the fresh combined focused run also passes all 563.

Final failed = 0; skipped = 0 in certified lanes. The broader safe lane deselects
seven live-provider smoke parameters, listed below. Warning families are existing
Starlette/httpx deprecation, SQLite datetime adapter and Alembic path_separator
deprecation; counts belong to their individual runs and overlap across lanes.
LF/CRLF Git notices are not pytest warnings. No new xfail or readiness suppression.

### Explicit broader exclusions

The established safe command ignores `tests/integration` (certified separately),
`tests/e2e` (live browser), `tests/test_migration_remediation.py` (unrelated legacy
migration-remediation suite; required migration checks run natively here), and
`tests/qa/test_qa_infrastructure.py` (subprocess QA self-test). Marker filter is
`not external and not e2e`. Exactly these live IBMI cases are deselected:

- test_external_ib_market_intelligence[liquidity-external_ib_bid_ask]
- test_external_ib_market_intelligence[short-pressure-external_ib_fee_rate]
- test_external_ib_market_intelligence[volatility-external_ib_volatility]
- test_external_ib_market_intelligence[options-external_ib_generic_ticks]
- test_external_ib_market_intelligence[scanner-external_ib_scanner]
- test_external_ib_market_intelligence[histogram-external_ib_histogram]
- test_external_ib_market_intelligence[flex-external_ib_flex]

All seven are in `tests/ib_market_intelligence/test_external_smoke.py`. They check
live-provider capability, not Phase-3 permission semantics. The affected subsystem
lane explicitly lists the safe subsystem modules and excludes that live smoke
file. No readiness-relevant test is deselected. Opt-in QA lane reporting records
exact arguments, outcomes, deselected node IDs, warning categories and collection
errors without altering execution.

### Changed file inventory

**43 explicitly staged files**: 13 application files, 25 test/fixture files,
2 observational QA scripts and 3 documentation files.

- `app/models/tables.py`
- `app/services/combined_decision.py`
- `app/services/contextual_consumer_eligibility.py`
- `app/services/producer_readiness.py`
- `app/services/ranking_profile_engine.py`
- `app/services/ranking_profile_service.py`
- `app/services/sector_rotation_service.py`
- `app/services/sector_universe_service.py`
- `app/services/setup_lifecycle/snapshot_builder.py`
- `app/services/setup_lifecycle/source_loader.py`
- `app/services/technical_consumer_eligibility.py`
- `app/services/winner_probability/consumer_eligibility.py`
- `app/services/winner_probability/repository.py`
- `docs/architecture/SWINGLENS_READINESS_CONTRACT.md`
- `docs/architecture/SWINGLENS_READINESS_PHASE3_CERTIFIED.md`
- `docs/remediation/calculation-lineage/T12E_phase3_readiness_certification.md`
- `scripts/qa/t12e_behavior_probe.py`
- `scripts/qa/t12e_lane_report.py`
- `tests/ceri_ready_helpers.py`
- `tests/core_readiness_helpers.py`
- `tests/fixtures/golden_pipeline.json`
- `tests/integration/test_contextual_consumer_eligibility_postgresql.py`
- `tests/integration/test_technical_consumer_eligibility_postgresql.py`
- `tests/integration/test_winner_consumer_eligibility_postgresql.py`
- `tests/setup_lifecycle/test_snapshot_builder.py`
- `tests/test_combined_decision.py`
- `tests/test_combined_ranking_identity_adoption.py`
- `tests/test_contextual_consumer_eligibility.py`
- `tests/test_golden_pipeline.py`
- `tests/test_phase3_consumer_readiness_matrix.py`
- `tests/test_ranking_profile_engine.py`
- `tests/test_ranking_profile_service.py`
- `tests/test_ranking_profiles_golden.py`
- `tests/test_schema_phase2.py`
- `tests/test_sector_rotation_service.py`
- `tests/test_sector_universe_service.py`
- `tests/test_t12b_ready_scenarios.py`
- `tests/test_t12e_ready_scenarios.py`
- `tests/test_technical_consumer_eligibility.py`
- `tests/winner_probability/readiness_capture_helpers.py`
- `tests/winner_probability/test_consumer_readiness.py`
- `tests/winner_probability/test_readiness_frozen_operations.py`
- `tests/winner_probability/test_t12e_readiness_certification.py`

## 31. Finding reconciliation

| Finding | T12E disposition | Scope / retained limitation |
|---|---|---|
| CORE-001 | CLOSED_FOR_READINESS | Native sparse-history mapping and v1 compatibility loophole corrected; classifier diagnostics retained |
| CORE-002 | CLOSED_FOR_ACTIONABILITY; PARTIAL_OVERALL | Typed STALE blocks all material consumers; native stale gate/sizing diagnostics remain |
| CORE-009 | CLOSED_FOR_ACTIONABILITY | Technical numeric remnants retained but cannot authorize material use |
| RANK-001 | CLOSED | Invalid Technical omitted before Combined weighted components |
| RANK-002 | CLOSED | Invalid Technical omitted before Ranking components, population and rank |
| CERI-007 | PARTIAL / OPEN_FOR_OWN_STALE_PENALTY | Original event-risk company_stale ordering remains; IBMI permission enforcement does not close it |
| SETUP-003 | CLOSED | Frozen Technical ineligibility blocks Setup/Lifecycle/actionable Alerts |
| WIN-003 | CLOSED_FOR_READINESS; PARTIAL_OVERALL | Configuration authority remains later work |
| WIN-004 | CLOSED_FOR_READINESS | All six material source quality/readiness permissions enforced; Fundamental nullable, Combined mandatory |
| WIN-005 | CLOSED_FOR_CONSUMER_READINESS | Exact context and typed source permission; producer diagnostics tracked separately |
| XINT-004 | CLOSED_FOR_CERTIFIED_PHASE3_BOUNDARY | All 23 material edges enforced, including corrected producer misclassification |
| INV-READINESS-001 | ENFORCED | Same-value, undecided, blocking, omission, history, actionability and capture tests |
| T12E-N1 | CLOSED | Newly confirmed Fundamental/Combined material consumer omissions fixed |
| T12E-N2 | CLOSED | Five newly confirmed Sector dependency bypasses fixed |
| T12E-N3 | CLOSED | Prospective old-v1 READY authorization loophole fixed |
| T12E-N4 | CLOSED | Spurious diagnostic missing-Fundamental warning fixed |

Scoped closure does not imply removed diagnostics, complete producer model
correctness, configuration authority or full original-context replay.

## 32. INV-READINESS certification

ENFORCED within the certified supported-service boundary: insufficient or invalid
evidence cannot regain decision/actionability influence through surviving
numerics, implicit defaults, sentinels, current fallback or the identified native
Regime producer misclassification. Every material consumer requires permission
for exact frozen evidence. Old misclassified/fence-free v1 evidence cannot
authorize new v2 decisions. Historical semantics remain immutable.

## 33. Residual risks

No material Phase-3 consumer bypass or incorrectly actionable READY producer gap
remains under the declared native contracts. Residual diagnostics: native Regime
stale sizing/gate and CERI own stale-risk penalty. Legacy current views can show
numeric remnants without authorizing new consumption. Existing historical
permissions are not rewritten to conform to prospective policy changes.

Deferred work: configuration authority; original-context replay/reconstruction;
acquisition-plan semantics; refresh-cycle identity; target-scope freezing;
entry-point unification; privileged direct-SQL governance. Readiness hashes do
not prove these guarantees. No Phase-4 implementation is undertaken.

## 34. Final Phase-3 verdict

**PASS — PHASE-3 IMPLEMENTATION CERTIFIED: YES; PHASE-3 POSTGRESQL RUNTIME
CERTIFIED: YES; PHASE-3 OVERALL CERTIFIED: YES.** All required lanes pass.
The certified readiness boundary is closed, INV-READINESS-001 is enforced,
and the repository is safe to proceed to Phase 4. This does not close
CERI-007, native diagnostic calculations or deferred authority/replay work.

Production/runtime mutations: repository code/tests/docs, task-owned test data,
temporary baseline checkout and disposable PG container only. No authoritative
database mutation, production rewrite, legacy backfill, merge or push. Final
cleanup preserves unrelated worktrees/monitoring and leaves the explicitly
committed worktree clean.
