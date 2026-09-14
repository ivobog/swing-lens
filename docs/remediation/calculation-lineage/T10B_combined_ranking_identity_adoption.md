# T10B — Calculation Identity Adoption for Combined and Ranking

## 1. Baselines

| Item | Value |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T09A | `2e0487a271d425b30fb61b17a33717562e0abe3b` |
| T09B | `c7b8df7f25fe709b0fa7b20bf2fe3b738afcfb29` |
| T09C | `bbb345b3257d0df572db425242c62ea1447e5d24` |
| T10A / T10B starting HEAD | `e08bf42a527c1345883e5587af794a7396443a7e` |
| Branch | `codex/t10b-combined-ranking-identity-adoption` |
| Final HEAD | The focused T10B commit containing this report; the exact SHA is recorded in the final response because a commit cannot contain its own SHA. |
| Alembic head before/after | `0074_ceri_evidence_quarantine` |
| Python | 3.12.2 from `.venv` |

The starting HEAD exactly matched the required T10A remediation base. There were no tracked
modifications. Pre-existing untracked architecture/audit registry files and IB historical
probe files were preserved and were not treated as implementation drift.

## 2. Findings

| Finding/invariant | T10B status | Basis |
|---|---|---|
| `RANK-004` | **CLOSED** | Combined and Ranking now require named-policy compatibility across pipeline, context, session, cutoff, calendar, and ticker in addition to run; required source config/version/lineage must be known. |
| `XINT-001` | **PARTIALLY_CLOSED** | The canonical contract is adopted at Fundamental/Technical → Combined/Ranking, but Setup, CERI, Winner, Sector, Regime, and other edges remain. |
| `INV-IDENTITY-001` repository-wide | **PARTIAL** | The in-scope boundaries enforce the invariant; repository-wide adoption is intentionally incomplete. |
| `RANK-003` | **PARTIALLY_CLOSED** | Ranking omits optional IBMI unless its session/calendar/config/versions/source identity and bounded cutoff are compatible. Broader IBMI context authority is not redesigned. |
| `RANK-005` | **PARTIALLY_CLOSED** | Complete effective profile configuration is hashed and embedded in Ranking identity. Dedicated immutable/versioned Ranking storage remains deferred. |
| `RANK-006` | **PARTIALLY_CLOSED** | Incompatible mutable upserts fail before mutation and legacy rows are explicitly replaced; same-identity updates remain mutable by design. |
| `CORE-005` | **PARTIALLY_IMPROVED** | New pipeline-produced Fundamental and Technical rows expose config/version/source identity, but core acquisition/history is not redesigned. |

Readiness findings `CORE-009`, `RANK-001`, and `RANK-002` were not remediated. An
identity-compatible TechnicalScore can still be readiness-insufficient.

## 3. Source identity inventory

The table describes newly pipeline-produced artifacts. A pre-T10B row without the embedded
payload is represented by `CalculationIdentity.legacy_unknown`; none of its missing fields is
reconstructed.

| Artifact | Ownership | Subject | Pipeline/context | Temporal | Configuration | Algorithm/model | Source lineage | Generation |
|---|---|---|---|---|---|---|---|---|
| `FundamentalScore` | run `KNOWN` | ticker `KNOWN`; company ID N/A | pipeline/context/fingerprint `KNOWN` from explicit frozen pipeline context | session/cutoff/calendar `KNOWN` as the calculation binding | complete fundamentals config hash `KNOWN` | persisted scoring model/calculation version `KNOWN` | exact `RawCompanyRow` ID (or content identity before an ID exists) and canonical payload hash `KNOWN` | N/A |
| `TechnicalScore` | run `KNOWN` | ticker `KNOWN`; company ID N/A | pipeline/context/fingerprint `KNOWN` | persisted session/cutoff/calendar `KNOWN` | canonical hash of complete Pine/V4/V5 semantic config and active flags `KNOWN` | technical calculation/engine version `KNOWN` | canonical temporal/source-session input envelope `KNOWN`; underlying price-row immutability is outside its proof boundary | N/A |
| IBMI liquidity | upload/pipeline ownership N/A because the feature is global reusable evidence | ticker `KNOWN` | market context N/A | session/cutoff/calendar `KNOWN` when persisted; missing cutoff/calendar is `UNKNOWN` | persisted complete IBMI config hash `KNOWN` | calculation and source versions `KNOWN` | persisted source-evidence hashes `KNOWN`, otherwise `UNKNOWN` | N/A |
| `CombinedResult` | copied validated run/pipeline | ticker copied from validated pair | copied validated Technical/Fundamental context | copied validated session/cutoff/calendar | complete Combined config fingerprint | Combined calculation/engine version | exact Fundamental/Technical artifact IDs and identity fingerprints plus cohort fingerprint | N/A |
| `RankingResult` | copied validated run/pipeline | ticker copied from validated pair | copied validated Technical/Fundamental context | copied validated session/cutoff/calendar | complete profile plus shared earnings-risk config fingerprint | Ranking engine version | exact per-row source IDs/identity fingerprints plus compact whole-cohort fingerprint; IBMI reference only when used | N/A |

