# T11B-3 — IBMI Immutable Constituent Evidence

## 1. Baseline

| Item | Value |
|---|---|
| Repository | `C:\Users\Ivica\Documents\SwingLens` |
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| T11A | `5d2df19c0ef4ff3e7f49f17bd7889378021bfb31` |
| T11B-1 | `bef1abe285779c3aa278e3691a9438126f5ccedc` |
| T11B-2 / starting HEAD | `f5bfa546ac5d03e641eb67473b21e7a04629cb23` |
| Branch | `codex/t11b3-ibmi-immutable-evidence` |
| Migration head before | `0077_ceri_decision_evidence` |
| Migration head after | `0078_ibmi_constituent_evidence` |
| Initial worktree | clean |

No production database, provider, brokerage connection, pipeline, notification, merge, or
push was used.

## 2. Preserved decision graph

```text
IBMI liquidity ----------------------> Ranking
IBMI volatility / short pressure ---> CERI
IBMI --------------------------------X Winner
```

No scoring formula or dependency direction changed. Scanner output still supplies discovery
and universe context, not an IBMI feature score. Histogram remains a standalone feature and
is not promoted to Ranking, CERI, or Winner.

## 3. Source inventory and classification

| Source family | Classification before evidence seal | T11B-3 treatment |
|---|---|---|
| `IBHistoricalMetricBar` | `MUTABLE_CURRENT` | Exact state is addressed by bar ID, data hash, revision number, and immutable revision ID |
| `IBHistoricalMetricRevision` | `APPEND_ONLY` / `VERSIONED` | Revision zero now records initial state; correction rows 1..N address every later state |
| `IBMarketIntelligenceSnapshot` | `LATEST_OBSERVATION` / append | Exact selected snapshot ID, timestamp, values, availability, request, and evidence hash are frozen |
| Shortability/availability snapshots | `LATEST_OBSERVATION` | Latest-at-calculation is converted to exact observation IDs, including the bounded local share history used for change |
| Historical request availability | operational row, effectively `MUTABLE_CURRENT` while running | Exact completed request-item observation is frozen when IV availability affects the feature |
| Liquidity spread inputs | metric `MUTABLE_CURRENT` + revisions | Exact metric revision states are frozen |
| Liquidity dollar-volume input | `DERIVED` from PIT PriceBar | Exact price/volume rows, roles, bases, state identity, and series fingerprint are frozen |
| Volatility inputs | metric `MUTABLE_CURRENT` + revisions | Exact HV/IV metric revision states and IV availability observation are frozen |
| Short-pressure inputs | metric revisions + `LATEST_OBSERVATION` | Fee states plus exact shortability observations are frozen |
| Options activity | `LATEST_OBSERVATION` | Exact options snapshot is frozen when this feature is calculated |
| Histogram | append snapshot/bins; latest UI projection | Exact histogram snapshot, raw distribution, derived bins, availability, and PIT reference price are frozen |
| Scanner parameter/results | append/cache; discovery-only | Inspected and left outside feature evidence because they do not produce the audited liquidity/volatility/short-pressure decision values |
| `PriceBar` | `MUTABLE_CURRENT` | Existing PIT projection is reused; no competing market-data version system was created |
| `PriceBarRevision` | `APPEND_ONLY` / `VERSIONED` | Existing revision ID is included when the selected state has one; otherwise canonical bar ID/revision/data-hash/value identity is sufficient |
| `IBIntelligenceFeature` | `DERIVED`, version-keyed current/history table | New certified rows point to independent immutable evidence; null pointer remains legacy |

## 4. Immutable feature envelope

`CoreCalculationEvidence(kind=IBMI)` is the `IbmiFeatureEvidence` envelope. It contains:

- the complete immutable derived feature output;
- the Phase-1 Calculation Identity and fingerprint;
- source/config/calculation versions and the complete resolved IBMI configuration;
- exact historical metric state and revision IDs;
- exact live, availability, shortability, options, and histogram observation IDs where used;
- the exact PIT PriceBar series/state representation where used;
- a constituent fingerprint added to the feature source lineage and input signature; and
- the immutable payload fingerprint/evidence key.

The feature's `evidence_id` is a nullable compatibility pointer. The generic current
projection is keyed by ticker and module, so a later feature can become preferred without
modifying any older envelope.

## 5. Metric version semantics

The existing IB metric revision system is extended, not replaced. New initial metric rows
create revision `0`, with the initial hash/values as both sides of the state record. Provider
corrections continue to create transition rows `1..N`; each row's `new_data_hash` and
`new_values_json` identify the resulting immutable state.

```text
M1 current + revision 0 -> F1 -> IBMI evidence E1(revision 0)
correction M2/revision 1 -> F2 -> IBMI evidence E2(revision 1)
```

