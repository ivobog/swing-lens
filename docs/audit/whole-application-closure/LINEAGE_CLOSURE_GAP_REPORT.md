# Lineage closure gap report

## Executive verdict

- WHOLE-APPLICATION EXECUTION GRAPH COMPLETE: **YES**
- MUTATION WRITER REGISTRY COMPLETE: **YES**
- RECOVERY PATH REGISTRY COMPLETE: **YES**
- NORMAL/CERTIFICATION PATH DIFFERENCES COMPLETE: **YES**
- TRANSACTION BOUNDARIES COMPLETE: **YES**
- GLOBAL LARGE-READ AUDIT COMPLETE: **YES**
- AUTONOMOUS MUTATION INVENTORY COMPLETE: **YES**
- TEST-COVERAGE MATRIX COMPLETE: **YES**
- TOP-DOWN/BOTTOM-UP RECONCILIATION: **PASS**
- R1 UNIFIED RUNTIME MUTATION AUTHORITY: **CLOSED**
- SAFE TO PROCEED TO R2: **YES**
- SAFE TO RESUME LIVE CANARY REMEDIATION: **NO**

“Complete” means the finite current-tree inventory has no unknown caller or writer. R1 closes GAP-001, GAP-002, and GAP-003 with a shared typed authority boundary and disposable-PostgreSQL negative proofs. GAP-004 and GAP-005 remain open and continue to block live canary resumption.

## Exact counts

| Measure | Count | Method |
| --- | ---: | --- |
| Repository files enumerated | 1,746 | `rg --files` |
| FastAPI route registrations | 191 | AST decorator inventory under `app/routers` |
| Runtime `app.routes` registrations | 196 | 191 application registrations plus five framework/static registrations |
| Mutating HTTP registrations | 52 | POST/PUT/PATCH/DELETE decorators |
| Python `__main__` entrypoints | 95 | AST main-guard scan: 5 app, 90 scripts |
| Mutation-indicator script entrypoints | 49 | persistence/transaction/status keyword + manual review |
| Registered durable job types | 34 | executed `default_job_handlers()` mapping read-only |
| Disabled, nonregistered job constants | 2 | Winner training/similarity negative registry contract |
| SQLAlchemy table mappings | 120 | AST `__tablename__` scan |
| Mutable table families | 119 | bottom-up writer reconciliation |
| Intentional read-only mappings | 1 | `engine_parameters` |
| Static mutation-candidate functions | 485 | AST add/delete/DML/status/attribute assignment over-approximation, manually normalized |
| Status-like mutation statements | 229 | AST assignment scan |
| Functions containing transaction primitives | 276 | AST commit/rollback/flush/begin scan |
| Material transaction families | 25 | caller/lock/continuation normalization |
| Autonomous mutating triggers | 17 | startup/loop/scheduler/finalizer/recovery call graph |
| Independent recovery paths | 22 | worker/supervisor/manual/continuation/replay normalization |
| Direct predicate-free ORM read sites | 30 | AST executed-select parent-chain scan |
| Dynamic generic unscoped read families | 11 | `select(model)` helper/caller scan |
| Potentially unbounded read candidates | 41 | direct + dynamic reconciliation |
| High-risk large-payload read families | 7 | payload/cardinality/manual classification |
| Stable execution nodes | 29 | execution registry |
| Stable writer families | 73 | writer registry |
| Stable state-transition families | 70 | transition registry |
| New tables absent from old T14A census | 5 | current schema minus T14A reverse index |

The 485 mutation candidates are intentionally an over-approximation; helpers, object construction, and false positives are included so discovery errs toward inclusion. The authoritative physical result is 119 mutable table families plus one intentional read-only mapping.

## Mechanical discovery proof

The audit used two independent directions:

1. **Top down:** route decorators; upload/pipeline endpoints; application/CLI main guards; lifespan/startup/shutdown; worker and supervisor loops; all `enqueue_job` callers; job handler map; schedulers; `run_after`; continuation/finalizer calls; recovery/fence/watchdog functions; admin/maintenance scripts; migration/bootstrap entrypoints. Recursive references were followed until an external/system entrypoint was reached.
2. **Bottom up:** every `__tablename__`; SQLAlchemy `add/add_all/delete/execute(insert|update|delete)`; field/status assignment; commit/rollback/flush; `FOR UPDATE`; generic repository/model helpers; legacy SQL; backfill/migration writers. Each table was joined to exact writers and then to the top-down graph.

