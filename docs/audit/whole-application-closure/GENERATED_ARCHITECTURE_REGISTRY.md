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
| Deferred R4 aggregate scans | 8 |

## Closure invariants

- Route declarations are discovered from router ASTs independently of runtime metadata.
- Durable handlers come from `default_job_handlers`; creators come from ASTs.
- Model `__tablename__` declarations drive the table and writer census.
- Recovery callers need `authority=` and must match the checked caller census.
- Transaction, autonomous, state-writer, and read sites use exact censuses.
- Every route, job, and autonomous trigger has NORMAL and CERTIFICATION behavior.
- Material path IDs reference test files/nodes that exist.

## Explicit deferred work

- `READ-002` — `app/services/background_performance_baseline.py:_technical_artifact_report:artifact_status_counts` — `DEFERRED_R4_AGGREGATE` (`GAP-011`)
- `READ-003` — `app/services/background_performance_baseline.py:_technical_artifact_report:shadow_validation_sums` — `DEFERRED_R4_AGGREGATE` (`GAP-011`)
- `READ-010` — `app/services/ceri/query_service.py:_database_freshness_records:provider_dataset_counts` — `DEFERRED_R4_AGGREGATE` (`GAP-011`)
- `READ-012` — `app/services/ceri/query_service.py:_provider_cost_summary:telemetry_aggregates` — `DEFERRED_R4_AGGREGATE` (`GAP-011`)
- `READ-022` — `app/services/setup_lifecycle/query_service.py:_alerts_summary:status_counts` — `DEFERRED_R4_AGGREGATE` (`GAP-011`)
- `READ-023` — `app/services/setup_lifecycle/query_service.py:_alerts_summary:severity_counts` — `DEFERRED_R4_AGGREGATE` (`GAP-011`)
- `READ-025` — `app/services/winner_probability/cohort_generation_service.py:current_material_watermark` — `DEFERRED_R4_AGGREGATE` (`GAP-011`)
- `READ-026` — `app/services/winner_probability/operations_service.py:status:obligation_counts` — `DEFERRED_R4_AGGREGATE` (`GAP-011`)

No GAP-011 remediation is performed by this registry.
