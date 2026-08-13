# CERI Run 101 Remediation Implementation Report

## Status

Code remediation and deterministic Golden 10 certification are complete. Deployment is **BLOCKED** because the active research database remains at schema `0041_sec_incremental_documents`; the additive `0042_ceri_run101_fail_closed` migration was verified only on disposable PostgreSQL. A new 200-ticker run is not authorized until 0042 is applied and verified on the intended deployment database.

## Phase 0 baseline (2026-08-13)

- Repository HEAD: `8e90975527d2f3a407874c72a7525b050098aee4`.
- Initial dirty state: untracked Run 101 forensic artifacts, verification bundles, and `scripts/qa/certify_ceri_run101.py`; all are treated as user-owned and preserved.
- Current schema head inspected: `0041_sec_incremental_documents` over `0040_ceri_remediation_ledgers`.
- Current semantic identity: calculation `ceri-1.1.0`, config `2026-08-12-remediation-r3`.
- Focused baseline: `218 passed, 1 warning` from `pytest tests/ceri -q`.
- Read-only Run 101 reconstruction: `200/200` score/risk/coverage reconstructions and `200/200` evidence hashes match.
- Reproduced defect baseline: `200` first-snapshot `OPPORTUNITY_UPGRADED` events, `40` alerts, `192/200` SEC runs partial, `0/4800` usable revision slots.
- Safety: reconstruction ran with `SET TRANSACTION READ ONLY` and wrote artifacts only to a temporary directory. No active research rows were changed.

## Explicit design-conflict register

| Conflict | Older decision | Run 101 controlling decision | Resolution |
|---|---|---|---|
| Calculation version | Prior remediation finalized `ceri-1.1.0`. | Semantic changes require the next version, recommended `ceri-1.2.0`. | Preserve all 1.0/1.1 snapshots; emit new calculations only as `ceri-1.2.0`. |
| Missing-currency estimates | Prior plan required verified currency for every revision and described missing-currency EPS as unavailable. | Same-provider provider-retrospective EPS percentage revisions may be dimensionless without fabricated currency. | Add explicit comparison modes; retain currency/scale gates for absolute and cross-provider comparisons. |
| Provider outage semantics | Full-stack runbook said outages should degrade confidence/increase risk and continue. | Dominant provider unreadiness must create explicit degraded/rejected run-level evidence status. | Preserve technical workflow completion separately from evidence certification. |
| SEC legacy acceptance | Migration 0040 intentionally left legacy acceptance nullable and current selection rejects only explicit false. | Only literal `accepted_for_scoring = TRUE` may score; legacy null must fail closed. | Safe migration converts unknown to false before making the column non-null; never mass-promote. |
| Retrospective EPS materialization | Existing adapter materializes retrospective EPS points with historical `effective_at`. | Retrospective values are known only when the containing response is known and must not look historically observed. | Persist `reference_at` separately; select only under same-response relative mode and PIT-gate on response `known_at`. |
| First snapshot changes | Existing change service emits a generic opportunity upgrade on every first snapshot. | First opportunity and risk observations are baseline-only. | No actionable upgrade/escalation for absent prior state; use explicit rated/unrated transitions only between comparable snapshots. |
| Alert eligibility | Existing alert service gates only on configured change type/cooldown. | Opportunity and risk changes have different semantic gates. | Opportunity requires rated/covered/non-insufficient current state; risk requires prior comparable risk plus accepted evidence, independent of Opportunity. |

## Requirement traceability matrix

This matrix records the Phase 0 status. The final certification override following it is authoritative.

