# Global-read audit

## Method and count

An AST parent-chain scan found 30 direct `Session.execute/scalar(s)(select(Model...))` call sites with no `where`, `filter`, `limit`, or aggregate-only existence predicate in the executed statement. A second search for dynamically typed `select(model)` helpers found 11 additional production call families. Exact candidate total: **41**. Grouped aggregates are retained because result payload is small but database work remains unbounded.

The audit distinguishes row payload size from scan cost. “Schema-bounded” means a deliberately small registry/rule/universe, not a SQL-enforced maximum.

## Direct static candidates (30)

| ID | File:function | Read | Classification | Pipeline reach / risk |
| --- | --- | --- | --- | --- |
| READ-001 | `ceri_routes._admit_ceri_job_authority` | all `CeriCompany.ticker` | schema-bounded universe | mutating CERI APIs; linear ticker universe |
| READ-002 | `background_performance_baseline._technical_artifact_report` | grouped artifact status counts | unbounded scan, small result | diagnostics only; index/scan cost |
| READ-003 | same | grouped shadow validation sums | unbounded scan, small result | diagnostics only |
| READ-004 | `configuration_delivery.resolve_pipeline_configurations` | all `CeriAlertRule` | harmless small rules | pipeline config |
| READ-005 | `pipeline_executor._schedule_ceri_provider_ingest` | all `CeriCompany` | potentially unbounded | main pipeline; unnecessary ORM payload if run rows define scope |
| READ-006 | `readiness_service._ib_workload_required` | `EXISTS` scalar | harmless/false positive | readiness; bounded by EXISTS semantics |
| READ-007 | `ceri.backlog_cleanup_service.inspect_legacy_ceri_backlog` | all `UploadRun.id` | potentially unbounded | maintenance; historical upload count |
| READ-008 | `ceri.batched_workflow._ensure_ceri_companies` | all `CeriCompany` | schema-bounded universe | CERI pipeline setup |
| READ-009 | `ceri.query_service._database_freshness_records` | all company tickers | schema-bounded universe | diagnostics/UI |
| READ-010 | same | grouped ingestion provider/dataset | unbounded scan, small result | diagnostics/UI |
| READ-011 | `ceri.query_service._grouped_counts` | generic grouped count | unbounded scan, small result | diagnostics/UI |
| READ-012 | `ceri.query_service._provider_cost_summary` | telemetry aggregates | production-unbounded scan | diagnostics; telemetry growth |
| READ-013 | `ib_market_intelligence.journal.exclude_execution_fill` | all `IBTradeEpisode FOR UPDATE` | **large-payload dangerous** | direct mutation API; locks all episodes |
| READ-014 | `ib_market_intelligence.journal.rebuild_trade_episodes` | all `IBTradeEpisode` | **large-payload dangerous** | durable/direct rebuild; Python reconciliation |
| READ-015 | `ib_market_intelligence.journal.journal_analytics` | all `IBTradeResearchLink` | potentially unbounded | UI/analytics |
| READ-016 | `ib_market_intelligence.query_service.overview` | ordered `IBScannerRun` scalar | unbounded sort, one result | UI; add LIMIT for plan clarity |
| READ-017 | same | ordered `IBFlexImportRun` scalar | unbounded sort, one result | UI |
| READ-018 | `ib_market_intelligence.query_service.trade_journal` | all research links | **large-payload dangerous** | UI joins in Python |
| READ-019 | `ib_market_intelligence.query_service.scanner_runs` | all candidate/run joins | **large-payload dangerous** | UI; no pagination at this statement |
| READ-020 | `setup_lifecycle.alert_service.seed_builtin_rules` | all rule IDs | harmless small rules | alert service startup-on-use |
| READ-021 | `setup_lifecycle.alert_service._rules` | all rule IDs | harmless small rules | pipeline alert calculation |
| READ-022 | `setup_lifecycle.query_service._alerts_summary` | grouped status count | unbounded scan, small result | UI/ops |
| READ-023 | same | grouped severity count | unbounded scan, small result | UI/ops |
| READ-024 | `winner_probability.api_service.list_models` | all model versions | schema-small but growing | API; should paginate |
| READ-025 | `winner_probability.cohort_generation_service.current_material_watermark` | aggregate maxima | unbounded aggregate, one row | autonomous cohort planning |
| READ-026 | `winner_probability.operations_service.status` | grouped obligations | unbounded scan, small result | operations UI |
| READ-027 | `winner_probability.temporal_eligibility.load_current_temporal_decisions` | all latest decisions | potentially large payload | training/cohort services |
| READ-028 | `ceri.sec.processor_capability.evaluate_sec_processor_capability` | all releases | harmless small registry | worker/startup/readiness |
| READ-029 | `ceri.sec.processor_lifecycle.lifecycle_state` | all releases | harmless small registry | worker startup/readiness |
| READ-030 | `ceri.sec.processor_lifecycle.promote_processor` | all releases FOR UPDATE | harmless small registry but global lock | explicit processor promotion |

