# SLSE Historical Data Remediation

## Impact decision

Current derived SLSE history must be treated as invalid for release decisions if it was produced with the reviewed `slse-1.0.0` builder/alert code. The false required-close warning and 0.75 coverage ceiling can alter data-quality, confidence, lifecycle actionability and alerts. Signal-derived alerts may also contain fabricated confidence and incorrect acceleration semantics.

Affected tables:

- `setup_signal_snapshots`: false warning/coverage/quality and incomplete signal lineage.
- `setup_lifecycle_episodes`: potentially wrong confidence/actionability/current metadata.
- `setup_lifecycle_events`: potentially wrong confidence/actionability/warnings.
- `signal_change_events`: wrong raw rank delta, incomplete confidence/coverage/freshness evidence.
- `signal_alert_events`: false initial GATE_BLOCKED and unsupported acceleration alerts.

## Development and QA

After the corrected implementation and migration are installed, use an explicitly scoped SLSE purge/rebuild against disposable/dev data only. Re-run preserved upstream source runs in chronological order. Verify counts before deletion and retain the audit output. Never delete upload, score, price, market, or sector source evidence.

## Retained history

Do not update append-only snapshots/events in place. Use a new engine/config/schema version, capture corrected snapshots with new source/config hashes, create a REPAIR or persisted REPLAY evaluation version, and supersede prior current-version lifecycle events. Alert events should be rebuilt from corrected source events; invalid prior alert IDs remain auditable and are marked superseded/invalid by repair metadata rather than silently rewritten.

## Identification queries

Flag snapshots whose warning array contains `MISSING_REQUIRED_CLOSE_PRICE` while `close_price IS NOT NULL`, plus all dependent episode/event/alert rows by snapshot/evaluation/source-event links. Separately flag signal-derived alerts whose evidence lacks a real `source_confidence`, initial `GATE_BLOCKED` alerts with no predecessor actionability, and sector-rank events where `rank_delta != normalized_delta`.

## Release gate

No production retained-history repair should run until the corrected golden fixtures, disposable-PostgreSQL integration suite, and one real source-run vertical proof pass. Development/QA clean rebuild is recommended once those gates pass.

## Read-only retained-data inspection (2026-08-11)

Database `swinglens` was inspected without mutation:

| Evidence | Count |
|---|---:|
| setup_signal_snapshots | 8,217 |
| setup_lifecycle_episodes | 1,213 |
| setup_lifecycle_events | 9,134 |
| signal_change_events | 7,244 |
| signal_alert_events | 1,734 |
| non-null close plus false `MISSING_REQUIRED_CLOSE_PRICE` | 8,210 |
| snapshots with 0.75 required coverage | 8,210 |
| initial/no-predecessor GATE_BLOCKED alerts | 1,213 |
| change events without `confidence_score` evidence | 7,244 |

All 8,217 snapshots use engine `slse-1.0.0`, config `2026-07-31`. Alert distribution is 1,213 `GATE_BLOCKED/RISK` and 521 `SCORE_ACCELERATION/NOTABLE`. Therefore the existing derived history is invalid for release decisions and requires a clean dev/QA rebuild or versioned retained-history repair. No delete, rewrite, supersession or replay was executed during this audit.
