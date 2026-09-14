# T10C — Contextual Consumer Calculation Identity Adoption

## 1. Baselines

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T09A | `2e0487a271d425b30fb61b17a33717562e0abe3b` |
| T09B | `c7b8df7f25fe709b0fa7b20bf2fe3b738afcfb29` |
| T09C | `bbb345b3257d0df572db425242c62ea1447e5d24` |
| T10A | `e08bf42a527c1345883e5587af794a7396443a7e` |
| T10B / T10C starting HEAD | `7edef90175de190f4fbfc5ea3fd283ed8c9aa2a9` |
| Branch | `codex/t10c-contextual-identity-adoption` |
| Final HEAD | The focused T10C commit containing this report; its exact SHA is recorded in the final response because a commit cannot contain its own SHA. |
| Alembic head before/after | `0074_ceri_evidence_quarantine` |
| Python | 3.12.2 from `.venv` |

The branch was created from the exact required T10B base. Before editing, the repository had
no tracked modifications. Pre-existing untracked architecture/audit registry files and IB
historical-probe files were preserved and were not staged.

## 2. Scope

T10C adopts the shared T10A `CalculationIdentity` representation in Market Regime, Sector
Rotation, Setup/Lifecycle input assembly, CERI contextual consumption, and reusable IBMI
features. It changes producer/consumer compatibility and propagation only.

Winner acquisition/backfill/cohort design, immutable price/evidence architecture, readiness,
repository-wide configuration authority, entry-point unification, and replay/backfill
semantics remain excluded. No CERI → Setup, CERI → Winner, Setup/Lifecycle → Winner, or
Sector → Ranking dependency was introduced.

## 3. Compatibility policies

| Policy | Producer → consumer | Required dimensions and reuse rule | Mismatch / unknown behavior |
|---|---|---|---|
| `REGIME_CONTEXT_COMPATIBILITY` | Regime → contextual consumer | Exact session, cutoff, calendar, complete Regime config, calculation/engine version; source lineage must be known. Run/pipeline/context/ticker are intentionally N/A for a global Regime. | Candidate rejected; `UNKNOWN`/`LEGACY_UNKNOWN` never pass. |
| `SECTOR_RANKING_COMPATIBILITY` | Ranking → Sector | Exact run, pipeline, ticker, context ID/fingerprint, session, cutoff, and calendar. Config, calculation version, and source lineage must be known; the selected profile cohort must be config/version coherent. | Ranking row is omitted from Sector input. Unknown/legacy is omitted. |
| `SECTOR_REGIME_COMPATIBILITY` | Regime → Sector | Same global contextual contract as Regime above. | Newest incompatible candidate is skipped; absence is explicit context unavailability. |
| `SECTOR_PRIOR_COMPATIBILITY` | prior Sector → Sector | Exact calendar, Sector config, calculation/engine version, and mode component, plus a proven earlier session. Prior run ownership is irrelevant. | Incompatible candidate is skipped; no match means no prior state. |
| `SETUP_TECHNICAL_COMPATIBILITY` | Technical → Setup | Exact run/pipeline/ticker/context/session/cutoff/calendar; known config/calculation/source lineage. | Artifact is removed from assembly and cannot drive Setup. |
| `SETUP_COMBINED_COMPATIBILITY` | Combined → Setup | Same exact run-context spine and intrinsic proof requirements. | Both behavioral earnings risk and metadata are omitted. |
| `SETUP_RANKING_METADATA_COMPATIBILITY` | Ranking → Setup | Same exact run-context spine and intrinsic proof requirements. | Metadata/provenance is omitted; Ranking does not become a lifecycle gate. |
| `SETUP_REGIME_COMPATIBILITY` | Regime → Setup | Same validated global Regime contract. | Candidate is omitted; Technical fallback regime is not used. |
| `SETUP_SECTOR_COMPATIBILITY` | Sector → Setup | Exact run/pipeline/context/session/cutoff/calendar, current Sector config, calculation/engine version; known source lineage. | Sector context is omitted. |
| `CERI_IBMI_COMPATIBILITY` | IBMI → CERI | Global N/A ownership/context, exact ticker/session/calendar/current complete config/calculation/source versions, known source lineage, and a feature cutoff at or before the CERI cutoff. | Optional feature is omitted. |
| `IBMI_CONTEXT_COMPATIBILITY` | IBMI → other contextual consumer | Reusable alias of the same explicit IBMI proof contract. | Optional evidence is omitted; unknown never passes. |
| `CERI_CONTEXT_COMPATIBILITY` | existing CERI → CERI reuse | Exact run/pipeline/ticker/context/session/cutoff/calendar; query constrains company/config/version and identity must have known config/calculation/source lineage. | Incompatible or legacy existing result fails closed; it is not upgraded. |