Cross-check 1 reconciled all 34 registered job types to creator, handler, common transition/retry/cancel machinery, authority, and tests. Cross-check 2 reconciled every one of 120 mappings to writer/caller/mode/authority/transaction/test; `sector_rotation_rows` required interprocedural resolution through `_to_row_model`, and five post-T14A tables required a delta census.

## Authority provenance matrix

| Mutation path | Authority required | Where validated | Missing/weak authority possible? |
| --- | --- | --- | --- |
| Upload/raw rows | request body/hash/upload ID | upload service and schema | upload ID alone is not downstream calculation authority |
| Pipeline creation/root enqueue | upload, pipeline, calculation/config, transition plan; certification session | `start_pipeline`, pre-enqueue gate, certification runtime | direct service calls must supply same context |
| Job enqueue | causality/root/parent, idempotency; session in certification | `enqueue_job` -> `require_enqueue_authorized` | safe for queue paths; not used by direct synchronous routes |
| Job claim/heartbeat/terminalization | worker instance + execution token; exact session claim | `claim_next_job`, `heartbeat_job`, `mark_job_*` | worker ownership alone is operational, not semantic authority |
| Supervisor recovery | typed `RecoveryAuthority`; exact-session live-root scope for fencing and exact-session recoverable-root scope for requeue | `_fence_worker`, `fence_stalled_jobs`, `requeue_stalled_jobs` | primitive rejects missing/untyped authority; unrelated or terminal-root lineage is untouched |
| Core calculation writers | Calculation Identity, effective config, source/evidence pins, pipeline scope | domain mutation/core authority services | reduced/standalone routes are rejected |
| Market/IB acquisition | acquisition plan, work scope, refresh cycle, contract/source identity | work-scope identity and repositories | ticker alone insufficient; legacy/manual scripts vary |
| CERI provider/normalize/features | provider request, run/company, PIT cutoff, source records | CERI services/domain writer fences | some global helpers infer scope after loading all rows |
| CERI capture/change | run/calculation/evidence/source bundle/session/cutoff | capture/change services, source mutation authority | R0 baseline commits the Run-166 exact-bundle refresh and tests |
| CERI alerts | change/revision evidence IDs, rule config | alert persistence service | `_eligible_changes` may infer authority from run/ticker after global load |
| CERI purge | provider/license scope, preview manifest hash, confirmation, immutable references | purge service | scope is correct but global materialization creates stale/scale window |
| CERI review/alert ack | target row, human review/local admin + HTTP mutation capability | central route middleware/decorator and row lookup | `NORMAL_ONLY`; certification denial occurs before dependency/handler/database work |
| Setup/lifecycle | handoff manifest, calculation/evidence, original-context or maintenance decision | domain policies/repository + HTTP mutation capability | direct alert state mutation is `NORMAL_ONLY` |
| Winner capture/maturation | prediction/model/evidence/generation, outcome definition/session | Winner authority/evidence services | current/latest reads must not substitute for frozen generation |
| Cohort/publication/model | evidence watermark, generation/publication generation/model lifecycle | row/advisory locks, services, and HTTP mutation capability | direct model admin mutation is `NORMAL_ONLY` |
| Worker SEC deployment registration | typed runtime authority + deployed processor signature | `establish_worker_processor_identity` / `register_deployed_processor` | NORMAL registers; CERTIFICATION performs read-only exact-signature lookup and fails closed if absent/mismatched |
| Maintenance/CLI | operation-specific actor/manifest/database safety | individual script/service | no single global runtime capability; production reach must remain explicit |

Authority inferred from status/latest/ticker/worker alone remains flagged in the deferred findings. R1 removes those ambient inputs as sufficient authority for supervisor requeue, direct certification-time APIs, and startup processor registration.

## Findings

### P0 correctness/authority

No unresolved P0 was proven. This is not a canary approval: R0/R1 are committed and verified, but GAP-004 and GAP-005 remain reachable and block live work.

### P1 production safety

#### GAP-001 — Certification web surface has direct synchronous writers outside the session capability — **CLOSED**

