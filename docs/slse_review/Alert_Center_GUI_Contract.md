# Alert Center GUI Contract

## Four independent concepts

- Alert Type: `NEW_READY`, `NEW_TRIGGER`, `NEW_CONFIRMATION`, `NEW_FAILURE`, `NEW_EXTENSION`, `SCORE_ACCELERATION`, `SECTOR_ACCELERATION`, `GATE_BLOCKED`, `DATA_DEGRADED`.
- Severity: `INFO`, `NOTABLE`, `ACTIONABLE`, `RISK`.
- Review Status: `UNREAD`, `ACKNOWLEDGED`, `DISMISSED`.
- Source Type: `LIFECYCLE_EVENT`, `SIGNAL_CHANGE_EVENT`, `ACTIONABILITY_CHANGE`, `DATA_QUALITY_CHANGE`.

No column or API field named only `status` may ambiguously represent more than Review Status. The page includes all canonical severities; a default view may prioritize unread ACTIONABLE/RISK without making NOTABLE unsupported.

## Row contract

Columns: Ticker, Date, Alert Type, Severity, Lifecycle State, Actionability, Confidence, Reason/Blocker, Source Type, Review Status, Actions. Stable DTO fields also include reason codes, blockers, evidence, episode ID, lifecycle-event ID, signal-change-event ID, source event key and evaluation-run ID.

Source links are chosen by source type. A lifecycle-event ID is never used as an episode ID. Acknowledge/dismiss changes only review status/timestamps; it does not alter the source event or alert type/severity.

## Counts, filters and ordering

Summary counts cover the complete filtered scope before pagination and include UNREAD/ACKNOWLEDGED/DISMISSED plus INFO/NOTABLE/ACTIONABLE/RISK. Filters: date/date range, ticker, alert type, severity, review status, lifecycle state, actionability and source type.

Default ordering: effective date descending, severity priority RISK > ACTIONABLE > NOTABLE > INFO, alert-type priority within severity, source event key and alert ID descending as deterministic tie-breakers. Explicit user sorts retain stable tie-breakers.

## Export parity

CSV/JSON preserve every visible semantic field and all source IDs. The same filter/sort query used by the page is used by exports. Missing lifecycle/actionability fields for signal-only alerts remain null rather than being inferred or filled with empty strings.

## Second-pass actionability semantics

Alert rows preserve the source actionability reason and metadata. Reduced market posture is `WATCH_ONLY` plus `MARKET_POLICY_REDUCED`, not `LOW_CONFIDENCE`; `GATE_BLOCKED` remains eligible only for ACTIONABLE/WATCH_ONLY to BLOCKED transitions. Evidence confidence and market posture must remain separately inspectable.