No policy treats ownership equality, row recency, `UNKNOWN`, or `LEGACY_UNKNOWN` as
compatibility evidence.

## 4. Compatibility matrix

| Producer | Consumer | Required identity dimensions | Globally reusable? | Mismatch behavior | UNKNOWN behavior | Legacy behavior | Policy name |
|---|---|---|---|---|---|---|---|
| Ranking | Sector | run, pipeline, ticker, context, session, cutoff, calendar; known/coherent profile config, calculation version, cohort lineage | No | omit row | reject | omit | `SECTOR_RANKING_COMPATIBILITY` |
| Regime | Sector | session, cutoff, calendar, Regime config, calculation/engine version, benchmark lineage | Yes | skip candidate / unavailable | reject | omit | `SECTOR_REGIME_COMPATIBILITY` |
| prior Sector | Sector | predecessor session, calendar, Sector config, calculation/engine version, mode | Across runs, yes | skip candidate / no prior state | reject | omit | `SECTOR_PRIOR_COMPATIBILITY` |
| Technical | Setup | run, pipeline, ticker, context, session, cutoff, calendar; known config/version/lineage | No | omit; cannot drive Setup | reject | omit | `SETUP_TECHNICAL_COMPATIBILITY` |
| Combined | Setup | run, pipeline, ticker, context, session, cutoff, calendar; known config/version/lineage | No | omit risk and metadata | reject | omit | `SETUP_COMBINED_COMPATIBILITY` |
| Ranking | Setup | run, pipeline, ticker, context, session, cutoff, calendar; known config/version/lineage | No | omit metadata only | reject | omit | `SETUP_RANKING_METADATA_COMPATIBILITY` |
| Regime | Setup | session, cutoff, calendar, Regime config/version, benchmark lineage | Yes | omit context | reject | omit | `SETUP_REGIME_COMPATIBILITY` |
| Sector | Setup | run, pipeline, context, session, cutoff, calendar, Sector config/version/mode lineage | No | omit context | reject | omit | `SETUP_SECTOR_COMPATIBILITY` |
| IBMI volatility | CERI | ticker, session, bounded cutoff, calendar, IBMI config/calculation/source version and lineage | Yes | omit optional feature | reject | omit | `CERI_IBMI_COMPATIBILITY` |
| IBMI short pressure | CERI | ticker, session, bounded cutoff, calendar, IBMI config/calculation/source version and lineage | Yes | omit optional feature | reject | omit | `CERI_IBMI_COMPATIBILITY` |
| IBMI reusable feature | contextual consumer | global N/A ownership/context, ticker, session, bounded cutoff, calendar, config/calculation/source version and lineage | Yes | omit optional feature | reject | omit | `IBMI_CONTEXT_COMPATIBILITY` |

## 5. Market Regime adoption

New Market Regime snapshots embed a canonical identity in existing debug metadata. The
identity derives session/cutoff/calendar from the frozen market context, hashes the complete
effective Regime configuration, records calculation/engine versions, and fingerprints the
bounded benchmark, participation, and sector-leadership source envelope.

A global snapshot deliberately carries N/A upload-run, pipeline, market-context, and subject
dimensions. Sector and Setup enumerate bounded candidates newest-first but accept the first
one satisfying the Regime policy. Therefore cross-run/global reuse is supported without an
identity-blind latest-global fallback.

## 6. Sector adoption

Ranking rows are no longer admitted from `run_id` alone. The universe builder validates each
Fundamental, Technical, Combined, and Ranking artifact on the same explicit run/pipeline/
context/temporal spine before it can contribute; Ranking profile cohorts with mixed effective
config or calculation-version identities are removed.

Regime selection now skips incompatible run/global candidates. Prior Sector selection
enumerates snapshots across runs, validates the current Sector config/model/calendar/mode,
and requires a strictly earlier session. A newer incompatible row cannot mask an older
compatible predecessor. The produced Sector snapshot embeds its own identity and exact
Ranking, Regime, and prior-Sector references.

## 7. Setup adoption

- Technical remains the direct behavioral calculation input, but only after exact
  run/pipeline/ticker/context/session/cutoff/calendar validation.
- Combined `earnings_risk` remains behavioral. If Combined is incompatible, the entire
  Combined artifact is omitted, so its risk cannot influence actionability.
- Combined score/decision and Ranking score/decision/profile remain metadata only.
  Incompatible metadata is omitted, not promoted into a new Setup prerequisite.
- Regime is accepted only under compatible global contextual identity; the former
  `TechnicalScore.market_regime` fallback was removed.