- Path IDs: EXEC-023, WRITE-044/046/058/068, MODE-003/004/005.
- Source: mutating routes including `ceri_routes.review_ceri_event`, CERI/setup alert acknowledge/dismiss, cleanup execute, IB journal exclusion, Winner model retirement and other direct service commits.
- Reachability: the certification supervisor starts the normal web application; all 52 mutating route registrations remain mounted.
- Affected state: CERI review/alerts, setup alerts, operational cleanup, IB journal, Winner model/publication-related state depending endpoint.
- Closure: `RuntimeMutationContextMiddleware` performs a pre-handler, fail-closed lookup of route metadata. `unsafe_route` also wraps direct endpoint invocation so tests/internal direct calls use the same authority check.
- Classification: all 52 mutating registrations carry one explicit `MutationCapability`; 49 are `NORMAL_ONLY`, one pipeline-root POST is `CERTIFICATION_SESSION_SCOPED`, two connection tests are `READ_ONLY`, and zero production routes are `CERTIFICATION_CONTROL`.
- Default deny: an unsafe method matched to an unclassified route is rejected in CERTIFICATION with `UNCLASSIFIED_CERTIFICATION_MUTATION` before dependency resolution.
- Mechanical guard: `app.security.http_mutation_route_registry` plus `tests/test_route_security.py` asserts the complete 52-route registry and zero unclassified mutations.
- Evidence: unit tests cover normal, unclassified, normal-only, approved/unapproved control, correct/wrong/missing session, and real CERI/setup writers. `test_runtime_mutation_authority_postgresql.py` proves denied cleanup leaves the target row byte-for-byte unchanged and the DB dependency is never entered.

#### GAP-002 — Certification worker startup mutates the global SEC processor registry — **CLOSED**

- Path IDs: EXEC-025, WRITE-051, AUTO-007, TX-03, STATE-044.
- Source after closure: `background_worker.run_worker -> processor_lifecycle.establish_worker_processor_identity`.
- Reachability: every durable worker startup, before the work loop.
- Affected state: `ceri_sec_processor_releases`; may insert a DEPLOYED row or update missing git SHA.
- Policy: NORMAL authority retains `register_deployed_processor`; CERTIFICATION authority performs only `db.get` for the deterministic deployed signature and rejects missing or mismatched registered identity.
- Startup proof: `run_worker` constructs the typed session authority after certification-session validation and before processor identity establishment.
- Evidence: lifecycle unit tests cover NORMAL registration, repeated certification idempotence/read-only behavior, missing identity, and mismatch. The PostgreSQL test snapshots every `ceri_sec_processor_releases` column before and after two certification startup identity checks and proves exact equality.

#### GAP-003 — Shared recovery primitives do not require authority intrinsically — **CLOSED**

- Path IDs: REC-005..009, WRITE-007/008, STATE-010/011, TX-22/23.
- Source after closure: all six shared recovery entry calls require keyword-only typed `RecoveryAuthority`; there is no `None`/ambient-mode default.
- Reachability: all current supervisor/worker callers are typed; direct service imports/future callers fail immediately if authority is omitted or untyped.
- Affected state: background jobs, pipeline steps/runs, worker ownership/execution tokens.
- Enforcement: `require_recovery_authority` rejects untyped values; stale/abandoned worker recovery additionally rejects certification authority. Supervisor fencing/reconciliation use the exact-session live-root claim predicate; post-fence requeue uses a separate exact-session root-lineage predicate that admits `STALLED` but never terminal roots.
- Caller closure: six production call sites are structurally enumerated; all supply `authority=`. Worker stale/abandoned recovery supplies NORMAL authority; supervisor watchdog/loss/requeue derives NORMAL or CERTIFICATION authority from the validated supervisor session.
- Evidence: missing/untyped authority tests, cancellation-precedence tests, structural caller guards, existing supervisor-isolation PostgreSQL tests, and a new PostgreSQL before/after proof for unrelated-session nonmutation plus authorized NORMAL transition.

#### GAP-004 — CERI alert rebuild globally loads changes/snapshots/companies

- Path IDs: EXEC-013/022, WRITE-043, READ-031, TX-20.
- Source: `ceri.job_handlers._eligible_changes` -> `_load_rows(select(model))`.
- Reachability: `CERI_ALERT_REBUILD` after every CERI change job and via admin API.
- Affected state: memory/DB load and `ceri_alert_events`; pipeline release waits for it.
- Existing guard: payload filtering by IDs/run/company/ticker/since happens in Python; alert persistence has evidence guards.
- Missing guard: SQL predicates/pagination derived from exact job authority and a bounded payload.
- Tests: functional alert tests only; no query-shape/cardinality test.
- Recommended phase: R2 bounded CERI downstream reads before any canary.

