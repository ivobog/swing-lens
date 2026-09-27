# R4 aggregate operability certification

## Decision

R4 certifies all eight retained server-side aggregate families and closes GAP-011. The certification does not authorize a live canary: R5 database/runtime/fault certification remains required. No provider, pipeline, application runtime, recovery path, or production mutation was invoked during R4.

The machine-readable evidence is:

- `R4_PRODUCTION_CARDINALITY.json`: PostgreSQL 18.3 production cardinality, relation/index sizes, temporal ranges, index inventory, and read-only `EXPLAIN (FORMAT JSON)` plans.
- `R4_DISPOSABLE_PLAN_CERTIFICATION.json`: disposable PostgreSQL `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans at intentionally larger cardinality.
- `config/architecture_registry.json`: the enforceable semantic, retention, index, cardinality, result, plan-estimate, sequential-scan, and execution budgets.

## Aggregate census and policy

| Read | Exact owner and caller | Tables | Runtime/purpose | Classification and horizon | Required/expected index shape | Budget |
| --- | --- | --- | --- | --- | --- | --- |
| READ-002 | `background_performance_baseline._technical_artifact_report`; called by `build_background_performance_baseline` | `technical_feature_artifacts` | read-only diagnostic; status/kind counts for the online cache | `CERTIFIED_CURRENT_STATE_AGGREGATE`; entire online cache | intentional sequential scan; cache signature/last-used indexes remain lifecycle support | 250,000 rows examined; 16 rows returned; 750 ms; estimate multiplier 1.1 |
| READ-003 | same function/caller | `technical_feature_artifacts` | read-only diagnostic; shadow-validation count/sum state | `CERTIFIED_CURRENT_STATE_AGGREGATE`; entire online cache | intentional sequential scan; no selective predicate exists | 250,000; 3; 750 ms; 1.1 |
| READ-010 | `ceri.query_service._database_freshness_records`; called by CERI operations/read UI | `ceri_ingestion_runs` | provider/dataset identities needed to build freshness records | `CERTIFIED_WHOLE_HISTORY_AGGREGATE`; all retained ingestion runs | full scan is valid; `ix_ceri_ingestion_runs_provider_dataset_status` guards provider/dataset/status follow-up access | 1,000,000; 256; 1,000 ms; 1.1 |
| READ-012 | `ceri.query_service._provider_cost_summary`; called by CERI operations/read UI | `ceri_provider_request_telemetry` | lifetime retained provider request/cost/runtime/byte ledger | `CERTIFIED_WHOLE_HISTORY_AGGREGATE`; all online telemetry | full scan is valid; `ix_ceri_provider_telemetry_provider_observed` supports provider/time operational access | 5,000,000; 64; 2,000 ms; 1.1 |
| READ-022 | `setup_lifecycle.query_service._alerts_summary`; called by paginated/filtered `alerts` read | `signal_alert_events`, `signal_alert_rules` | status counts under the exact caller filter | `CERTIFIED_CURRENT_STATE_AGGREGATE`; filtered current alert projection | `idx_signal_alert_events_status_severity`; small rule-table hash join is permitted | 1,000,000; 3; 1,000 ms; 1.1 |
| READ-023 | same function/caller | same | severity counts under the exact caller filter | `CERTIFIED_CURRENT_STATE_AGGREGATE`; filtered current alert projection | same | 1,000,000; 4; 1,000 ms; 1.1 |
| READ-025 | `EvidenceWatermarkService.current_material_watermark`; called by cohort-generation watermark advancement | five Winner evidence tables | newest material evidence IDs for one outcome definition | `CERTIFIED_CURRENT_STATE_AGGREGATE`; one outcome-definition watermark | target forward/current, generation/source and prediction/definition indexes; eligibility/replay lookup indexes; temporal PK plus prediction lookup | 100 rows examined; 1 row; no large-table sequential scan; 250 ms; 2.5 |
| READ-026 | `WinnerProbabilityOperationsService.status`; called by Winner status routes/operations UI | `winner_market_data_obligations` | current durable obligation counts by status | `CERTIFIED_CURRENT_STATE_AGGREGATE`; current obligation ledger | intentional sequential scan; `idx_winner_market_data_obligation_status_range` supports selective operational access | 1,000,000; 5; 1,000 ms; 1.1 |

READ-010 is genuinely whole-history because a provider/dataset identity that has retained runs remains part of the freshness inventory even if it has no recent success. READ-012 is genuinely whole-history because it is explicitly a cumulative cost/storage ledger. Neither is classified whole-history merely because the old SQL lacked a cutoff. The other six describe current projections or current cache/ledger state.

## Production cardinality and retention

Production was inspected in an explicitly read-only transaction with a 15-second statement timeout. No payload column was selected.

| Table | Rows | Heap | Indexes | Total | Observed time range | Expected growth | Retention decision |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| `technical_feature_artifacts` | 9,352 | 6.1 MiB | 2.4 MiB | 46.6 MiB | 2026-08-06 to 2026-09-26 | ~67k/year at observed rate, but cache should plateau | online current-state cache; rows may be evicted by cache policy; immutable calculation evidence is separate |
| `ceri_ingestion_runs` | 54,039 | 54.9 MiB | 20.7 MiB | 75.7 MiB | 2026-08-08 to 2026-09-26 | ~0.40m/year | append-only audit online to the one-million-row budget; archive may move rows but must preserve provider/dataset identity and evidence |
| `ceri_provider_request_telemetry` | 38,070 | 9.7 MiB | 8.4 MiB | 18.2 MiB | 2026-08-08 to 2026-09-26 | ~0.28m/year | operational telemetry online to five million rows; roll up/archive before the ceiling while retaining the lifetime ledger |
| `signal_alert_events` | 4,305 | 5.7 MiB | 1.2 MiB | 6.9 MiB | 2026-08-12 to 2026-09-13 | ~48k/year at observed rate | current notification projection; linked decision/source evidence remains immutable independently |
| `winner_forward_outcomes` | 175,835 | 30.2 MiB | 56.8 MiB | 87.0 MiB | 2026-08-04 to 2026-09-06 | event/batch-driven; use 900k certified horizon | immutable evidence retained indefinitely; operational watermark is index-bounded |
| `winner_target_stop_outcomes` | 34,204 | 8.3 MiB | 9.8 MiB | 18.2 MiB | 2026-08-10 to 2026-09-06 | event/batch-driven; use 175k certified horizon | immutable evidence retained indefinitely; current-revision predicates remain authoritative |
| `winner_training_eligibility_decisions` | 8,859 | 17.3 MiB | 2.2 MiB | 19.6 MiB | one 2026-08-15 batch | batch-driven; use 45k certified horizon | immutable eligibility evidence retained indefinitely |
| `winner_training_outcome_replays` | 390 | 0.5 MiB | 0.1 MiB | 0.7 MiB | one 2026-08-15 batch | batch-driven; use 2k certified horizon | immutable replay evidence retained indefinitely |
| `winner_temporal_validity_decisions` | 5,478 | 5.5 MiB | 0.9 MiB | 6.4 MiB | 2026-09-05 to 2026-09-13 | event/batch-driven; use 30k certified horizon | immutable PIT validity evidence retained indefinitely |
| `winner_market_data_obligations` | 8,346 | 6.8 MiB | 1.9 MiB | 8.7 MiB | 2026-09-05 to 2026-09-13 | event/batch-driven; use 45k certified horizon | durable current lineage ledger; retain while referenced |

Observed distinct dimensions were small: ingestion had 2 providers/4 datasets/4 statuses; telemetry 2 providers/4 datasets/4 endpoints; alerts 981 tickers/3 statuses/3 severities; target outcomes 4 definitions/2 statuses. Short capture windows and batch-loaded Winner evidence make naive yearly extrapolation unreliable. The two continuously arriving CERI tables imply approximately 0.28 million telemetry and 0.39 million ingestion rows/year at the observed 49-day rate; their budgets therefore provide roughly 18 years and 2.5 years respectively if that rate persists. Capacity must be re-certified before a ceiling is crossed, not after it.

Relevant production index sizes are: ingestion provider/dataset/status 0.73 MiB; telemetry provider/observed 1.17 MiB; alert status/severity 0.06 MiB; obligation status/range 0.27 MiB. READ-025 support is target forward/current 1.60 MiB, target generation/source 3.19 MiB, target prediction/definition 1.13 MiB, eligibility lookup 0.08 MiB, replay lookup 0.02 MiB, and temporal prediction/sequence 0.26 MiB. The JSON evidence retains exact byte counts for every index, not only these policy-relevant ones.

No archive, partition, summary table, incremental aggregate, or materialized view is presently justified. Those mechanisms remain the escalation path if READ-010 approaches one million online rows or READ-012 approaches five million.

## Query-plan certification

The disposable database was migrated to the current Alembic head and seeded with 1,747,000 rows across the ten participating high-growth tables. This is at least approximately five times current production for each table except the deliberately much larger forward-outcome seed (900,000 versus 175,835). Every plan used `ANALYZE, BUFFERS`; all had zero temporary read/write blocks.

| Read | Seed rows in principal table | Plan and important scans | Rows examined / returned | Buffers hit/read | Plan / execution | Result |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| READ-002 | 50,000 | aggregate + intentional sequential scan | 50,000 / 6 | 0 / 2,120 | 0.126 / 55.871 ms | PASS |
| READ-003 | 50,000 | aggregate + intentional sequential scan | 50,000 / 3 | 2,120 / 0 | 0.141 / 36.577 ms | PASS |
| READ-010 | 275,000 | parallel sequential scan + aggregate/sort | 275,000 / 4 | 48 / 26,390 | 0.169 / 206.294 ms | PASS |
| READ-012 | 200,000 | parallel sequential scan + aggregate/sort | 200,000 / 4 | 48 / 15,385 | 0.237 / 262.609 ms | PASS |
| READ-022 | 25,000 alerts | hash join + intentional scans of 25,000 events/4 rules | 25,004 / 3 | 1,186 / 0 | 0.245 / 19.189 ms | PASS |
| READ-023 | 25,000 alerts | same join shape | 25,004 / 4 | 1,186 / 0 | 0.216 / 22.236 ms | PASS |
| READ-025 | 900,000 forward; 175,000 target; 77,000 other | descending PK/scoped index scans with `LIMIT 1`; no sequential scan | 7 / 1 | 90 / 0 | 1.102 / 0.213 ms | PASS |
| READ-026 | 45,000 | aggregate + intentional sequential scan | 45,000 / 5 | 1,800 / 0 | 0.151 / 19.145 ms | PASS |

The production `EXPLAIN` was intentionally non-executing. It selected whole-table sequential scans for the six queries in which every scoped row contributes, and index/index-only scans for all seven READ-025 branches. READ-025 used the target forward/current, generation/source, prediction/definition, eligibility lookup, replay lookup, temporal primary-key, and forward primary-key paths. No production `ANALYZE` was run.

## Optimization and index review

READ-025 was the only plan deficiency. Five `MAX(id)` subqueries made PostgreSQL aggregate all matching historical rows; the production estimate included approximately 5,904 target rows and 5,281 temporal rows for a single definition. The query now uses the semantically identical `ORDER BY id DESC LIMIT 1` form. At the 5x fixture it examines seven index entries and returns the same five-ID watermark as the prior `MAX` formulation.

Three candidate composite indexes were tested in the disposable database. PostgreSQL did not select them after the query-shape correction because existing scoped indexes and descending primary-key scans were cheaper. They were removed before certification. Therefore R4 creates no migration, adds no index, incurs no new write/storage cost, and leaves no redundant index. This is the smallest measured fix.

## Correctness and non-materialization

The integration fixture covers multiple providers, datasets, dates/sessions, alert statuses and severities, Winner outcome definitions, evidence families, and obligation states. It proves exact input-count conservation for READ-002/003/012/022/023/026, exact provider/dataset grouping for READ-010, and compares all five READ-025 values against the former `MAX` SQL. Cutoff/current-revision/status predicates remain in the watermark query.

All eight reads remain SQL aggregates or scalar/index-limit queries. No ORM `.all()` of entity payloads, Python grouping/deduplication, or JSON payload projection was introduced. Static source-token guards protect the authoritative grouping/scope shape, and the PostgreSQL test protects result cardinality, rows examined, plan-estimate multiplier, required schema indexes, sequential-scan policy, execution ceiling, and temporary spill.

## CI/operability guard

The fast architecture check now requires exactly READ-002/003/010/012/022/023/025/026, a certified class, all budget/retention fields, referenced source tokens, direct test registration, and every required index. It rejects any deferred R4 entry. The targeted PostgreSQL integration test performs the larger `EXPLAIN ANALYZE` certification outside the fast static pass.

Final closure state: P0 = 0, P1 = 0, P2 = 0, deferred R4 aggregates = 0. It is safe to begin R5, but not safe to run a live canary.
