# CERI Changes and Alerts Forensic Certification

## Certification boundary

- Source semantics: original CERI SRS and SDD (structurally extracted from the available unsuffixed DOCX files), Run 101/102/104 remediation/certification evidence, and Controlled Replay v2 artifacts.
- Database schema: `0045_ceri_changes_alerts_semantics`.
- Final test command/result: `pytest -q tests/ceri` — **353 passed**.
- Historical rows were annotated or invalidated, never deleted.

## Exact Run 102 -> Run 104 reconciliation

The stored pair has 177 companies and only 66 change rows, all `BECAME_RATED / EVIDENCE_CONTRACT_TRANSITION`. Therefore the corrected trader groups for this pair are empty.

- Revisions: empty by design. Run 102 had no usable selected 7/30/90d revision evidence; Run 104 had 706 selected features in each window because 0043 rehydrated relative EPS observations. Old/current percentage-point, breadth, and acceleration deltas are undefined across that evidence-contract boundary, so thresholds 2.0 pp and 0.01 acceleration are not applied.
- Guidance: empty. Neither score snapshot had an available guidance component. New guidance changes require `accepted_for_scoring IS TRUE` and persist `guidance_event_id`; unlinked legacy source observations do not establish a pair change.
- Risk: empty and independently proven. All 177 Event Risk values are identical; minimum and maximum delta are both 0.0. Sufficiency/driver ledgers are unchanged for the comparison.
- Resolved: empty. No canonical event revision in this run pair establishes a lifecycle transition to completed/cancelled/resolved. Provider disappearance and a first-seen terminal observation do not qualify.
- Opportunity: the 66 rating appearances are remediation transitions, not market upgrades, and are excluded.

## Golden scenario matrix

| Scenario | Comparison/change result | Alert/dedup result | API/UI result | Status |
|---|---|---|---|---|
| First snapshot | `NO_PRIOR_COMPARABLE_SNAPSHOT`; no trader change | no alert | excluded, count visible | PASS |
| Model transition | `MODEL_VERSION_TRANSITION`; no market upgrade | no alert | forensic-only | PASS |
| Config transition | `CONFIG_TRANSITION`; no market upgrade | no alert | forensic-only | PASS |
| Evidence-contract transition / Run 102 -> 104 | 66 legacy rows annotated; future emission suppressed | related legacy alerts invalidated | semantic values only when explicitly included | PASS |
| Small same-posture score delta | no change below 1.0 | no alert | absent | PASS |
| Material same-posture score delta | `OPPORTUNITY_CHANGED` | transition identity | business summary | PASS |
| Cross 7.5 upward/downward | upgrade/downgrade | type-correct identity | business summary | PASS |
| Posture-only change | `POSTURE_CHANGED` | rule dependent | posture values | PASS |
| Source-only catalyst arrival | no trader `NEW_CATALYST` | no alert | default-excluded | PASS |
| Accepted material canonical catalyst | `NEW_CATALYST` with event payload | canonical event/revision identity | no `N/A -> N/A` | PASS |
| Material new event revision | distinct change | distinct alert; cooldown does not swallow it | revision details | PASS |
| Guidance observation rejected for scoring | no guidance change | no alert | absent | PASS |
| Risk delta below/above threshold | deterministic none/escalation | risk transition identity | Event Risk values | PASS |
| Provider disappearance | no resolution | no alert | absent | PASS |
| Canonical lifecycle resolution | resolved group | rule dependent | event lifecycle | PASS |
| Idempotent rerun | same change dedup key | same event key, no duplicate | one row | PASS |
| Invalid legacy alert | retained and classified | `INVALIDATED`, non-actionable | reason shown | PASS |
| Missing alert lineage | database constraint/audit hard failure | cannot be created | unavailable | PASS |

## Hard-fail checks

- No primary snapshot IDs: PASS. AEIS IDs 2362/2970 exist only in `technical`.
- No primary `N/A -> N/A`: PASS.
- No normal market changes or alerts across non-comparable transitions: PASS.
- No ineligible source arrival in the default catalyst feed: PASS.
- No duplicate alert on idempotent rerun: PASS.
- No invalid legacy alert actionable/unread: PASS; 723/723 invalidated.
- No alert without valid change/rule lineage: PASS; zero failures and database constraint installed.

## Status

**Changes certification: PASS. Alerts certification: PASS.**

Low-priority follow-up: improve provider-specific catalyst taxonomy/materiality extraction so qualifying canonical events can enter the newly strict boundary without manual review. This does not weaken the fail-closed certified behavior.