- Sector is accepted only with current same-run/pipeline/context/temporal and Sector
  config/version identity.
- The raw-row earnings-risk fallback was removed because it had no compatible Combined
  calculation identity.

Setup outputs embed a Setup-specific identity with exact accepted source references and the
bounded price/trigger input envelope. CERI is still **not** a Setup input.

## 8. CERI adoption

CERI now builds its context envelope from the frozen run/pipeline/ticker/company/session/
cutoff/calendar spine and embeds its own config/calculation identity in the existing evidence
lineage JSON. Exact accepted IBMI source identities are included in that lineage.

Volatility and short-pressure queries remain point-in-time bounded, then scan newest-first
for a feature satisfying current session/calendar/config/calculation/source-version/lineage
requirements and `feature cutoff <= CERI cutoff`. An incompatible optional feature is omitted
without invalidating CERI. Existing T09A bounded price/source acquisition is unchanged; the
new identity envelope does not weaken its cutoff predicates.

Existing CERI results are reusable only when their embedded identity matches the current
context. A legacy or incompatible row fails closed instead of being silently reused or
rewritten.

## 9. IBMI reusable identity

IBMI remains intentionally global. Safe reuse is proven by N/A run/pipeline/context fields,
exact ticker and session, the persisted calendar, current complete IBMI config hash,
calculation and source versions, known source-evidence hashes, and a persisted cutoff not
later than the consumer cutoff. Existence, recency, or matching ticker alone is insufficient.

## 10. Legacy handling

Artifacts lacking an embedded shared identity deserialize as `LEGACY_UNKNOWN`; incomplete
IBMI fields remain `UNKNOWN`. Neither state compares equal to a known or N/A requirement.
Required Technical inputs cannot drive Setup, and incompatible optional Combined/Ranking/
Regime/Sector/IBMI evidence is omitted. Legacy CERI result reuse fails closed. No row is
mutated in place and no historical identity is inferred or backfilled.

## 11. Findings

| Finding/invariant | T10C status | Basis |
|---|---|---|
| `CORE-006` | **PARTIALLY_CLOSED** | Identity-blind Regime→Sector fallback is removed; unrelated Regime semantics remain outside T10C. |
| `CORE-007` | **PARTIALLY_CLOSED** | Prior-sector history now requires compatible predecessor identity; broader historical/immutable-state concerns remain. |
| `SETUP-005` | **PARTIALLY_CLOSED** | Canonical Setup input assembly validates all in-scope artifact identities; alternate entry-point unification is deferred. |
| `SETUP-008` | **PARTIALLY_CLOSED** | Identity-blind contextual and earnings-risk fallbacks are removed; broader Setup semantics/readiness are unchanged. |
| `XINT-001` | **PARTIALLY_CLOSED** | Combined/Ranking plus contextual consumers now use shared identity, but Winner and residual source/acquisition edges remain. |
| `INV-IDENTITY-001` repository-wide | **PARTIALLY_CLOSED** | The invariant holds on all T10C edges, not yet on every repository calculation boundary. |

## 12. Tests

| Contract | Focused proof |
|---|---|
| compatible global Regime and Regime→Sector | `test_global_regime_requires_exact_contextual_identity_not_run_ownership` |
| Regime mismatch dimensions | parameterized session/cutoff/calendar/config/version rejection |
| exact and mismatched Ranking→Sector / Technical, Combined, Ranking→Setup | parameterized named-policy acceptance and same-run/different-pipeline rejection |
| legacy unknown | `test_legacy_unknown_never_proves_setup_or_regime_compatibility` |
| Setup behavioral safety | incompatible Combined risk/Ranking metadata omission, Technical rejection, compatible Setup output identity |
| Regime→Setup | older compatible candidate selected over newer incompatible candidate |
| Sector→Setup | exact context accepted and different pipeline rejected |
| prior Sector→Sector | newer incompatible skipped; no compatible predecessor returns unavailable |
| IBMI volatility/short pressure→CERI | compatible global feature used while newer config-incompatible candidate is skipped |
| missing IBMI cutoff | optional feature omitted as insufficient identity |
| legacy CERI result | fails closed and lineage remains unchanged |
| negative architecture edges | source inspection proves no CERI→Setup/Winner, Setup→Winner, or Sector→Ranking edge |

## 13. Verification

