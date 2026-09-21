# T15D — Remaining provenance and algorithm correctness remediation

## 1. Executive verdict

PASS. All seven operation families handed off by T15C are reconciled. The four live population
paths either consume retained semantic authority or an immutable upload-run target; two obsolete
refresh routes remain retired; the ticker repair is certified as an explicit dated single-artifact
operation. No assigned family is unknown or defective.

## 2. Baselines

- Original audit: `3a9d47063be996908b7d5d1cc5769e0bbd033546`
- Phase 5: `f587b63e4e35e81486e53ffa0f13b9cd7369f963`
- T15A: `8188f1ac3e7e3a8f93ff75c2cf10019d9c5935f8`
- T15B: `0c8e5664c52ff19d80d30f33c64ade8772c955a8`
- T15C / T15D starting HEAD: `65284b3f1cc4321618bff439d53bf7f12a3d409f`
- Starting tree: `61cfdff39934d390405d447779f528aa3587aeaf`
- Migration head: `0083_winner_scope_truth`
- Branch: `codex/t15d-provenance-algorithm-remediation`

The starting worktree was clean. The T15A inventory, T15B certificate and T15C certificate SHA-256
values were respectively `caddc1a79b84bd1fd81ab131fdfe062188fe94d9ae88c45757c261f38cd2c5e4`,
`a41b3632ccbb90cfb5126b20edb6a5b7ccc9f64956c40f9ed7b05a3003cf86f3`, and
`b756ba75fc7e333be0b754e866db59721f8bbe194b7dc921fd015505b67062ec`.

## 3. T15C handoff

The machine handoff assigned exactly seven IDs: `AF_09185dfbe8dc8ae9`,
`AF_095460ea3293f382`, `AF_2071f64f2b1f231c`, `AF_9701990a1bad3828`,
`AF_be5ed7d8ee0c3e0c`, `AF_c6cbaf3f7ed57e8e`, and `AF_ddaa57ad85ff8bf7`.
Assigned 7, reconciled 7, unreconciled 0. No family was added or removed.

## 4. Finding reconciliation

The exact original defects and current residuals are recorded in
`T15D_finding_remediation_matrix.csv`. Closed after T15D: CORE-001, CORE-003, CORE-004, CERI-001,
CERI-002, RANK-001, RANK-008 and XINT-003. Explicit supported partials: CORE-005, CERI-008,
CERI-010, RANK-007, WIN-006 and INV-REVISION-001. There are no open assigned findings. Each partial
is caused by absent historical authority, not by a supported current-path defect.

## 5. Remaining operation families

The context/feature/change CERI rebuilds now use retained parent authority in durable jobs and
intersect explicit targets with retained ticker membership. A rebuild tied to an immutable upload
run uses that run's retained `RawCompanyRow` set and an empty run cannot expand to all companies.
Setup capture receives the parent pipeline's frozen tickers. The combined-ranking and technical
refresh routes remain retired. `repair_ticker` is bounded to one explicit ticker/date and is not a
population refresh.

## 6. Core provenance

New Fundamental evidence states exactly what can be proved: a content-addressed raw upload row and
the calculation/configuration identity. It separately marks provider, revision, provider time,
fiscal period and currency dimensions unavailable. It never substitutes today's metadata for an
upload-time fact.

## 7. Core algorithm remediation

CORE-003 now labels a mixed required-benchmark result with the older common source session, retains
both source dates and forces low confidence with `mixed_benchmark_sessions`. CORE-004 now compares
the bounded required price and volume windows with the US-market session calendar and reports exact
missing sessions. Weekends and exchange holidays are excluded. Existing sparse-evidence behavior
for CORE-001 remains fail closed.

## 8. CERI provider selection

PIT estimate selection first collapses economically equivalent duplicates, then groups competing
facts for one canonical business observation and executes configured provider priority, quality,
freshness and stable source-ID tie-breaking. Revision evidence retains all candidate source IDs,
the selected source/provider, selection reason and frozen calculation configuration.

## 9. CERI temporal/known-at semantics

