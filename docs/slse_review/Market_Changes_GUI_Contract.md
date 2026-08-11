# Market Changes GUI Contract

## Purpose and source

Market Changes is a selected trading-date view over two canonical event streams: material `SignalChangeEvent` rows and current lifecycle transition/opening rows from `SetupLifecycleEvent`. Canonical-revision audit events are excluded. It is not an episode-current-state table and not a lifecycle-only table. Each DTO row has `source_type` and a source ID. A ticker may have more than one row on a date when independently material events occurred. The explicit No Material Change quick view is the sole exception and projects canonical `SNAPSHOT_OBSERVATION` rows with no event in either stream.

## Dates and summary cards

- `selected_date`: source `data_as_of_date`/event `effective_date`, never upload timestamp.
- `comparison_date`: prior canonical ticker date for a signal row; prior lifecycle observation date where available.
- `missing_session_gap`: completed US sessions between comparison and selected date minus one; null on first observation.
- Summary cards count the complete filtered result set before pagination. Required cards: newly discovered, tightening, ready, triggered, confirmed, extended, failed, material signal changes and major risk changes. Counts identify their basis (`lifecycle_transition` or `material_change`) and never mix current-state population with transition counts.

## Row fields

Every row exposes: ticker, company, sector, selected/comparison dates, source type and IDs; setup family, phase, previous/current state, transition, state age; actionability, confidence score/label and components when expanded; technical score current/previous/delta and 1/3/5/10-session velocities; setup score equivalents when available; trigger distance; sector rank current/previous/normalized delta; relative-strength change; market regime/gate; earnings/liquidity risk; blockers; required coverage/freshness/data quality; latest reason/reason codes; warnings; snapshot/run/version/hash links.

Signal-only rows do not fabricate a lifecycle transition. Lifecycle-only rows do not fabricate a numeric change. Missing values render `—` and remain null in JSON/CSV.

## Visual semantics

Positive/negative color follows normalized favorable direction, not raw numeric sign. A sector rank change from 9 to 5 displays `+4` improvement. Risk, stale and blocked states use text labels as well as color. Core semantics come from DTO fields; templates may display evidence JSON but may not derive state/type/severity/confidence from it.

## Filtering, sorting and pagination

Filters: selected date, ticker, sector, family, lifecycle state, transition, source type, actionability, confidence range, state-age range, score range, explicit velocity window/range, sector rank/change, market regime, blockers, warning flags. Quick filters map to documented predicates: Newly Ready, Newly Triggered, Improving Fast, Failed Today, Extended, Gate Blocked, Low Confidence and No Material Change.

Sorts: transition priority, confidence, current technical score, explicit score velocity, state age, trigger distance, sector rank and latest event time. Every sort adds effective date, semantic priority, source type and ID tie-breakers for stable pagination.

`total` and `summary` are computed before pagination; page item count is separately available. JSON and CSV use the same filtered scope and ordering as the GUI, subject only to an explicit export row limit error.

## Source links

`episode_id` links to episode detail; `lifecycle_event_id` links to a lifecycle-event detail/API anchor; `signal_change_event_id` links to signal-change detail/API; `snapshot_id`, `previous_snapshot_id`, `source_run_id` and context source IDs are never interchanged.
