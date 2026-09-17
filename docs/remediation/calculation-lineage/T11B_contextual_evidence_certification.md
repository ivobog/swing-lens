# T11B — Contextual Immutable Evidence Certification

## 1. Certification baseline

| Stage | Commit |
|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T11A core evidence | `5d2df19c0ef4ff3e7f49f17bd7889378021bfb31` |
| T11B-1 Regime/Sector | `bef1abe285779c3aa278e3691a9438126f5ccedc` |
| T11B-2 CERI | `f5bfa546ac5d03e641eb67473b21e7a04629cb23` |
| T11B-3 branch start | `f5bfa546ac5d03e641eb67473b21e7a04629cb23` |
| Migration head | `0078_ibmi_constituent_evidence` |

## 2. Integrated evidence matrix

| Artifact | Immutable evidence | Current projection | Exact sources pinned | Legacy behavior | Historical read | Consumer | Certification |
|---|---|---|---|---|---|---|---|
| Regime | `CoreCalculationEvidence(REGIME)` freezes output, identity, bounded-frame fingerprints, config/version | Generic projection plus compatibility snapshot current revision | Actual bounded benchmark/participation/leadership source envelope | Null evidence remains current/unknown and cannot satisfy evidence lookup | Identity/evidence-only; no latest fallback | Sector, Setup, Winner receive exact Regime evidence ID where consumed | `PASS` |
| Sector | `CoreCalculationEvidence(SECTOR)` freezes snapshot and every sector row | Generic projection plus compatibility current revision | All Ranking evidence, selected Regime evidence, exact prior Sector evidence | Cannot act as certified predecessor/source | Identity/evidence-only; no current fallback | Setup and Winner receive exact Sector evidence ID | `PASS` |
| CERI | `CoreCalculationEvidence(CERI)` freezes decision, normalized source manifest, PIT price meaning, complete rules/config | Generic projection plus append-like compatibility snapshot | Provider/SEC/estimate/earnings/guidance/catalyst/price evidence and exact IBMI evidence edges | Cannot satisfy certified lookup; legacy IBMI is omitted/rejected | Identity/evidence-only; no latest provider/IBMI reinterpretation | Operational change/alert paths only; no Ranking/Setup/Winner edge | `PASS` |
| IBMI | `CoreCalculationEvidence(IBMI)` freezes output, identity, config, and exact constituent manifest | Generic ticker+module projection plus versioned feature rows | Metric revision IDs, live/availability IDs, PIT PriceBar identity, options/histogram sources where used | `LEGACY_CURRENT/LEGACY_UNKNOWN`; cannot feed certified Ranking/CERI evidence | Direct immutable evidence ID; no latest metric/snapshot lookup | Liquidity → Ranking; volatility/short pressure → CERI; no Winner edge | `PASS` |

## 3. Integrated invariants

### New contextual evidence is immutable

Regime, Sector, CERI, and IBMI all use the T11A append-only ledger. ORM update/delete guards
protect ledger rows and immutable graph edges. Compatibility/current rows may change or be
removed without changing historical evidence payloads.

### Current projections advance independently

Projection uniqueness covers run/ticker, run/context, global/context, and global
ticker/module scopes. A new contextual calculation advances only the appropriate pointer;
old evidence remains addressable by ID or Calculation Identity.

### Exact sources remain pinned

- Sector pins every Ranking source, its Regime, and its exact earlier Sector predecessor.
- CERI freezes its normalized decision source manifest and adds exact IBMI evidence edges.
- IBMI freezes exact metric-state revisions, observations, availability, and PIT price
  evidence.
- Later provider/current truth creates new evidence and cannot reinterpret an old decision.

### Legacy fails closed for certification

No migration fabricates source history. Null evidence pointers remain explicit compatibility
state. Certified historical readers and new contextual consumer paths require evidence, so a
legacy hash or current row cannot masquerade as immutable proof.

### Phase-1 compatibility remains authoritative

