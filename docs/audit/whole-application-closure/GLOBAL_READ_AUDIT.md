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
| READ-013 | `ib_market_intelligence.journal.exclude_execution_fill` | episodes containing exact fill ID `FOR UPDATE` | **R2 closed: target-scoped** | direct mutation API; locks only reachable episodes |
| READ-014 | `ib_market_intelligence.journal.rebuild_trade_episodes` | episodes for exact input ticker set | **R2 closed: authority-scoped** | durable/direct rebuild; deterministic target reconciliation |
| READ-015 | `ib_market_intelligence.journal.journal_analytics` | server-side grouped aggregates/percentiles | aggregate scan, small result | UI/analytics; scan budget deferred unchanged to GAP-011 |
| READ-016 | `ib_market_intelligence.query_service.overview` | ordered `IBScannerRun` scalar | unbounded sort, one result | UI; add LIMIT for plan clarity |
| READ-017 | same | ordered `IBFlexImportRun` scalar | unbounded sort, one result | UI |
| READ-018 | `ib_market_intelligence.query_service.trade_journal` | links for one bounded episode page | **R2 closed: paginated/scoped** | UI page uses stable `(opened_at,id)` cursor |
| READ-019 | `ib_market_intelligence.query_service.scanner_runs` | candidates for bounded selected run IDs | **R2 closed: bounded** | UI candidate cap 500 |
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
| READ-031 | `ceri.job_handlers._eligible_changes` | exact change/run/company/ticker/time authority | **R2 closed: SQL-scoped/keyset batched**; fixture loader rejects production `Session` |
| READ-032 | `ceri.normalization_service._next_catalyst_revision_number` | event-scoped `MAX(revision_number)` | **R2 closed: scalar/event locked** |
| READ-033 | `ceri.purge_service._sql_lifecycle_manifest` | provider/license source IDs and relational dependent IDs | **R2 closed: ID projection/batched target locks**; preview does not project source bodies |
| READ-034 | `ceri.query_service` generic model loader | multiple CERI diagnostics models | **R2 closed:** production `Session` rejected; production branches are named/scoped/bounded |
| READ-035 | `ceri.export_service._fixture_rows` | fixture collections only | **R2 closed:** production `Session` rejected; export SQL is scope-pushed and capped at 5,000 rows |
| READ-036 | `ceri.identity_resolver` generic loaders | all companies and aliases | schema-bounded universe; ingest/repair |
| READ-037 | `ceri.backfill_service._fixture_companies` | fixture adapter plus explicit all-universe maintenance projection | intentionally retained maintenance/schema-universe path; no dynamic production large-model loader |
| READ-038 | `ceri.alert_service` named rule/cooldown queries | rules plus ticker/rule cooldown history | **R2 closed:** rule table schema-small; alert history SQL-scoped |
| READ-039 | `ceri.feature_rebuild_service._scalars` | caller-supplied statements | **R2 closed:** large CERI entities require SQL criteria; catalyst/earnings/guidance fallback predicates are explicit |
| READ-040 | `ceri.capture_service._scalars` | caller-supplied run/evidence/source statements | **R2 closed:** large CERI entities require SQL criteria |
| READ-041 | `ceri.price_response_service._scalars` | ticker/session/cutoff price/event statements | **R2 closed:** large CERI entities require SQL criteria |

## Large JSON/evidence/source focus

| Risk | Affected reads | Why |
| --- | --- | --- |
| Highest | READ-033 | materializes raw/restricted source JSON plus derived feature and snapshot JSON across the corpus; preview alone can exhaust memory |
| High | READ-031 | loads all CERI changes and score snapshot component/evidence JSON on every alert rebuild when payload is run/ticker scoped only in Python |
| High | READ-014/018/019 | historical trade/candidate/link sets grow without a schema bound and are joined/reconciled in Python |
| Medium | READ-032 | repeats a full catalyst-revision load for individual normalization; quadratic behavior as corpus grows |
| Medium | READ-005/008 | company universe is expected to be modest but no SQL/schema bound exists |

The CERI change-detection global reads remain closed by `_scoped_scalars` and company chunks. R2 extends the same structural rule through downstream alerts, normalization, purge, query/export, feature, capture, and price-response paths.

## Required remediation shape

- READ-031: derive change/snapshot/company predicates from exact job authority and query them directly; paginate/chunk change IDs.
- READ-032: `max(revision_number)` or event-scoped ordered/aggregate query under the event lock.
- READ-033: provider/license-scoped source query, set-based joins/JSON membership strategy, server-side streaming/chunked immutable manifest, and a second locked authority check before application.
- READ-013/014/018/019: episode/run/user/date pagination and target-only row locking.
- READ-034/035/037..041: require a scope/pagination object at the helper signature so unscoped use is structurally impossible.

## R2 mechanical reconciliation

The original 41-family inventory remains the stable census; R2 changed classifications rather than deleting audit identities. Repeated AST/text search plus PostgreSQL SQL capture gives these exact post-R2 counts:

| Risk family | Before | After | Retained/deferred |
| --- | ---: | ---: | --- |
| Predicate-free/potentially unscoped large CERI production read families in READ-031..041 | 10 | 0 | READ-036 company/alias identity is schema-small; explicit all-universe maintenance is retained without a dynamic large-model loader |
| High-risk large-payload read families | 7 | 0 | server-side aggregate scan cost remains GAP-011, without large ORM materialization |
| Global IB journal payload reads | 3 | 0 | journal aggregates remain server-side and GAP-011 owns plan budgets |
| Global IB journal lock paths | 2 | 0 | exact fill-reachable or input-ticker rows only |
| Production-capable unsafe generic CERI loader families | 10 | 0 | fixture-only adapters remain and explicitly reject `Session` |

PostgreSQL regression capture observed zero predicate-free reads across the alert, purge, catalyst, and IB R2 families. The retained READ-002/003/010/012/022/023/025/026 aggregate scans are deliberately unchanged by this task and remain GAP-011.

## Verdict

R2 closes every high-risk payload/materialization family identified for GAP-004 through GAP-008. The remaining unbounded reads are classified schema-universe, explicit maintenance, or small-result aggregate scans; the aggregate/index/retention budget is still GAP-011 and was not altered.
