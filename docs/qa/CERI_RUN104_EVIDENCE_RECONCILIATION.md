# CERI Run 104 Evidence Reconciliation

## Population result

The supplied export and production database each contain 177 unique Run 104
tickers. Opportunity coverage recomputes from the configured weights of
available components for all 177 snapshots; there are no stored coverage
mismatches.

Every available component has direct selected IDs except `surprise_trend`.
`surprise_trend` is an aggregate of accepted historical earnings and therefore
uses the explicit `AGGREGATE_COMPONENT_NO_DIRECT_EVIDENCE_IDS` lineage
exemption. No other available component needs a missing-lineage exemption.

## XPEL and CBL

| Ticker | Stored coverage | Available components | Direct component IDs | Disposition |
|---|---:|---|---:|---|
| XPEL | 30% | revision breadth 15%; surprise trend 15% | breadth 12; surprise aggregate exemption | FIXED |
| CBL | 35% | revision breadth 15%; surprise trend 15%; price response 5% | breadth 6; price response parent 1; surprise aggregate exemption | FIXED |

The root cause of the apparent zero/tiny selected count was twofold:

1. the GUI showed the generic evidence-state count, not the union of evidence
   IDs selected by available Opportunity components; and
2. the evidence ledger rejected a revision feature whenever its magnitude pair
   was unavailable, even if that same feature's dimensionless Breadth was
   selected and scored.

The evidence ledger now treats a revision feature selected by an Opportunity
component as accepted/selected/scored for that component, and the GUI reports
the direct selected-component count. This does not make unavailable magnitude
evidence available and does not change either score.

## Historical revision lineage defect

Run 104 also exposes an independent, deeper defect in reused revision rows.
`feature_rebuild_service._copy_revision_derived` formerly copied derived
values such as percent change, breadth, and acceleration into a reusable row
without copying the current/baseline/source lineage fields used to derive those
values. A snapshot could therefore select a value paired with stale evidence
IDs.

Read-only reconciliation of every selected Run 104 revision feature found:

- 2,118 selected revision-feature references;
- 525 feature values that do not reproduce from their persisted selected
  current/baseline evidence;
- 74 affected tickers;
- 661 provider-retrospective features that do reproduce from the persisted raw
  source baseline and receive the explicit
  `PROVIDER_RETROSPECTIVE_REHYDRATED_SOURCE_FIELD` exemption.

The copy operation now transfers all value and lineage fields atomically. List
and detail payloads also flag `revision_feature_lineage_mismatch` as a blocker
and state that a rebuild is required. Existing Run 104 database rows were not
silently rewritten; a controlled rebuild is required before semantic
certification.

## Source and eligibility semantics

Source freshness is now reported independently from normalization,
eligibility, and selection. The stable states are:

- `CATALYST_SOURCE_UNAVAILABLE`
- `CATALYST_SOURCE_STALE`
- `CATALYST_NONE_ELIGIBLE`
- `CATALYST_EVIDENCE_INELIGIBLE`
- `CATALYST_SELECTED`

Guidance uses the same staged model. Guidance acceptance remains literal
`accepted_for_scoring is TRUE`; no SEC guidance gate changed.

## Status

- XPEL selected-count case: **FIXED**
- CBL selected-count case: **FIXED**
- catalyst unavailable versus none-eligible: **FIXED**
- future rebuild lineage copying: **FIXED**
- persisted Run 104 selected revision lineage: **BLOCKER** pending controlled
  rebuild and recertification