Technical source lineage deliberately claims only the persisted input envelope available at
this layer. It does not mislabel the feature-cache signature as a complete final-score source
revision contract. Full price-revision adoption is residual work.

## 4. Compatibility policies

### `COMBINED_INPUT_COMPATIBILITY`

- Exact comparisons: run, pipeline, ticker, market-context ID/fingerprint, as-of session,
  calculation cutoff, and calendar identity.
- Intrinsic requirements on each source: structurally valid identity plus `KNOWN` effective
  configuration, calculation version, and source lineage.
- N/A is not permitted for any shared comparison. Unknown and legacy-unknown never pass.
- Algorithm/config/source values are required on both artifacts but are not compared to each
  other because Fundamental and Technical are different calculators. The result instead
  references both exact identities.
- Every ticker in the Combined cohort must also share one pipeline/context/temporal spine.

### `RANKING_INPUT_COMPATIBILITY`

- Uses the same exact shared-source comparisons and intrinsic requirements as Combined.
- Ranking validates its own Fundamental/Technical pair; it does not reuse Combined identity.
- Every ticker in a profile cohort must share one pipeline/context/temporal spine.
- Profile configuration is not compared to source calculators; it becomes the output's own
  complete effective configuration identity.

### `RANKING_IBMI_COMPATIBILITY`

- Exact comparisons: ticker, as-of session, calendar, current complete IBMI config hash,
  calculation version, and source version.
- The IBMI cutoff must be timezone-aware and at or before the Ranking cutoff. Future evidence
  is rejected even if all exact dimensions match.
- Global IBMI ownership and market-context fields must be N/A on both expected and actual
  identities; they cannot masquerade as run/context proof.
- Source lineage is required and must be known. Any unknown or legacy-unknown requirement
  yields insufficient identity.
- The source is optional. An absent or rejected source is omitted; Ranking continues without
  the overlay and the output identity contains no IBMI reference.

No ad-hoc ignore flags were added. All consumer decisions are named and testable.

## 5. Combined adoption

The full pipeline passes its explicit frozen `MarketCalculationCutoff` and pipeline ID to the
Fundamental and Technical producers. Each producer embeds a canonical T10A payload and
fingerprint in its existing `debug_json`.

`refresh_combined_results` loads the exact same-run rows, but does not accept that lookup as
compatibility proof. For each ticker it deserializes and fingerprint-checks both identities,
validates the named policy, verifies an explicitly supplied pipeline context when present,
and verifies cohort coherence. Missing, unknown, legacy, structurally invalid, or mismatched
identity raises `CALCULATION_IDENTITY_REJECTED` before calculation or persistence.

The output identity has Combined's own effective config and calculation version. Its source
lineage references the exact FundamentalScore and TechnicalScore IDs/fingerprints and a
compact cohort fingerprint. Earnings calculation uses the validated as-of session, not wall
clock, on this production boundary.

## 6. Ranking adoption

Ranking independently performs the same Fundamental/Technical validation under
`RANKING_INPUT_COMPATIBILITY`, verifies a single cohort context, and uses the validated
session as the earnings evaluation date. A caller-supplied evaluation date that differs from
identity is rejected.

Each profile's complete dataclass configuration, including weights, components, missing-data
policy, thresholds, penalties, gates, and tradeability settings, is hashed together with the
shared earnings-risk configuration. The output uses Ranking's own engine version and exact
source identities. Different profiles or effective configurations therefore fingerprint
differently.