#### GAP-005 — Licensed-data purge materializes the global CERI corpus

- Path IDs: WRITE-045/048, READ-033, TX-25.
- Source: `CeriPurgeService._lifecycle_manifest`, `_load`, `_rows_with_source_ids`.
- Reachability: purge preview and durable execute API/job.
- Affected state: source, estimates, earnings, guidance, revisions, derived/price features, scores, changes, alerts, purge audit.
- Existing guard: explicit EODHD provider/license scope, preview manifest hash, confirmation token, immutable decision-evidence block.
- Missing guard: provider/source-set SQL scoping, streaming/chunking, target-only locks, bounded manifest construction.
- Tests: correctness tests, no production-cardinality/lock-scope proof.
- Recommended phase: R2 purge query/transaction redesign.

### P2 scalability/operability

#### GAP-006 — Catalyst normalization performs a global revision read per changed event

- Path: WRITE-037, READ-032, TX-09.
- Source: `normalization_service.normalize` uses `_load(CeriCatalystEventRevision)` then filters in Python to compute next revision number.
- Guard: event identity and current-row query; missing event-scoped aggregate/lock.
- Tests: functional only. Phase R2.

#### GAP-007 — IB journal mutation and UI paths globally read/lock episode/link/candidate tables

- Path: WRITE-072, READ-013/014/018/019.
- Source: `journal.exclude_execution_fill`, `rebuild_trade_episodes`, `journal_analytics`, `query_service.trade_journal/scanner_runs`.
- Guard: target fill is locked first; missing target-only episode lock and pagination.
- Tests: functional/resilience, no lock-cardinality proof. Phase R2.

#### GAP-008 — Generic CERI helpers permit unscoped dynamic model reads

- Path: READ-034..041.
- Source: query/export/identity/backfill/alert/feature/capture/price-response `_load` helpers.
- Guard: many callers add in-memory scope; missing scope object/SQL predicate requirement at helper signature.
- Tests: no structural ban comparable to current `change_rebuild_service._scoped_scalars`. Phase R2/R3.

#### GAP-010 — CERI change progress writes share the long domain transaction

- Path: EXEC-013, TX-12, AUTO-003.
- Source: `execute_change_detection_job.report_progress` calls `heartbeat_job/record_job_progress` on the same session without committing.
- Guard: company chunk size 25; missing multi-connection visibility or a formally bounded worst-case chunk-time proof.
- Risk: supervisor may observe stale progress and fence a live long chunk.
- Tests: no two-connection progress visibility assertion. Phase R2.

#### GAP-011 — Aggregate/diagnostic scans have no retention/index budget

- Path: READ-002/003/010/012/022/023/025/026.
- Source: background/CERI/setup/Winner operations summaries.
- Guard: small result sets; missing scan/retention budgets.
- Tests: performance tests do not assert query plans for all aggregates. Phase R4 operability.

### P3 maintainability

#### GAP-009 — The writer census drifted from 115 to 120 tables without automatic failure

- Path: writer registry cross-check.
- Source: five scope/manifest tables added after T14A.
- Existing guard: T14A artifacts/tests; missing current-schema-to-registry CI reconciliation.
- Tests: inventory tests validate artifacts but did not force these tables into the old reverse index.
- Phase R3 generated inventories and CI orphan check.

#### GAP-012 — Runtime policy is descriptive in one module and enforced piecemeal elsewhere

- Path: MODE-001..006.
- Source: `CERTIFICATION_DISABLED_AUTOMATIC_WORKFLOWS` is a reporting tuple; callers independently branch.
- Existing guard: many correct explicit branches; missing executable central policy/capability.
- Tests: subsystem tests, no complete conformance matrix.
- Phase R1/R3.

## Previously missed paths

Earlier lineage/remediation work explicitly covered calculation identity, immutable evidence, configuration, readiness, semantic business writers, pipeline CERI scope refresh, Winner scope, historical reconstruction, and the downstream CERI-to-Winner surface. It did not fully enumerate:

- supervisor watchdog, proven-worker-loss, memory-critical, and shutdown recovery as independent job/pipeline writers;
- certification startup's worker registration and SEC processor release mutation;
- direct synchronous admin HTTP writes while the certification web process is active;
- downstream CERI alert rebuild's global reads after change detection;
- normalizer and purge generic global loaders;
- all 95 Python executable entrypoints and 49 mutation-indicator scripts as one production-reachability corpus;
- the five tables added after the T14A schema census;
- progress-heartbeat visibility as a transaction-boundary property.