Evidence availability is an additional gate, not a replacement for Calculation Identity.
The full explicit Phase-1 lane passed `134` tests, including core adoption, contextual
compatibility, Ranking/Combined, repository certification, and Winner acquisition identity.

## 4. Dependency certification

```text
Ranking evidence -> IBMI liquidity evidence
CERI evidence    -> IBMI volatility / short-pressure evidence

CERI -X-> Ranking
CERI -X-> Setup/Lifecycle
CERI -X-> Winner
IBMI -X-> Winner
```

No direct negative edge was introduced. Setup/Lifecycle/Alert evidence remains T11C.

## 5. Finding and invariant reconciliation

| Finding/invariant | Status after T11B | Evidence |
|---|---|---|
| `XINT-005` | `PARTIALLY_REMEDIATED` repository-wide; closed for new T11A core + Regime/Sector/CERI/IBMI evidence paths | Independent immutable ledger, explicit current pointers, exact contextual source edges, fail-closed legacy lookup |
| `INV-EVIDENCE-001` | `PARTIALLY_ENFORCED` repository-wide; `ENFORCED` for new core, Regime, Sector, CERI, and IBMI calculations | Immutable payload/key/identity and source graph tests; correction/retry/legacy cases pass |
| `INV-TRUTH-001` | `PARTIALLY_ENFORCED` repository-wide; `ENFORCED` for the certified T11B contextual boundary | Provider corrections, current-row changes, live observations, PriceBar revisions, refreshes, and projection movement do not change old evidence |
| `INV-IDENTITY-001` | `PRESERVED` | Phase-1 lane `134 passed` |

Repository-wide historical-read authority, Setup/Lifecycle/Alert state, publication
semantics, and privileged operational SQL remain outside this certification.

## 6. Migration certification

The Phase-2 chain is:

```text
0075_core_immutable_evidence
-> 0076_regime_sector_evidence
-> 0077_ceri_decision_evidence
-> 0078_ibmi_constituent_evidence
```

Every T11 migration compiles as PostgreSQL DDL with schema assertions. The disposable
PostgreSQL runtime chain was attempted again in T11B-3 and could not authenticate as the
configured local administrator. No authoritative or production database was used.

**POSTGRESQL PHASE-2 MIGRATION CERTIFICATION: DEFERRED.** T11E must close this debt.

## 7. Test certification

| Lane | Result |
|---|---|
| T11B-3 focused | `7 passed, 1 skipped, 1 warning` |
| T11B-1 + T11B-2 + T11B-3 contextual regression | `24 passed, 3 skipped, 1 warning` |
| IBMI / Ranking / CERI affected regression | `540 passed, 2 skipped, 1 warning` |
| T11A evidence regression | `3 passed, 1 skipped, 1 warning` |
| Phase-1 identity regression | `134 passed, 1 warning` |
| Phase-0 safety regression | `81 passed, 1 warning` |
| Broader safe lane | `2,551 passed, 128 skipped, 118 deselected, 23 warnings` |

There were no failures. PostgreSQL runtime cases skipped on authentication; live brokerage
and external mutation lanes were not enabled. The broader command explicitly excluded
external, E2E, slow, and destructive markers.

## 8. Deferred and residual scope

- T11C: Setup/Lifecycle/Alert immutable evidence.
- T11D: repository-wide historical-reader enforcement.
- T11E: full Phase-2 integration and disposable PostgreSQL migration certification.
- Phase 6: `INV-REFRESH-001` request-key/legitimate-refresh semantics.
- Privileged direct-SQL governance remains an operational control concern.

## 9. Overall verdict

**T11B PASS.** Regime, Sector, CERI, and IBMI now have immutable evidence separate from
current projections; exact contextual predecessors and decision-relevant constituents are
pinned; provider/current corrections cannot reinterpret old decisions; legacy rows cannot
masquerade as certified evidence; and Phase-1 compatibility remains green.

Production/runtime mutations: **NONE**. Production rewrite: **NO**. Legacy backfill:
**NO**. Merge/push: **NONE**.