The globally selected, temporally bounded IBMI candidate is passed through
`RANKING_IBMI_COMPATIBILITY` before the engine sees it. Compatible evidence is included in
the result's source lineage. Absent or incompatible evidence is omitted, logged at debug
level with policy/status/fingerprint/failed dimensions, and Ranking preserves its existing
optional-overlay behavior.

## 7. Persistence behavior

Both result tables retain identity in existing non-null JSON metadata; no schema change is
needed.

- Combined preflights every desired row against existing identity-aware rows before clearing
  Winner references or deleting results. Same identity may be recalculated. A different
  identity under `(run_id, ticker)` raises a persistence conflict. An identity-less legacy
  row is explicitly deleted and replaced by a new identity-aware row.
- Ranking preflights the complete desired batch before mutating any row. Same identity may
  update the existing row. Different identity under `(run_id, profile, ticker)` raises a
  persistence conflict. A legacy row is deleted/flushed, then a new identity-aware row is
  inserted; its old object is not silently upgraded.

The compact cohort fingerprint means a cross-sectional rank cannot be treated as the same
calculation when another participating source identity changes.

## 8. Legacy handling

Rows without `debug_json.calculation_identity` are mapped to `LEGACY_UNKNOWN` for every
unrecoverable dimension. Shared unknowns never compare equal. Legacy source rows therefore
cannot feed current Combined or Ranking calculations. Legacy result rows cannot satisfy an
identity-aware read/persistence comparison and are explicitly replaced during an authorized
current recalculation; no historical context/config/source value is guessed and no backfill
is performed.

## 9. Tests

| Contract/finding | Focused evidence |
|---|---|
| Real Fundamental/Technical producers | `test_fundamental_and_technical_producers_persist_context_bound_identities` |
| Combined compatible path/output/exact sources | `test_combined_compatible_sources_persist_identity_and_exact_source_refs` |
| Raw row still matches Fundamental lineage | `test_combined_rejects_raw_row_changed_after_fundamental_identity` |
| Same run/ticker but context/session/cutoff mismatch | parameterized Combined rejection test and Ranking context rejection test |
| Unknown and legacy do not pass | `test_combined_unknown_and_legacy_identity_fail_closed` |
| Fingerprint stability/material sensitivity | `test_combined_identity_fingerprint_is_stable_and_materially_sensitive` |
| Combined incompatible replacement | `test_identity_incompatible_combined_replacement_is_rejected` |
| Ranking compatible/profile-distinct identity | `test_ranking_compatible_inputs_and_profile_config_produce_distinct_identity` |
| Optional IBMI absent | `test_optional_ibmi_absent_is_not_a_ranking_failure` |
| Optional IBMI compatible | `test_optional_compatible_ibmi_is_used_and_included_in_identity` |
| T09A-safe but identity-wrong IBMI | `test_temporally_safe_but_config_wrong_ibmi_is_omitted` |
| Ranking incompatible upsert | `test_identity_incompatible_ranking_upsert_is_rejected` |
| Legacy Ranking replacement | `test_legacy_ranking_row_is_explicitly_replaced_not_upgraded` |

Existing Ranking service and golden-pipeline fixtures now create explicit test identities;
they do not receive a compatibility bypass.

## 10. Verification

| Lane | Exact scope | Result |
|---|---|---|
| T10B focused | `tests/test_combined_ranking_identity_adoption.py` | **16 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| CalculationIdentity infrastructure | `tests/test_calculation_identity.py tests/test_calculation_identity_adoption.py` | **38 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Affected subsystems | 32 explicitly enumerated Fundamental/Technical/Combined/Ranking/IBMI files plus session/context and T10A adoption; marker exclusions applied | **349 passed, 0 failed, 0 skipped, 6 deselected, 1 warning** |
| Phase-0 regressions | IBMI/CERI/Setup temporal containment, domain fence, Winner generation/estimate publication fences | **70 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** |
| Broader clean lane | `tests` with exact ignores/markers in §11 | **2,467 passed, 0 failed, 7 skipped, 33 deselected, 22 warnings** |

Static verification: Ruff passes on every changed Python file and `git diff --check` passes.
The repeated single focused warning is the existing Starlette/httpx deprecation. The broader
lane also has 21 existing Python 3.12 SQLite datetime-adapter deprecations.