## Dynamic generic read families (11)

| ID | File:function | Runtime model(s) | Classification / reach |
| --- | --- | --- | --- |
| READ-031 | `ceri.job_handlers._load_rows` via `_eligible_changes` | `CeriChangeEvent`, `CeriScoreSnapshot`, `CeriCompany` | **large-payload dangerous**; `CERI_ALERT_REBUILD` globally loads before Python filtering; main pipeline continuation |
| READ-032 | `ceri.normalization_service._load` | `CeriCatalystEventRevision` | **production-unbounded** per normalized catalyst; used only to compute next revision for one event |
| READ-033 | `ceri.purge_service._load` / `_rows_with_source_ids` | source, estimates, earnings, guidance, revisions, sources, features, scores, changes, alerts | **large-JSON dangerous**; purge preview and execution load most CERI corpus |
| READ-034 | `ceri.query_service` generic model loader | multiple CERI diagnostics/export models | large-payload dangerous if called without pagination; UI/diagnostics |
| READ-035 | `ceri.export_service._load` | export-selected CERI models | production-unbounded by design; operator export path |
| READ-036 | `ceri.identity_resolver` generic loaders | all companies and aliases | schema-bounded universe; ingest/repair |
| READ-037 | `ceri.backfill_service._load` | backfill-selected CERI models | potentially production-unbounded; explicit maintenance |
| READ-038 | `ceri.alert_service._load` | alert/change/rule models depending call | potentially unbounded; alert rebuild/direct service |
| READ-039 | `ceri.feature_rebuild_service` generic result loader | feature/source models | potentially large; rebuild path; predicates must be verified at caller |
| READ-040 | `ceri.capture_service` generic/result loader | snapshots/evidence/source rows | potentially large; capture path; many calls are run-scoped but helper permits unscoped use |
| READ-041 | `ceri.price_response_service` generic result loader | event/price feature rows | potentially large; feature rebuild |

## Large JSON/evidence/source focus

| Risk | Affected reads | Why |
| --- | --- | --- |
| Highest | READ-033 | materializes raw/restricted source JSON plus derived feature and snapshot JSON across the corpus; preview alone can exhaust memory |
| High | READ-031 | loads all CERI changes and score snapshot component/evidence JSON on every alert rebuild when payload is run/ticker scoped only in Python |
| High | READ-014/018/019 | historical trade/candidate/link sets grow without a schema bound and are joined/reconciled in Python |
| Medium | READ-032 | repeats a full catalyst-revision load for individual normalization; quadratic behavior as corpus grows |
| Medium | READ-005/008 | company universe is expected to be modest but no SQL/schema bound exists |

The previously reported CERI change-detection global reads are no longer present in the current uncommitted `change_rebuild_service`: `_scoped_scalars` rejects predicate-free reads for the five large CERI models and the implementation chunks by company. That remediation does not cover the downstream CERI alert handler (READ-031), normalizer (READ-032), or purge (READ-033).

## Required remediation shape

- READ-031: derive change/snapshot/company predicates from exact job authority and query them directly; paginate/chunk change IDs.
- READ-032: `max(revision_number)` or event-scoped ordered/aggregate query under the event lock.
- READ-033: provider/license-scoped source query, set-based joins/JSON membership strategy, server-side streaming/chunked immutable manifest, and a second locked authority check before application.
- READ-013/014/018/019: episode/run/user/date pagination and target-only row locking.
- READ-034/035/037..041: require a scope/pagination object at the helper signature so unscoped use is structurally impossible.

## Verdict

The candidate audit is complete: 41 exact read families are classified. Seven are large-payload dangerous (READ-013, 014, 018, 019, 031, 033, and the unbounded branches of READ-034/035), six are potentially production-unbounded pipeline/maintenance reads, eight are unbounded aggregate scans with small result sets, and the remainder are schema-small or false positives. The high-risk reads are P1/P2 closure blockers.