Eligibility remains bounded by the explicit calculation cutoff and source-specific known-at
semantics. A correction retrieved after C1 cannot enter a C1 calculation; it may enter a C2
calculation. Published, observed, retrieved, ingested, effective-session and calculation-cutoff
meanings remain distinct and are not collapsed into a generic timestamp.

## 10. CERI freshness/staleness

Existing ticker/source-specific freshness behavior remains authoritative and its fresh, boundary,
stale and missing tests remain green. T15D did not replace it with provider-global freshness and did
not introduce a second numerical penalty. This previously remediated finding was regression-only.

## 11. CERI provenance/source caps

Existing warning-based confidence caps remain executable and test-covered. Provider selection now
adds exact candidate/selection provenance before confidence is computed; it does not reinterpret a
cap as a provider selector or silently turn weak provenance into high confidence.

## 12. CERI schema/revision semantics

New calculations retain source record/content identity and calculation configuration. A new source
revision under a later cutoff creates new lineage; an earlier result remains pinned. Legacy alert
rule identity that was never stored remains `LEGACY_UNKNOWN` and is not interpreted with today's
rule as if it were original.

## 13. CERI purge/hash semantics

Certified source references remain deletion protected. T15D re-certifies that semantic contents
cannot be removed without invalidation/new identity. Pre-certification purge history without a
sealed pre-purge body remains an explicit CERI-010 partial; no current hash is presented as the
missing original hash.

## 14. Ranking algorithm remediation

Ranking now persists a `NON_AUTHORITATIVE_FOR_RANKING` command-center Regime contract. Its applied
sources are Fundamental, Technical and optional IBMI liquidity; CERI, command-center Regime and
same-run Sector are explicitly excluded. RANK-001 readiness behavior remains unchanged.

## 15. Ranking temporal semantics

Supported Ranking calculations use explicit calculation business time rather than a wall clock.
The obsolete combined-ranking refresh remains retired. Legacy uploaded earnings schedules still do
not prove provider publication/retrieval or schedule revision, so RANK-007 remains partial only at
that truthful legacy boundary.

## 16. Revision identity

The supported classifications are `EXACT_REVISION`, `CONTENT_ADDRESSED_EXACT`,
`REVISION_IDENTITY_UNAVAILABLE`, and `LEGACY_UNKNOWN`. CERI source records and immutable evidence
use exact IDs/hashes. Price response manifests record each consumed bar ID/hash/revision metadata.
No later revision silently replaces an earlier historical source.

## 17. Provider provenance

CERI derived evidence is reproducible from the candidate set, selected provider/source, resolution
policy, source content and cutoff. Operational credentials and transient connection/request IDs are
not semantic inputs. Fundamental legacy uploads expose their missing provider contract rather than
fabricating one.

## 18. Currency provenance

Structured CERI estimate facts retain canonical currency and scale. No T15D path performs FX
conversion. Fundamental legacy evidence records native currency, normalized currency, rate, source
and effective time as unavailable and `conversion_applied=false`; it makes no FX claim.

## 19. Manual override lineage

Manual provider facts participate as separate immutable source records and may win only under the
frozen provider policy. Changing the policy affects a new calculation, not old evidence. Original
provider evidence is never rewritten by an override.

## 20. Remaining scope/refresh adoption

The assigned CERI background workflows carry parent `scope_id`, `refresh_cycle_id` and
`acquisition_plan_id`; batched child jobs inherit them, including finalizer-to-capture propagation.
Deployed-process fixtures bind manually seeded CERI/IB prerequisites to Pipeline authority and give
Winner capture its distinct frozen raw-row target scope. Setup capture uses parent retained tickers.
The two retired routes need no scope/refresh identity and the ticker repair is non-population. The
T15A inventory therefore has no remaining uncertified scope- or refresh-capable family.

## 21. Legacy behavior

Legacy rows remain nullable/unknown where original facts were never retained. T15D performs no
semantic backfill, production rewrite, provider invention, FX invention, rule reconstruction or
synthetic PriceBar revision creation.

## 22. Algorithm delta analysis

Expected changes are machine-recorded in `T15D_algorithm_delta_certification.json`: provider policy
can change the selected EPS fact; canonical price/volume basis can change volume ratio; mixed
benchmark evidence changes effective date/confidence; an internal trading-session gap changes
readiness. Ranking's explicit negative-dependency evidence changes while its numeric score remains
8.0 in the control. No unexpected numeric change was observed.

