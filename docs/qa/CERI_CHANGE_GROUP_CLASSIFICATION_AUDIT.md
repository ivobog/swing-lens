# CERI Change Group Classification Audit

## Root cause and correction

The router owned a second, incomplete UI-only mapping. It omitted rating transitions and several catalyst/lifecycle types, then silently placed the remainder in Other. Production now has one exhaustive `CeriChangeType -> ChangeGroup` mapping in `change_semantics.py`; API DTOs and the HTML page consume its result. A test proves the mapping key set equals the complete enum.

| Group | Change types |
|---|---|
| Upward revisions | `REVISION_UP`, `REVISION_ACCELERATED` |
| Downward revisions | `REVISION_DOWN`, `REVISION_DECELERATED` |
| Guidance | `GUIDANCE_RAISED`, `GUIDANCE_LOWERED`, `GUIDANCE_WITHDRAWN` |
| Catalysts | `NEW_CATALYST`, `CATALYST_UPDATED`, `NEW_BINARY_EVENT`, `CATALYST_CONFIRMED`, `CATALYST_DELAYED` |
| Opportunity | `OPPORTUNITY_CHANGED`, `OPPORTUNITY_UPGRADED`, `OPPORTUNITY_DOWNGRADED`, `POSTURE_CHANGED`, `BECAME_RATED`, `BECAME_UNRATED` |
| Risk | `RISK_ESCALATED`, `RISK_DEESCALATED` |
| Resolved | `CATALYST_CANCELLED`, `CATALYST_RESOLVED`, `EVENT_COMPLETED`, `EVENT_CANCELLED`, `EVENT_RESOLVED`, `RISK_RESOLVED` |
| Other/Data quality | `DATA_STALE`, `DATA_REFRESHED`, `CONFLICT_OPENED`, `CONFLICT_RESOLVED`, `MODEL_VERSION_TRANSITION`, `CONFIG_TRANSITION`, `EVIDENCE_CONTRACT_TRANSITION`, `BASELINE_ESTABLISHED` |

`BECAME_RATED` and `BECAME_UNRATED` are therefore Opportunity changes, never fallback data-quality rows.

## Opportunity taxonomy

Rules are deterministic and configuration-driven:

- missing -> rated: `BECAME_RATED`
- rated -> missing: `BECAME_UNRATED`
- crossing upward through `opportunity_upgrade_threshold=7.5`: `OPPORTUNITY_UPGRADED`
- crossing downward through 7.5: `OPPORTUNITY_DOWNGRADED`
- posture changes without a threshold crossing: `POSTURE_CHANGED`
- same-posture absolute score movement at or above `score_delta=1.0`: `OPPORTUNITY_CHANGED`
- smaller movement: no change

This replaces the old behavior that called every positive score delta at least 1.0 an upgrade.

## Importance versus signal class

The old `severity` value mixed urgency with semantic direction (`RISK` versus `NOTABLE`). New rows and backfilled rows have independent dimensions:

- importance: `INFO`, `NOTABLE`, `IMPORTANT`, `URGENT`
- signal class: `POSITIVE`, `NEGATIVE`, `RISK`, `NEUTRAL`, `DATA_QUALITY`

Legacy `severity` remains for compatibility but is populated from importance for new changes. Event Risk remains the independent numeric score in score snapshots; it is not derived from either display dimension.