The old T14A audit was strong within its declared boundary: 187 Python business persistence boundaries, 99 semantic writer/supporting families, 34 canonical writers, 291 business initiator candidates, 46 mutating functions/48 registrations at that time, 34 handlers, 18 CLI candidates plus restore, and 115 tables. `FINAL_DOWNSTREAM_CONTRACT_CLOSURE.md` was similarly bounded to 22 downstream surfaces/213 codes. Neither document claimed the independent supervisor/startup/control-plane closure now required.

## Why they were missed

1. Audits were organized by calculation subsystem and semantic business writer, not by every executable process authority.
2. Normal pipeline progression was followed more deeply than failure, shutdown, restart, watchdog, and startup reconciliation.
3. “Worker disables recovery in certification” was treated as system policy even though the supervisor is an independent writer.
4. Operational writers were classified separately and therefore not always tested against financial certification isolation.
5. Dynamic generic model helpers and Python-side filters defeated simple model-name/static predicate searches.
6. Point-in-time inventories were not continuously reconciled against schema and route growth.
7. Commit/heartbeat boundaries were audited for correctness within services, not as edges that invalidate previously proven authority.

## Objective closure criteria

`ALL MATERIAL PIPELINE PATHS ENUMERATED` may be declared only when all are true:

1. every production writer maps to at least one external/system entry chain;
2. every state transition maps every mutating function;
3. every autonomous trigger is mapped;
4. every recovery/reconciliation path is mapped;
5. NORMAL/CERTIFICATION/test differences are traced through actual callers;
6. every material commit/rollback boundary states revalidation requirements;
7. every predicate-free/large-table read is classified;
8. every continuation/finalizer edge is mapped;
9. every mutation path names its authority token and validation site;
10. every execution/recovery/writer family has direct test status;
11. top-down graph and bottom-up table inventory have no orphan;
12. generated CI checks fail on a new table, job type, mutating route, executable entrypoint, autonomous trigger, or recovery mutator until it is classified.

Criteria 1–11 are satisfied as an audit inventory. R1 implements executable drift guards for mutating HTTP registrations and production recovery/SEC-registration callers. Criterion 12 remains broader than R1 because table/job/writer/transition generation is assigned to R3. GAP-004 and GAP-005 still block canaries.

## Remediation DAG status

```text
R0 freeze and baseline
  - preserve current dirty worktree and audit artifacts
  - decide/commit or discard prior Run-166 remediation separately
  |
  +--> R1 unified runtime mutation authority [COMPLETE]
  |      - typed RuntimeMutationAuthority(session/mode/process/operation)
  |      - central direct-route certification gateway (GAP-001)
  |      - classify/move SEC startup registration (GAP-002)
  |      - require authority in recovery primitives (GAP-003)
  |      - committed PostgreSQL negative/isolation matrix
  |
  +--> R2 bounded reads and long-transaction safety [depends on R1 contracts]
  |      - CERI alert SQL scope/chunks (GAP-004)
  |      - purge set-based streaming/locked recheck (GAP-005)
  |      - catalyst revision event-scoped query (GAP-006)
  |      - IB journal target locks/pagination (GAP-007)
  |      - scoped generic read API and progress visibility (GAP-008/010)
  |      - cardinality/query-count/timeout tests
  |
  +--> R3 executable architecture registry [can begin after R1 interface freezes]
  |      - generate route/job/table/writer/transition inventories
  |      - CI orphan/drift checks (GAP-009/012)
  |      - 34-job seven-dimension conformance test
  |
  +--> R4 operability budgets [depends on R2 query shapes]
  |      - aggregate indexes/retention/query-plan budgets (GAP-011)
  |      - production-cardinality synthetic certification
  |
  '--> R5 pre-canary certification [depends on R1-R4]
         - clean PostgreSQL restore
         - no providers for dry authority/recovery matrix
         - controlled provider-enabled small canary only after all P1 close
         - supervisor/worker kill, cancel, restart, and continuation fault injection
```

## Final conclusion

The application now has a finite current-state map and a fail-closed R1 runtime mutation boundary. GAP-001, GAP-002, and GAP-003 are closed; GAP-004 and GAP-005 remain open. It is safe to begin R2, but not to repair job 43415 or start another canary.
