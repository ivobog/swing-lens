# Generated architecture registry

This document is generated from `config/architecture_registry.json`. Run
`python scripts/check_architecture_registry.py --write` after an intentional,
reviewed registry change. CI uses `--check` and never rewrites files.

| Inventory | Count |
| --- | ---: |
| Mutating HTTP registrations | 52 |
| Durable job types | 34 |
| SQLAlchemy tables | 120 |
| Mutable tables | 119 |
| Writer families | 73 |
| Recovery paths | 22 |
| Autonomous triggers | 17 |
| Material transaction families | 25 |
| High-risk tables | 71 |
| Certified aggregate policies | 8 |
| Deferred R4 aggregate scans | 0 |

## Closure invariants

- Route declarations are discovered from router ASTs independently of runtime metadata.
- Durable handlers come from `default_job_handlers`; creators come from ASTs.
- Model `__tablename__` declarations drive the table and writer census.
- Recovery callers need `authority=` and must match the checked caller census.
- Transaction, autonomous, state-writer, and read sites use exact censuses.
- Every route, job, and autonomous trigger has NORMAL and CERTIFICATION behavior.
- Material path IDs reference test files/nodes that exist.

## Certified aggregate budgets

- `READ-002` — `CERTIFIED_CURRENT_STATE_AGGREGATE` — horizon `ONLINE_CACHE_CURRENT_STATE` — 750 ms
- `READ-003` — `CERTIFIED_CURRENT_STATE_AGGREGATE` — horizon `ONLINE_CACHE_CURRENT_STATE` — 750 ms
- `READ-010` — `CERTIFIED_WHOLE_HISTORY_AGGREGATE` — horizon `ALL_RETAINED_INGESTION_RUNS` — 1000 ms
- `READ-012` — `CERTIFIED_WHOLE_HISTORY_AGGREGATE` — horizon `ALL_RETAINED_PROVIDER_TELEMETRY` — 2000 ms
- `READ-022` — `CERTIFIED_CURRENT_STATE_AGGREGATE` — horizon `FILTERED_CURRENT_ALERT_PROJECTION` — 1000 ms
- `READ-023` — `CERTIFIED_CURRENT_STATE_AGGREGATE` — horizon `FILTERED_CURRENT_ALERT_PROJECTION` — 1000 ms
- `READ-025` — `CERTIFIED_CURRENT_STATE_AGGREGATE` — horizon `OUTCOME_DEFINITION_CURRENT_WATERMARK` — 250 ms
- `READ-026` — `CERTIFIED_CURRENT_STATE_AGGREGATE` — horizon `CURRENT_OBLIGATION_LEDGER` — 1000 ms

GAP-011 is closed; no deferred aggregate policy remains.