| Requirement | Implementation file(s) | Test/certification file(s) | Initial status |
|---|---|---|---|
| CERI-BR-001 | `opportunity_score_service.py`, `revision_feature_service.py`, `snapshot_service.py` | `test_ceri_remediation.py`, `test_wave1_p0.py` | BASELINE_PASS |
| CERI-BR-002 | `source_record_service.py`, feature services, `snapshot_service.py` | new evidence-state and vertical tests | PARTIAL |
| CERI-BR-003 | `point_in_time_query.py`, normalizers | `test_point_in_time_query.py`, new leakage tests | PARTIAL |
| CERI-BR-004 | `point_in_time_query.py`, `revision_feature_service.py` | new comparison-mode tests | RED |
| CERI-BR-005 | feature ledgers, provider/run manifests | new capability/reason tests | PARTIAL |
| CERI-BR-006 | `change_detection_service.py` | `test_ceri_change_detection_service.py` | RED |
| CERI-BR-007 | `change_detection_service.py`, `alert_service.py` | lifecycle/alert regression tests | RED |
| CERI-BR-008 | additive migrations, snapshot services | migration/immutability and Run 101 replay | BASELINE_PASS |
| CERI-EODHD-FR-001 | `providers/eodhd_mapping.py`, `providers/eodhd_provider.py`, source/request telemetry | `test_eodhd_provider.py`, mapping contract tests | PARTIAL |
| CERI-EODHD-FR-002 | `source_record_service.py`, EODHD projection | economic-fingerprint dedup tests | RED |
| CERI-EODHD-FR-003 | EODHD provider, estimate normalizer/model | provider fixture/normalization tests | PARTIAL |
| CERI-EODHD-FR-004 | PIT query, revision service/model | same-provider relative tests | RED |
| CERI-EODHD-FR-005 | PIT query, currency conversion, revision service | cross-provider/absolute rejection tests | BASELINE_PASS |
| CERI-EODHD-FR-006 | estimate normalizer, PIT query, revision service | accumulated Revenue history tests | PARTIAL |
| CERI-EODHD-FR-007 | feature rebuild/capability service | slot capability/performance tests | RED |
| CERI-EODHD-FR-008 | EODHD provider, earnings normalizer | historical/upcoming acquisition tests | PARTIAL |
| CERI-EODHD-FR-009 | EODHD provider, earnings normalizer | zero-preservation tests | BASELINE_PASS |
| CERI-EODHD-FR-010 | surprise service, PIT query | report-time/later-estimate tests | PARTIAL |
| CERI-EODHD-FR-011 | EODHD provider, catalyst normalizer/model | structured metadata fixture tests | PARTIAL |
| CERI-EODHD-FR-012 | EODHD provider, catalyst feature service | issuer ledger tests | PARTIAL |
| CERI-EODHD-FR-013 | catalyst/price-response/risk services | lifecycle/materiality/rejection tests | PARTIAL |
| CERI-EODHD-FR-014 | EODHD client/request telemetry | HTTP fidelity tests | RED |
| CERI-SEC-FR-001 | `sec/state_service.py`, SEC models | readiness state tests | PARTIAL |
| CERI-SEC-FR-002 | batched workflow/orchestration, processing run model | REQUIRE_READY/ALLOW_DEGRADED tests | RED |
| CERI-SEC-FR-003 | SEC incremental ingestion/state service | terminal reuse tests | BASELINE_PASS |
| CERI-SEC-FR-004 | guidance selection/services/model | explicit-true and migration tests | RED |
| CERI-SEC-FR-005 | `sec/guidance_extractor.py` | hard-negative corpus | RED |
| CERI-SEC-FR-006 | SEC extractor/guidance model | clean structured guidance tests | PARTIAL |
| CERI-SEC-FR-007 | guidance comparison/extractor | UNKNOWN/no-prior tests | PARTIAL |
| CERI-SEC-FR-008 | guidance current-state selection | stale/superseded tests | RED |
| CERI-SEC-FR-009 | labeled SEC certification harness | precision corpus/certification artifact | RED |
| CERI-CORE-FR-001 | evidence ledgers/models/DTOs | evidence-state/API/UI tests | RED |
| CERI-CORE-FR-002 | feature and snapshot ledgers | persisted vertical tests | PARTIAL |
| CERI-CORE-FR-003 | opportunity score/config | remediation/acceptance tests | BASELINE_PASS |
| CERI-CORE-FR-004 | revision aggregate/snapshot ledger | partial internal coverage tests | PARTIAL |
| CERI-CORE-FR-005 | confidence service | confidence hard-gate tests | BASELINE_PASS |
| CERI-CORE-FR-006 | event risk/catalyst selection | risk reconstruction tests | BASELINE_PASS |
| CERI-CORE-FR-007 | snapshot/query/API DTOs | lineage narrowing tests | RED |
| CERI-LIFE-FR-001 | change detection service | baseline tests | RED |
| CERI-LIFE-FR-002 | change detection/enums | null transition truth-table tests | RED |
| CERI-LIFE-FR-003 | change detection service | independent risk tests | RED |
| CERI-ALERT-FR-001 | alert service | opportunity alert gate tests | RED |
| CERI-ALERT-FR-002 | alert service | independent risk alert tests | RED |
| CERI-ALERT-FR-003 | change/alert source acceptance gate | rejected guidance/catalyst alert tests | RED |
| CERI-RUN-FR-001 | processing/ingestion run manifests | provider readiness manifest tests | RED |
| CERI-RUN-FR-002 | processing run/batched workflow | degraded/rejected run status tests | RED |
| CERI-RUN-FR-003 | feature rebuild/capability matrix | short-circuit performance tests | RED |
| CERI-RUN-FR-004 | feature rebuild bulk unavailable path | bounded-query tests | RED |
| CERI-REP-FR-001 | run/capture models and manifest service | deployment identity tests | RED |
| CERI-REP-FR-002 | snapshot service | Run 101 reconstruction | BASELINE_PASS |
| CERI-REP-FR-003 | source identity/provenance | correction/replay semantic tests | PARTIAL |
| CERI-LIC-FR-001 | provider policy/projection/source service | storage/export tests | BASELINE_PASS |
| CERI-LIC-FR-002 | purge service | provider-lineage purge tests | BASELINE_PASS |
| CERI-COST-FR-001 | request/processing/provider cost ledger | runtime/request/storage tests | PARTIAL |
| CERI-UI-FR-001 | API DTOs/query/routes/templates | explicit-status API/UI tests | RED |
| CERI-UI-FR-002 | query/ops DTOs/templates | provider-health explanation tests | RED |
| CERI-UI-FR-003 | evidence lineage DTOs/templates | considered-vs-selected tests | RED |
| CERI-UI-FR-004 | lifecycle/alert domain + query/UI | DB/API/UI parity tests | RED |
| CERI-NFR-001 | all work packages | RED/GREEN/CERTIFY evidence in this report | PARTIAL |
| CERI-NFR-002 | domain services/configuration | generic fixtures, no ticker-specific branches | BASELINE_PASS |
| CERI-NFR-003 | PIT query/normalizers | AS_KNOWN leakage tests | PARTIAL |
| CERI-NFR-004 | capability matrix/instrumentation | golden runtime/query-count artifacts | RED |
| CERI-NFR-005 | provider fixture tests | deterministic offline contracts | PARTIAL |
| CERI-NFR-006 | additive Alembic migration | empty/populated/legacy snapshot migration tests | RED |
| CERI-NFR-007 | ledgers/manifests/DTOs | first-cause reason assertions | PARTIAL |