During test selection, one intended affected-file command used an unsupported `rg`
look-ahead, leaving its file array empty. Pytest began the marker-filtered repository
collection and was interrupted at 19% as soon as the selection error was visible. It
produced no certification result and made no application/database/provider state change.
Every reported verification total above comes from a subsequently validated, non-empty,
explicitly excluded command.

## 11. Test exclusions

The clean command is:

```text
.venv\Scripts\python.exe -m pytest tests \
  --ignore=tests/e2e \
  --ignore=tests/integration \
  --ignore=tests/ib_market_intelligence/test_external_smoke.py \
  -m "not external and not e2e and not slow" -q
```

- Live external/provider: `tests/ib_market_intelligence/test_external_smoke.py`, IBKR/IB
  Gateway, SEC/provider/network/credential tests, marker `external`.
- Browser/E2E: `tests/e2e/`, Playwright, Selenium, full-webapp/browser flows, markers
  `e2e` and `slow`.
- PostgreSQL-dependent: `tests/integration/`, disposable/live PostgreSQL credentials,
  containers, and PostgreSQL concurrency certification.

## 12. Migration/data impact

| Item | Required? |
|---|---|
| Migration | **NO** |
| Production data rewrite | **NO** |
| Legacy identity backfill | **NO** |

Identity is stored in existing JSON metadata. T10B does not alter schemas, run migrations,
rewrite data, or fabricate legacy values.

## 13. Residual risks

Self-review classification of every in-scope selection/join:

| Boundary | Classification | Reason |
|---|---|---|
| RawCompanyRow → Fundamental/Combined/Ranking | `IDENTITY_ENFORCED` | Current raw row must match the exact reference/fingerprint committed by Fundamental identity. |
| FundamentalScore ↔ TechnicalScore → Combined | `IDENTITY_ENFORCED` | Named policy, intrinsic proof checks, explicit context check, and cohort check run before calculation. |
| FundamentalScore ↔ TechnicalScore → Ranking | `IDENTITY_ENFORCED` | Ranking performs its own validation and cohort check. |
| Global IBMI liquidity → Ranking | `OPTIONAL_AND_IDENTITY_VALIDATED` | Only a bounded, profile-relevant, identity-compatible feature reaches the engine. |
| Pre-T10B source artifacts | `LEGACY_FAIL_CLOSED` | Legacy-unknown required dimensions cannot pass either primary policy. |
| Pre-T10B result rows | `LEGACY_FAIL_CLOSED` | They cannot prove compatibility and are explicitly replaced by new rows, never upgraded by guessed metadata. |
| Display/export getters for Combined/Ranking | `OUT_OF_SCOPE` | They publish persisted values but do not perform a calculation-consuming source join. |
| Setup/CERI/Winner/Sector/Regime consumers | `OUT_OF_SCOPE` | Deferred to the named later tasks; no T10B compatibility claim is made. |

- Setup/Lifecycle, CERI, Winner acquisition, Sector Rotation, and Market Regime do not yet
  uniformly consume CalculationIdentity; these are later adoption boundaries.
- Technical lineage's current proof boundary records the persisted temporal/source-session
  envelope, not immutable price-row revisions. Core source-revision adoption remains.
- IBMI is globally reusable and has no MarketCalculationContext identity; T10B therefore
  requires explicit N/A and current config/version compatibility rather than inventing a
  context.
- Result storage remains mutable for the exact same calculation identity. Full immutable
  evidence/versioning is a later phase.
- Central repository-wide resolved configuration authority is not introduced. T10B hashes
  the complete effective configurations already available at these calculators.
- Manual/legacy paths that did not produce source identities now fail closed until they run
  through an explicit context-bound producer path.

## 14. Certification verdict

```text
COMBINED IDENTITY ADOPTION: PASS
RANKING IDENTITY ADOPTION: PASS
OPTIONAL IBMI IDENTITY ENFORCEMENT: PASS

RANK-004: CLOSED
XINT-001: PARTIALLY_CLOSED
INV-IDENTITY-001 repository-wide: PARTIAL

T10B OVERALL: PASS
```

No production pipeline, provider, IBKR session, job, migration, Winner publication,
maturation/cohort/rescore flow, or production data mutation was performed.