| Lane | Exact command/scope | Result |
|---|---|---|
| T10C focused | `.venv\Scripts\python.exe -m pytest tests/test_contextual_calculation_identity_adoption.py -q` | **26 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Identity infrastructure | `.venv\Scripts\python.exe -m pytest tests/test_calculation_identity.py tests/test_calculation_identity_adoption.py tests/test_combined_ranking_identity_adoption.py tests/test_contextual_calculation_identity_adoption.py -q` | **80 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Affected subsystems | 145 explicitly selected Market Regime, Sector, Setup/Lifecycle, CERI, IBMI, Combined/Ranking, pipeline, and T10 identity files with `-m "not external and not e2e and not slow"` | **1,096 passed, 0 failed, 0 skipped, 13 deselected, 1 warning** |
| Phase-0 regressions | 12 explicit T09A temporal, T09B domain-fence, and T09C publication-fence files | **158 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Broader clean lane | `.venv\Scripts\python.exe -m pytest tests --ignore=tests/e2e --ignore=tests/integration --ignore=tests/ib_market_intelligence/test_external_smoke.py --ignore=tests/test_migration_remediation.py --ignore=tests/qa/test_qa_infrastructure.py -m "not external and not e2e and not slow" -q` | **2,486 passed, 0 failed, 0 skipped, 33 deselected, 22 warnings** |

Ruff passes on every changed Python file, `compileall` passes for the changed service modules
and focused test, and `git diff --check` passes. The repeated focused warning is the existing
Starlette/httpx deprecation; the broader lane also reports 21 existing Python 3.12 SQLite
datetime-adapter deprecations.

## 14. Intentional exclusions

- Live external/provider: `tests/ib_market_intelligence/test_external_smoke.py`, marker
  `external`, IBKR/IB Gateway, SEC/provider/network/credential tests. No live provider was
  called.
- Browser/E2E: `tests/e2e/`, markers `e2e` and `slow`, Playwright/Selenium/browser flows.
- PostgreSQL-dependent: `tests/integration/`, `tests/test_migration_remediation.py`, and
  `tests/qa/test_qa_infrastructure.py`; no disposable/live PostgreSQL credentials or
  containers were used.

## 15. Schema/data impact

```text
migration required: NO
production rewrite required: NO
legacy backfill required: NO
```

Existing JSON metadata fields hold the identity payloads. No migration was run and no
production/runtime data was read or mutated.

## 16. Residual identity risks

Self-review classification of relevant selectors and joins:

| Boundary/selector | Classification | Reason |
|---|---|---|
| Regime producer and Regime→Sector/Setup candidate scans | `INTENTIONALLY_GLOBAL_AND_COMPATIBLE` | Global ownership is N/A; all contextual dimensions and source lineage are proven. |
| Ranking→Sector | `IDENTITY_ENFORCED` | Each row has an exact run/context/temporal spine and known/coherent calculation identity. |
| prior Sector→Sector | `IDENTITY_ENFORCED` | Newest-first is only ordering among candidates; policy plus predecessor-session check decides acceptance. |
| Technical/Combined/Ranking→Setup | `IDENTITY_ENFORCED` | Same run/ticker queries are candidate discovery only; policy validation gates consumption. |
| Regime/Sector→Setup | `OPTIONAL_IDENTITY_OMISSION` | Newest compatible candidate is used; no match produces explicit absence. |
| IBMI volatility/short pressure→CERI | `OPTIONAL_IDENTITY_OMISSION` | SQL cutoff bounds candidates and the named policy gates use. |
| existing CERI result reuse | `IDENTITY_ENFORCED` | Legacy or incompatible identity fails before reuse. |
| legacy selector helpers used only by lightweight fake repositories/tests | `OUT_OF_SCOPE` | Real SQLAlchemy production paths enter the identity-aware branches; the adapters preserve existing unit contracts. |
| general display/history repository getters | `OUT_OF_SCOPE` | They do not perform calculation-consuming source joins. |
| Winner acquisition/backfill and Winner consumers | `OUT_OF_SCOPE` | Explicitly deferred to T10D. |

No in-scope `REMAINING_IDENTITY_DEFECT` was found. Residual work is:

- T10D: bind Winner acquisition to complete decision/source calculation identity without
  creating reverse dependencies.
- T10E: Phase-1 integration certification and authoritative registry/status update.
- Immutable evidence: price/source revision identity and append-only evidence architecture.
- Configuration authority: repository-wide resolved/versioned effective configuration,
  beyond the complete local hashes used here.
- Exact-same-identity result mutability and broader replay/alternate-entry-point semantics.

## 17. Final verdict

```text
MARKET REGIME IDENTITY ADOPTION: PASS
SECTOR IDENTITY ADOPTION: PASS
SETUP/LIFECYCLE IDENTITY ADOPTION: PASS
CERI IDENTITY ADOPTION: PASS
IBMI CONTEXTUAL IDENTITY ADOPTION: PASS

T10C OVERALL: PASS
```

Production/runtime mutations performed: **NONE**.