## Phase certification log

### RED / GREEN / REFACTOR / CERTIFY record

| Package | RED evidence | GREEN implementation | CERTIFY result |
|---|---|---|---|
| Lifecycle and alerts | First/null/rated/unrated/risk truth-table failures | Explicit baseline, became-rated/unrated, separate Opportunity/Risk alert gates | Focused tests plus PostgreSQL legacy/batched parity pass; first snapshot produces 0 changes and 0 alerts |
| SEC fail-closed/current state | Null/false acceptance, Run 101 hard negatives, stale/current failures | Literal-true scoring, visible-text extraction, comparison/current-state gate, processor signature | Labeled fixture precision 100%, zero golden false positives; SEC suites pass |
| EPS comparison/identity | Missing-currency relative, AS_KNOWN, cross-provider, scale/period, economic dedup failures | Three comparison modes, PIT lineage, retrieval-independent economic hash | Provider fixture and revision suites pass |
| Revenue observations | Fabricated first-history/backdated baseline failures | Immutable accumulated observations; provider-retrospective Revenue excluded | Revenue history suite passes |
| Earnings | Historical/acquisition/zero/report-time selection failures | Separate historical/upcoming windows and report-time lineage | Earnings provider/surprise suites pass |
| Catalysts | Query-ticker relevance, completed event, other issuer failures | Structured-symbol relevance and lifecycle gates | Catalyst/price-response suites pass |
| SEC readiness | Missing explicit preflight/run states | Six readiness states, strict/degraded policy, signature-aware bootstrap, terminal reuse | Unit and PostgreSQL incremental suites pass |
| Capability/performance | Impossible-family and sparse-slot overwork | Bulk sparse capability matrix, input-hash reuse, runtime/query counters | Capability/scoped processing suites pass |
| Evidence/API/UI | Stored evidence conflated with selected/scored | Six evidence states and parity DTO/UI counts | Snapshot/query/UI suites pass |
| Identity/licensing/cost | Missing deployment/storage ledger | Run identity and provider request/runtime/byte ledger | Identity/cost tests and scope-exact purge test pass |
| Migration | Nullable legacy acceptance and immutable snapshot risk | Additive 0042 migration; null becomes false, no promotion | Clean and populated disposable PostgreSQL migrations pass; active DB unchanged at 0041 |
| Golden 10 | No complete vertical artifact | Deterministic ten-scenario trace generator | 10/10 pass with 11 stages per ticker |