## 23. Performance

The PostgreSQL 1/50 CERI capture, pipeline and material gates retain the certified bounds 29/29,
70/70 and 29/29. Change rebuild retains its `<=12` authority-SELECT budget for populations 1/50.
Feature rebuild is 13/13 after the one required set-based retained-scope membership read (the prior
source-only graph was 12/12). Scope membership and OHLCV session loading are set-based; no
per-company provenance query was introduced. T15A separately retains 1/50/200 authority scaling
coverage.

## 24. PostgreSQL certification

Tests ran against disposable PostgreSQL 18.6 on host port 55432. Fresh migration to the single head,
Alembic drift check, T15A/T15B/T15C identity suites, T15D retained-scope attack, CERI performance,
source-bundle, score/change integrity, configuration-delivery resume and Phase-4 process-restart
suites passed. The 3,952-test repository collection was covered in ordered fail-fast segments after
fixture corrections; the unavailable external browser smoke cases remained expected skips. No
authoritative database was migrated or mutated.

## 25. Business correctness/parity

Affected defective cases change only as documented by finding. Aligned benchmarks, complete OHLCV,
single-provider estimates, TRADES-only price data and the Ranking profile score are stable controls.
No cross-domain drift or unexplained financial delta is accepted.

## 26. Negative dependencies

The certified graph still excludes CERI→Ranking, CERI→Setup, CERI→Winner, Setup→Winner,
Lifecycle→Winner, IBMI→Winner direct and Sector→same-run Ranking. The new Ranking evidence makes two
of these exclusions explicit without adding an input edge.

## 27. Phase-5 regression

All writes continue through the certified calculation identity, immutable evidence, readiness,
effective configuration, mutation domain and execution-fencing boundaries. No helper bypasses a
certified writer and no production backfill was performed. The finite T14D source pins and derived
252-caller/220-family operation certificate were regenerated from the reviewed T15D delta with zero
incomplete families.

## 28. T15A/B/C regression

T15A scope identity, T15B Pipeline/CERI adoption and T15C Winner truth/scope PostgreSQL tests remain
green. Same-refresh retry reuses authority, later refresh remains distinct, and WIN-006's explicit
unavailable birth-revision state is unchanged.

## 29. Finding status after T15D

Fourteen reconciled findings/invariants are listed in the matrix: 8 closed, 6 partial, 0 open.
Partial does not mean an unhandled current defect: each residual identifies absent historical
provider/rule/revision/currency authority that cannot be truthfully reconstructed.

## 30. Phase-6 invariant status

- `INV-SCOPE-001`: enforced repository-wide for the inventoried scope-capable operations.
- `INV-REFRESH-001`: enforced repository-wide for the inventoried refresh-capable operations.
- `XINT-012`: closed.
- `INV-REVISION-001`: partial at exact enumerated legacy/unavailable boundaries.
- `WIN-006`: partial because some birth bars never had a PriceBarRevision primary key.

## 31. T15E exact handoff

`T15D_T15E_exact_handoff.json` carries every Phase-6 family/invariant, finding status, algorithm delta
fixture, regression lane and remaining Phase-7 boundary. T15E needs to verify closure artifacts; it
does not need to rediscover Phase 6.

## 32. Phase-7 boundary

`SETUP-006`, `SETUP-007` and `SETUP-010` original-context reconstruction remain Phase 7. Historical
rules or context may be reconstructed only from exact retained authority, never from current state.
External provider-contract governance also remains outside the Phase-6 implementation boundary.

## 33. Residual risks

Historical Fundamental uploads lack per-value provider/revision/time/fiscal/currency facts; some
legacy CERI alert/purge histories lack original rule/pre-purge identity; some Winner birth bars lack
revision primary keys. These are explicit partials with fail-closed/no-substitution behavior. There
is no unknown residual within the seven assigned operation families.

## 34. Final verdict

T15D PASS: assigned 7, reconciled 7, unreconciled 0, defects 0, unknown 0. Migration required: NO.
Migration head remains `0083_winner_scope_truth`. Production/runtime mutations: NONE.