Rows predating migration `0078` receive no fabricated revision-zero row. If their exact
state cannot be resolved, the new feature remains non-certified rather than inventing
lineage.

## 6. Live and availability observations

Live source selection remains bounded by feature session and cutoff. Evidence construction
receives the actual selected rows, not a request to find “latest” later. Short pressure
freezes the selected shortability observation and the exact bounded observation sequence
used to compute local share change. Options activity freezes its selected live snapshot.
Volatility freezes the exact completed request item when entitlement/availability affects
the output.

## 7. Price evidence

Liquidity still calculates 20-session median dollar volume from preferred adjusted close
and trade volume. The evidence collector applies the same basis/coverage rule and freezes
only the intersecting rows actually consumed, with `PRICE_CLOSE` and `TRADE_VOLUME` roles.
Histogram reference price is now selected through the existing PIT projection and its exact
row is frozen. A post-cutoff PriceBar correction cannot reinterpret either feature.

## 8. Retry versus refresh

The constituent fingerprint participates in `input_signature` and Calculation Identity
source lineage.

- An exact retry with the same selected facts, configuration, output, and cutoff reuses the
  same feature/evidence deterministically.
- A later observation, correction, availability item, or PriceBar series changes the
  constituent fingerprint and creates new evidence, even if the numeric score is unchanged.
- Existing deterministic request-key behavior is unchanged. Any legitimate-refresh
  suppression remains `INV-REFRESH-001` Phase-6 work.

## 9. Consumer pinning

Ranking accepts an IBMI liquidity overlay from a real database session only when the feature
has certified evidence. Ranking evidence then adds the `ibmi_liquidity` immutable source
edge. Advancing the IBMI current projection does not change an old Ranking edge.

CERI applies the same certified-evidence gate to volatility and short pressure. Its evidence
graph adds `ibmi_volatility` and/or `ibmi_short_pressure` edges, and its source manifest
records the IBMI evidence ID/key, immutable payload fingerprint, constituent fingerprint,
and Phase-1 identity. The T11B-2 provisional marker is replaced by `CERTIFIED_T11B3`.

## 10. Historical and legacy reads

`get_ibmi_evidence(evidence_id)` reads only the immutable ledger row. It never re-runs a
latest metric/snapshot query. `get_certified_ibmi_evidence(feature)` follows only the
feature's pinned evidence ID and validates kind, scope, and Phase-1 identity.

Existing features receive `evidence_id = NULL`; query output labels them
`LEGACY_CURRENT/LEGACY_UNKNOWN`. They remain available as explicit current compatibility
data but cannot satisfy certified historical lookup or feed new Ranking/CERI evidence in a
real database path. There is no broad backfill.

## 11. Migration

Migration `0078_ibmi_constituent_evidence`:

- adds `IBMI` to the shared immutable-evidence/current-projection kind checks;
- adds nullable indexed `ib_intelligence_features.evidence_id` with `ON DELETE SET NULL`;
- preserves every existing feature as legacy current; and
- performs no production rewrite or inferred backfill.

Offline PostgreSQL upgrade/downgrade DDL passes. Disposable PostgreSQL runtime execution was
attempted but the local administrator rejected authentication, so the complete Phase-2
runtime migration chain remains deferred to T11E.

```text
Production rewrite: NO
Legacy backfill: NO
POSTGRESQL PHASE-2 MIGRATION CERTIFICATION: DEFERRED
```

## 12. Verification

| Lane | Result |
|---|---|
| T11B-3 focused | `7 passed, 1 skipped, 1 warning` |
| Integrated T11B contextual evidence | `24 passed, 3 skipped, 1 warning` |
| IBMI + Ranking/CERI affected regression | `540 passed, 2 skipped, 1 warning` |
| T11A immutable evidence | `3 passed, 1 skipped, 1 warning` |
| Phase-1 identity | `134 passed, 1 warning` |
| Phase-0 safety | `81 passed, 1 warning` |
| Broader safe repository lane | `2,551 passed, 128 skipped, 118 deselected, 23 warnings` |

The focused skip and the contextual skips are the disposable PostgreSQL authentication
failure. The broader skips are repository-defined unavailable PostgreSQL/optional runtime
cases under a lane excluding external, E2E, slow, and destructive tests. Warnings are the
existing Starlette/httpx, Alembic configuration, and Python 3.12 SQLite adapter warnings.

## 13. Verdict

**T11B-3 PASS.** New IBMI feature evidence proves which exact metric versions,
observations, availability state, and PIT price facts produced the output. Corrections and
new observations advance current truth without rewriting old evidence; Ranking and CERI pin
the exact IBMI evidence consumed; legacy features fail certified historical use; and no
IBMI-to-Winner edge was introduced.