## Final requirement certification override

The implementation/test mappings in the Phase 0 matrix remain valid. Final status by requirement is:

| Requirement group | Requirements | Final status | Certification evidence |
|---|---|---|---|
| Business rules | CERI-BR-001 through CERI-BR-008 | CERTIFIED | Full CERI suite, legacy replay, evidence/lifecycle tests |
| EODHD | CERI-EODHD-FR-001 through CERI-EODHD-FR-014 | CERTIFIED_CODE | Provider contract fixtures, revision/Revenue/earnings/catalyst tests; live coverage awaits deployment run |
| SEC | CERI-SEC-FR-001 through CERI-SEC-FR-009 | CERTIFIED_CODE | Fail-closed, readiness, extraction, precision, migration, and PostgreSQL incremental tests |
| Core scoring/evidence | CERI-CORE-FR-001 through CERI-CORE-FR-007 | CERTIFIED | Full suite plus Golden 10 traces |
| Lifecycle | CERI-LIFE-FR-001 through CERI-LIFE-FR-003 | CERTIFIED | Truth table and persisted PostgreSQL vertical parity |
| Alerts | CERI-ALERT-FR-001 through CERI-ALERT-FR-003 | CERTIFIED | Separate opportunity/risk gate tests and first-snapshot vertical test |
| Run orchestration | CERI-RUN-FR-001 through CERI-RUN-FR-004 | CERTIFIED_CODE | Readiness/capability/batched workflow tests |
| Reproducibility | CERI-REP-FR-001 through CERI-REP-FR-003 | CERTIFIED | Deployment manifests plus 200/200 immutable Run 101 hash replay |
| Licensing | CERI-LIC-FR-001 through CERI-LIC-FR-002 | CERTIFIED | Storage projection and exact EODHD purge lineage test |
| Cost | CERI-COST-FR-001 | CERTIFIED_CODE | Request/runtime/response-byte/stored-byte aggregation tests |
| API/UI | CERI-UI-FR-001 through CERI-UI-FR-004 | CERTIFIED | Query/route/template parity tests |
| Non-functional | CERI-NFR-001 through CERI-NFR-007 | CERTIFIED_CODE | TDD record, generic fixtures, lint, migration, PIT, bounded-query tests |

`CERTIFIED_CODE` means the implementation and deterministic certification pass, but post-deployment provider coverage/runtime results are intentionally not claimed before an actual new run.

## Results and verdict

- Calculation/config identity: `ceri-1.2.0` / `2026-08-13-run101-remediation-r1`.
- Full CERI suite: **280 passed** (baseline 218; 62 additional collected cases), zero failures.
- Relevant PostgreSQL vertical/incremental/backlog suite: **13 passed** after updating the first-snapshot expectation; the corrected legacy-vs-batched vertical produces zero first-observation changes and alerts.
- Migration certification: clean-to-head and populated-0041-to-head both pass. Null guidance acceptance becomes false with `LEGACY_ACCEPTANCE_UNKNOWN`; an immutable `ceri-1.1.0` snapshot hash/version is unchanged.
- Active research database: still `0041_sec_incremental_documents`; no schema or historical row mutation was performed.
- Run 101 replay: **200/200 reconstruction matches and 200/200 hash matches** in a read-only transaction. Its historical 200 invalid upgrades and 40 alerts remain preserved, not rewritten.
- SEC labeled precision: **100% accepted precision (4/4 positives), zero golden false positives** on the checked processor fixture. This is an initial gate, not a population-level precision estimate.
- Golden cohort: **10/10 PASS**, all required scenarios, all 11 vertical stages, selected/rejected evidence IDs and reasons, snapshot/API hash parity, and zero first-observation lifecycle/alert events.
- Performance before: Run 101 feature build took about 97 minutes while producing zero usable revision slots.
- Performance after: impossible revision families issue **zero revision calculations**; sparse families build only observed slots; capability construction uses five bulk reads and repeated identical derived hashes avoid a second write. A live wall-clock after value is not claimed until deployment.
- Provider evidence coverage before: Run 101 had 9,156 EODHD estimate observations, 0/4,800 usable revision slots, 160 normalized earnings actuals, 439 normalized catalyst revisions, 192/200 SEC partial runs, and four selected guidance tickers.
- Provider evidence coverage after: deterministic eligibility paths pass, but population coverage is **not yet measured**. It must come from the next licensed post-migration run; no “NO MATERIAL VALUE” provider verdict is issued.

Provider-limited items: EODHD Revenue history remains absent until genuine observations accumulate; query-ticker-only news remains unusable without issuer metadata; SEC tickers without CIK/bootstrap remain degraded/rejected; historical earnings quality remains bounded by provider report-time fields.

Application-limited item: deployment is one additive migration behind. This is the only current rollout blocker discovered by certification.

## Broad 200-ticker decision

**NOT AUTHORIZED YET.** The Golden 10 code gate passed, but the active database is at 0041. Do not enqueue another broad run against that schema.

After an operator-approved backup/deployment window, the release sequence is:

1. Record the deployment SHA/dirty/image identity and take the normal recoverable database backup.
2. Run `alembic upgrade 0042_ceri_run101_fail_closed` against the intended deployment database.
3. Run `alembic current` and require `0042_ceri_run101_fail_closed`.
4. Re-run `pytest tests/ceri -q`, the two migration tests, and `python scripts/qa/certify_ceri_run101_golden.py`.
5. Run SEC readiness preflight in `REQUIRE_READY`; stop if dominant coverage is degraded/rejected.
6. Only then upload/select the generic 200-ticker cohort and schedule the normal batched CERI workflow. Do not reuse Run 101 request keys.
7. Generate the Phase 13 reconciliation artifacts and compare provider acquisition, semantic capability, normalized acceptance, unique information, runtime/cost, and only then predictive value.

The migration and broad-run steps above are intentionally a plan, not actions taken by this implementation task.
