# T13E â€” Phase-4 Effective Configuration Integration Certification

Certification date: 2026-09-17. Status: PASS.

## 1. Executive verdict

Final evidence: 3447 unique tests passed; broader 3299, PostgreSQL suite 306 (unit overlap 161). No failures/skips/configuration flakes. Seven new native PostgreSQL cases and 39 new authority units (three supplemental same-output cases after broad collection; full native mathematics, no production change). Exact actual Phase-3/current parity: 556 native captured results over 340 selected tests. All 16 lanes PASS. Configured CI Ruff/0080, format, compile and diff gates PASS; 24 unchanged legacy migration lint diagnostics are PRE_EXISTING_UNRELATED, outside configured CI scope.

The machine certificate records exact invocation arguments, report digests, selector verification, performance, inventory counts and scoped boundaries.

PASS. The machine certificate supplies exact selected counts, union accounting, hashes and final gate results.

Certification covers supported newly produced artifacts, the normal durable public upload/pipeline/resume path, registered worker delivery guards, native downstream handlers and addressed immutable history. External IB/provider readiness and acquisition scheduling are explicit test boundaries over cached native raw prices/estimates. No financial calculator, default registry, identity, evidence writer, claim/heartbeat/domain fence or durable loader is replaced. The public upload uses the applicationâ€™s normal SessionLocal/get_db with no dependency override. Root/retry/resume/history processes have distinct PIDs.

## 2. Git baselines and ancestry

The original audit is `3a9d47063be996908b7d5d1cc5769e0bbd033546`; Phase 2 is `7b015124807b8f895911d7e790fcbf01949ff85f`; Phase 3 is `5422bcdf7703db891810d9e9c20a8f1770241fc9`. T13E started clean at `b846e37bdfe823416ccb8c9b98c452243cb5ee0a`, on T13Dâ€™s branch. Work uses `codex/t13e-phase4-config-certification`. Local main and origin/main were both `febe376be67f01589199cd3ba55af98fd54001ed`. All required Phase-2/3/T13A-D ancestors were verified before edits.

## 3. Phase-4 chain

T13A `7fa173c1e34752d457043fce5b4c5f22ec6e25d5` â†’ T13B `ea8d48a510acbee9b88886390a9f775a36a4510a` â†’ T13C `6884b0d6a9f5179a0ccdd97b8215f0e542f0032d` â†’ T13D `b846e37bdfe823416ccb8c9b98c452243cb5ee0a`. The focused T13E commit contains certification, five deployed defects and regressions; final commit identity is provided by git and the final response, avoiding a self-referential embedded commit hash.

## 4. Migration 0080 certification

Only migration **0080_effective_configuration** (parent **0079_setup_lifecycle_alert_ev**) is required. Fresh upgrade, populated 0079 upgrade, populated downgrade/re-upgrade, Alembic check/ORM parity, constraint validation and immutable UPDATE/DELETE attacks are exercised on verified disposable PostgreSQL 16. No schema changes beyond 0080, production migration or historical backfill. Supported retention cannot delete a referenced authority record: all three tables reject UPDATE/DELETE; bindings use RESTRICT owner/anchor foreign keys.

## 5. Configuration schema tables

`effective_configuration_records`: resolution_hash VARCHAR(64) PK, namespace TEXT, semantic_hash VARCHAR(64), payload_json JSONB, created_at TIMESTAMPTZ. `execution_configuration_anchors`: anchor_id VARCHAR(64) PK, fingerprint VARCHAR(64), payload_json JSONB. `execution_configuration_bindings`: binding_key VARCHAR(80) PK, anchor_id FK RESTRICT, unique optional job_id/pipeline_run_id/winner_cohort_generation_id FKs RESTRICT, exactly-one-scope CHECK. Each table has a BEFORE UPDATE OR DELETE immutable trigger backed by reject_effective_configuration_mutation; ORM mutation protection complements PostgreSQL triggers. Records embedded in anchor JSON are validated on every load; there is no invented relational FK from JSON to records.

## 6. Effective-config contract verification

Sources resolve before financial math into a typed `EffectiveConfigurationSnapshot`. Its family declares namespace, schema, resolver/version, precedence, value types and classifications. Its `EffectiveConfigurationIdentity` becomes the configuration dimension of Calculation Identity. Immutable evidence retains that exact snapshot. The durable compositional anchor addresses retained records and is separately bound to the owning job/pipeline/generation.

Semantic hash includes the family/schema and behavior-affecting typed values. Resolution hash additionally addresses retained provenance and operational values. Identical semantics with different source provenance keep the financial identity while retaining different resolution evidence. Source locations, descriptions and transport controls cannot invalidate unrelated financial policy. Semantic equality does not prove algorithm correctness, input completeness, software compatibility or a historical acquisition plan.

BEHAVIORAL, OPERATIONAL, OBSERVABILITY, DISPLAY and SECURITY_SECRET are declared entry classifications. Behavioral values alone govern financial semantic hashing. Secrets are removed before retained canonical material is built; classification and secret-key guards prevent transport credentials from becoming financial keys. The source audit separately classifies scheduling, explicit current rules, legacy and acquisition boundaries.

## 7. Resolver inventory

The full root bundle has **51 scoped entries**: 8 core entries (Fundamental, Technical, Combined, five Ranking profiles), 8 contextual entries (Regime, Sector, CERI, five IBMI modules), 9 decision entries (Setup, Lifecycle, two Alerts, CERI changes, four Winner families), 23 readiness policies and 3 execution adapters. There are **14 named family resolver functions plus the bulk root composer (15 authority entry functions)**; repeated scoped calls are not extra resolver implementations. The configuration matrix lists all 16 required subsystem rows.

## 8. Core config certification

Fundamental freezes native v2 scoring plus CSV column aliases before mapping; family schema is now `core.fundamental-v2`. Technical freezes selected Pine/v4/v5 behavior and benchmark selection; inactive v5 and irrelevant Pine labels remain excluded. Combined owns its decision/weight policy and binds upstream immutable evidence. Ranking freezes the selected normalized profile, preserving profile-name collisions as distinct behavior. Changes to Defensive Quality cannot invalidate Momentum Swing. The native default worker executes all four after every current YAML source becomes invalid.

## 9. Contextual config certification

Regime and Sector retain native typed policy, thresholds, weights, freshness and fallback meaning. CERI retains confidence/freshness/provider priority/consumer coupling; downstream changes and alerts have their own decision families. IBMI freezes the selected histogram/liquidity/options_activity/short_pressure/volatility metric section and contributor behavior. Retry/network budgets remain operational. IBMI enum names are normalized before delivered lookup. Native PostgreSQL tests cover all constituent families; the fresh-process master adds actual CERI raw-estimate rebuild/capture/change children and IBMI Liquidity.

## 10. Decision config certification

Setup snapshots retain signal registry, family, confidence, actionability and freshness policies before normalization. Lifecycle evaluation and transitions retain their own state/phase/episode/observation-gap policy. Setup and CERI Alerts freeze selected rule values, cooldown and dedup semantics, with immutable rule/decision evidence. Current database rule mutation cannot rewrite a historical decision. Native master creates Setup/Winner, runs observation-gap maintenance, and produces an actual expiry AlertDecision under C1 after live sources become unusable C2.

## 11. Winner config certification

Winner prediction, outcome, cohort and generation have distinct behavioral roots in `WINNER_ROOTS`. Cohort minimum-N changes do not change prediction semantics. Original prediction vectors and outcome contracts remain retained. Maturation uses original retained same-bar/proxy policy and versioned outcomes. Latest rescore creates new estimate/model lineage over the captured vector and does not mutate original prediction policy. Generation/slice bindings preserve original cohort policy; legacy generations may create honestly labeled CURRENT_RULES_LEGACY_GENERATION proof without backfilling their original authority.

## 12. Readiness policy authority

All **23 named native consumer policies** are inventoried and individually executed under C1; changing each policy version to C2 fails with READINESS_POLICY_CONFIGURATION_ANCHOR_MISMATCH while the retained decision DTO and policy snapshot remain unchanged. Policy meaning participates in its own effective family and the retained native consumer eligibility decision. No current policy reinterpretation of old readiness is certified.

## 13. Calculation Identity integration

Sources resolve before financial math into a typed `EffectiveConfigurationSnapshot`. Its family declares namespace, schema, resolver/version, precedence, value types and classifications. Its `EffectiveConfigurationIdentity` becomes the configuration dimension of Calculation Identity. Immutable evidence retains that exact snapshot. The durable compositional anchor addresses retained records and is separately bound to the owning job/pipeline/generation.

Behavioral configuration changes invalidate reuse even when outputs happen to remain equal. The original algorithm/input/temporal/readiness dimensions remain independently enforced.

## 14. Immutable evidence integration

Certified evidence writers require a typed snapshot matching the exact Calculation Identity. CoreCalculationEvidence retains payload plus at-creation readiness/configuration metadata; decision ledgers/sealed lineage retain their own rules. Historical hydration uses the retained payload, not current resolvers. Compatibility projection comparison validates financial fields and source pins before canonicalizing physical row addresses to immutable evidence addresses. At-creation metadata is reattached from the ledger, never reevaluated under current rules.

## 15. Config granularity audit

Required negative isolation attacks are covered by core/contextual/decision focused tests: Defensive vs Momentum Swing, inactive v5, unrelated upstream policy, IBMI retry budgets, Winner cohort minimum-N vs prediction, and alert transport secrets. Same-input/same-output/different-policy Ranking, Regime, CERI and native Setup attacks bind different identities. Default/env/profile/database same-semantic provenance is retained independently.

Semantic hash includes the family/schema and behavior-affecting typed values. Resolution hash additionally addresses retained provenance and operational values. Identical semantics with different source provenance keep the financial identity while retaining different resolution evidence. Source locations, descriptions and transport controls cannot invalidate unrelated financial policy. Semantic equality does not prove algorithm correctness, input completeness, software compatibility or a historical acquisition plan.

## 16. Historical config reads

A second fresh application process retrieves unchanged evidence/config records/Lifecycle/transitions/Alerts/Winner lineage while every current YAML file is invalid. The master then restores valid C2 changes across 15 financial namespaces, enqueues new work through the public route, and verifies C1 history unchanged while C2 receives new identities and completes native calculations. Frozen neutral DTOs remain readable across supported policy/code changes even when execution compatibility fails closed.

## 17. Legacy behavior

Old artifacts without original authority remain UNKNOWN/NOT_PROVIDED/legacy; neither current files nor a guessed hash can certify them. Existing schema-v1 Fundamental snapshots remain neutrally readable but cannot execute the new alias-dependent family. Pre-0080 queued work without a retained binding fails closed. Populated downgrade/re-upgrade preserves preexisting business data; dropping authority tables cannot recreate missing historical configuration. No legacy backfill or production rewrite was performed.

## 18. Pipeline anchor composition

Root enqueue resolves once and persists `effective_configuration_records` by resolution hash. Anchor `fingerprint` hashes sorted semantic identities; `anchor_id` is the integrity hash covering reference addresses and provenance. Tiny payload references carry only anchor_id/fingerprint. Same semantics/different provenance may change integrity/address while keeping the business fingerprint. Behavioral switch drift changes the fingerprint; worker/runtime operational drift does not. Immutable bindings identify one job, pipeline or Winner generation.

## 19. Queued-job drift attack

Certification covers supported newly produced artifacts, the normal durable public upload/pipeline/resume path, registered worker delivery guards, native downstream handlers and addressed immutable history. External IB/provider readiness and acquisition scheduling are explicit test boundaries over cached native raw prices/estimates. No financial calculator, default registry, identity, evidence writer, claim/heartbeat/domain fence or durable loader is replaced. The public upload uses the applicationâ€™s normal SessionLocal/get_db with no dependency override. Root/retry/resume/history processes have distinct PIDs.

The public route exits after persisting C1. Every YAML source is replaced by invalid C2, then a fresh default worker executes native C1. The decision-graph master subsequently creates valid C2 through a new public enqueue and validates all 15 changed financial namespace identities.

## 20. Retry attack

A real acquisition-readiness interruption occurs after native Fundamental commit. The same unfinished job is marked retryable, current YAML becomes invalid, and a new actual worker attempt reuses the exact C1 Fundamental evidence and finishes. Duplicate completed-work rejection is separately asserted.

Queueing, unfinished-work retry, anchored public resume, child enqueue outside any live ContextVar, and stale-lease reclaim use persisted C1. Reclaim changes execution ownership token, not semantic authority. A parent reference mismatch or missing family never invokes a live resolver. Public resume retains market context and decision handoff; the handoff is frozen before provider scheduling can interrupt a completed core graph. A duplicate original job after successful resume correctly fails DECISION_MANIFEST_MISMATCH once the decision pointer has advanced; that rejection is not a configuration change.

## 21. Resume attack

Actual public resume runs after native core commits and an injected external acquisition-scheduler failure. New continuation keeps C1 and the original temporal context/handoff; native downstream calculators complete. All financial compatibility fields and source addresses remain validated.

Five defects were corrected: (1) freeze CSV aliases before Fundamental mapping, schema v2 and supplied-empty-map no fallback; (2) accept immutable Combined source addresses only with exact ledger source pins; (3) normalize IBMI enum module case before delivered lookup; (4) freeze decision handoff before external CERI acquisition scheduling can interrupt the committed core graph; (5) compare resume projections in the ledgerâ€™s canonical shape while preserving financial/source checks and at-creation metadata. The final source-pin regression also requires Fundamental lookup in its own table. Numeric algorithms, legacy fixtures, readiness rejection and run-start/decision-manifest guards remain intact.

## 22. Child inheritance

Actual native CERI rebuild/capture/change, IBMI, lifecycle maintenance, Alert rebuild and Winner capture children inherit the parent C1 reference. Enqueue also occurs outside live delivery ContextVars. Existing PostgreSQL coverage retains continuation/cohort binding behavior.

## 23. Stale lease reclaim

A task worker actually claims the root, exits with its lease expired by fixture clock adjustment, then a new production worker recovers and executes it. The token changes; immutable binding, fingerprint and family identities remain C1. No semantic timestamp is advanced for this execution-ownership test.

## 24. Missing-anchor/tamper attacks

The production `default_job_handlers()` registry has **34 handlers**, with **28 protected business/delivery handlers** and six nonbusiness acquisition/runtime handlers. The worker validates its immutable binding, reference integrity, family/schema, required entries, material authority and supported code policy before the native handler. All 28 missing-binding attacks execute the real default registry, fail closed and leave zero financial evidence. Mixed acquisition/business CERI and IB handlers remain protected conservatively; external acquisition planning is not frozen by this certification.

Valid-but-other-anchor, payload integrity, reference address, missing-family, unknown-authority and unsupported code-policy attacks are covered in focused PostgreSQL tests. They reject before financial evidence writes. Immutable no-op UPDATE and DELETE are rejected in all three tables.

## 25. Current-rules explicit boundaries

The native master executes a parentless C2 DRY_RUN_REPLAY over real retained snapshots and verifies no historical ledger mutation. Explicit current-rule repair/replay/rescore operations freeze their current policy for the new operation and retain honest CURRENT_STATE_REPAIR/CURRENT_RULES_RETROSPECTIVE or new estimate/model lineage. They do not rewrite original evidence or claim ORIGINAL_CONTEXT. The auditâ€™s six native parser call sites are new-root source reads, not six repair APIs. Direct unanchored legacy APIs are explicit current-rule entry points outside durable graph certification. **Original-context replay/reconstruction claim: NONE.**

## 26. Startup/restart integration

Certification covers supported newly produced artifacts, the normal durable public upload/pipeline/resume path, registered worker delivery guards, native downstream handlers and addressed immutable history. External IB/provider readiness and acquisition scheduling are explicit test boundaries over cached native raw prices/estimates. No financial calculator, default registry, identity, evidence writer, claim/heartbeat/domain fence or durable loader is replaced. The public upload uses the applicationâ€™s normal SessionLocal/get_db with no dependency override. Root/retry/resume/history processes have distinct PIDs.

Fresh processes import ordinary native loaders without import-cycle caches. Historical reads are independent of execution-process ContextVars. A retained unsupported schema/code policy can be read neutrally but cannot silently execute under new policy.

## 27. Alert rule drift

Setup snapshots retain signal registry, family, confidence, actionability and freshness policies before normalization. Lifecycle evaluation and transitions retain their own state/phase/episode/observation-gap policy. Setup and CERI Alerts freeze selected rule values, cooldown and dedup semantics, with immutable rule/decision evidence. Current database rule mutation cannot rewrite a historical decision. Native master creates Setup/Winner, runs observation-gap maintenance, and produces an actual expiry AlertDecision under C1 after live sources become unusable C2.

Existing actual PostgreSQL alert history tests mutate database rules/current files and verify original rule evidence, predecessor bounds and decisions unchanged; native rule seeding under C1 preserves existing C2 database authority.

## 28. Lifecycle rule drift

Setup snapshots retain signal registry, family, confidence, actionability and freshness policies before normalization. Lifecycle evaluation and transitions retain their own state/phase/episode/observation-gap policy. Setup and CERI Alerts freeze selected rule values, cooldown and dedup semantics, with immutable rule/decision evidence. Current database rule mutation cannot rewrite a historical decision. Native master creates Setup/Winner, runs observation-gap maintenance, and produces an actual expiry AlertDecision under C1 after live sources become unusable C2.

Lifecycle evaluation carries its own original policy and transition evidence. C2 observation-gap changes affect new operations; historical C1 evaluations/transitions remain unchanged. Current repair labels and legacy missing dimensions are preserved.

## 29. Winner prediction/rescore/maturation config

Winner prediction, outcome, cohort and generation have distinct behavioral roots in `WINNER_ROOTS`. Cohort minimum-N changes do not change prediction semantics. Original prediction vectors and outcome contracts remain retained. Maturation uses original retained same-bar/proxy policy and versioned outcomes. Latest rescore creates new estimate/model lineage over the captured vector and does not mutate original prediction policy. Generation/slice bindings preserve original cohort policy; legacy generations may create honestly labeled CURRENT_RULES_LEGACY_GENERATION proof without backfilling their original authority.

## 30. Secret campaign

The sentinel campaign covers a real PostgreSQL login password in DATABASE_URL, actual EODHD/IB credentials in fresh public/worker processes, and all retained records, anchors, evidence, repr, serialized output and captured stdout/stderr. Sentinel must never appear in authority material. Six canonical contract cases cover DSN/provider/IB/webhook/SMTP/session secret keys. There is no native SMTP/webhook/session-secret connector in this repository, so those cases prove canonical exclusion rather than an invented deployed connector. Transport credentials do not change AlertDecision semantics.

## 31. Static authority audit

`T13E_configuration_authority_audit.json` is a deterministic repository-wide AST/source-read and keyword census, with source hashes, exact paths/lines and classification reasons. It scans app/scripts/alembic/tools and repository-root Python, excluding tests. It inventories Settings attributes/getattr, raw env/file/YAML/config loaders and selected database rule/model/generation/authority reads. Keyword hits are discovery only, not evidence that arithmetic constants are live configuration. Manual review traces root resolvers â†’ scoped delivered native adapters â†’ writers/history and classifies acquisition, legacy, display, model promotion and operational controls. Native invalid-file/process attacks supply execution proof; the census does not claim arbitrary Python data-flow verification. Material certified UNKNOWN/POTENTIAL/CONFIRMED bypass counts must all be zero. Detailed counts and inventory digest are in the machine certificate.

## 32. Deployed graph certification

Certification covers supported newly produced artifacts, the normal durable public upload/pipeline/resume path, registered worker delivery guards, native downstream handlers and addressed immutable history. External IB/provider readiness and acquisition scheduling are explicit test boundaries over cached native raw prices/estimates. No financial calculator, default registry, identity, evidence writer, claim/heartbeat/domain fence or durable loader is replaced. The public upload uses the applicationâ€™s normal SessionLocal/get_db with no dependency override. Root/retry/resume/history processes have distinct PIDs.

The production `default_job_handlers()` registry has **34 handlers**, with **28 protected business/delivery handlers** and six nonbusiness acquisition/runtime handlers. The worker validates its immutable binding, reference integrity, family/schema, required entries, material authority and supported code policy before the native handler. All 28 missing-binding attacks execute the real default registry, fail closed and leave zero financial evidence. Mixed acquisition/business CERI and IB handlers remain protected conservatively; external acquisition planning is not frozen by this certification.

Five defects were corrected: (1) freeze CSV aliases before Fundamental mapping, schema v2 and supplied-empty-map no fallback; (2) accept immutable Combined source addresses only with exact ledger source pins; (3) normalize IBMI enum module case before delivered lookup; (4) freeze decision handoff before external CERI acquisition scheduling can interrupt the committed core graph; (5) compare resume projections in the ledgerâ€™s canonical shape while preserving financial/source checks and at-creation metadata. The final source-pin regression also requires Fundamental lookup in its own table. Numeric algorithms, legacy fixtures, readiness rejection and run-start/decision-manifest guards remain intact.

## 33. Performance/query sanity

The native master cold-loads a persisted bundle in a new Session: exactly two SELECTs (anchor plus batched retained records), then executes 100 native Setup builds and 100 native Winner feature extractions with zero extra configuration database SELECTs and Path.open blocked. Ranking/Regime durations are measured from the real public rootâ€™s performance result. Timing values, bundle size, process and test digests are retained in the machine certificate. Native synthetic computational inputs and cached acquisition are explicit; there is no production latency SLA or claimed throughput improvement.

## 34. Business parity

Identical native capture plugins execute the unchanged financial fixtures in the actual detached Phase-3 checkout (5422bcdf) and the final current code. 309 native decision/context tests produce 537 captured results; 31 pure Core tests produce 19 full native DTO captures. All **556 results** must be exactly equal under identical inputs. The only excluded adoption metadata is the newly added configuration_semantics key in the existing Setup financial probe; numeric outputs, classifications, dates, original input lineage and native compatibility hashes remain. Pure Fundamental/Pine/v4 DTO captures exclude no fields. This is actual checked-out baseline behavior, not current code masquerading as the baseline.

## 35. PostgreSQL certification

PostgreSQL 16 runs in task-owned loopback Docker container swinglens-t13e-pg-20260916, port 26326, tmpfs test data, fsync/synchronous_commit/full_page_writes ON. Fixtures create and verify GUID swinglens_pytest_* databases before Alembic and drop only their own databases. A task-only sentinel login is created/granted/dropped for the real DSN campaign. No production database, unrelated Docker service or user worktree is touched. Test infrastructure is not the production runtime.

Only migration **0080_effective_configuration** (parent **0079_setup_lifecycle_alert_ev**) is required. Fresh upgrade, populated 0079 upgrade, populated downgrade/re-upgrade, Alembic check/ORM parity, constraint validation and immutable UPDATE/DELETE attacks are exercised on verified disposable PostgreSQL 16. No schema changes beyond 0080, production migration or historical backfill. Supported retention cannot delete a referenced authority record: all three tables reject UPDATE/DELETE; bindings use RESTRICT owner/anchor foreign keys.

## 36. Phase-3 regression

Final broader/PostgreSQL outcome sets are checked against the prior Phase-3 selected node IDs. Native readiness/config adoption tests add to that inventory; cached earlier PASS reports are selectors, not substitutes for final execution.

## 37. Phase-2 regression

The seven original Phase-2 PostgreSQL files select 60 native immutable-evidence cases. The final lane also executes current Phase-2 unit/history guards; exact final counts are in the machine certificate.

## 38. Phase-1 regression

All 135 prior Phase-1 selected node IDs must occur as PASS in the final broader lane. Their Calculation Identity/input/source mismatch guards remain unchanged.

## 39. Phase-0 regression

Prior Phase-0 temporal/fence inventories (86 plus 71 supplementary selected nodes) and 36 native temporal/preflight/Winner PostgreSQL cases must be green in final outcomes. Invocation overlap is deduplicated; no cached pass is counted as execution.

## 40. Failure/flakiness analysis

Failures are classified rather than suppressed. CSV aliases are PHASE4_DEFECT; the other four native graph failures are DEPLOYED_INTEGRATION_DEFECT. Fixture setup mistakes (native Setup write DTO field assumptions, explicit UTF-8 document validation, safe test DB identity, static/template files, native bar timeframe, valid policy ranges/structure, expired ORM fixture access, tzdata path guard, duplicate-after-completed expectation) are TEST_INFRASTRUCTURE. The source-model mistake introduced in the new resume verifier was found by final deployed regression and corrected before restarting all final lanes. Prior incomplete/interrupted certification attempts are not counted as PASS or flaky. No confirmed flake, xfail, blanket skip, monkeypatched financial result or widened tolerance is used. Complete lanes restart after the last production correction.

Five defects were corrected: (1) freeze CSV aliases before Fundamental mapping, schema v2 and supplied-empty-map no fallback; (2) accept immutable Combined source addresses only with exact ledger source pins; (3) normalize IBMI enum module case before delivered lookup; (4) freeze decision handoff before external CERI acquisition scheduling can interrupt the committed core graph; (5) compare resume projections in the ledgerâ€™s canonical shape while preserving financial/source checks and at-creation metadata. The final source-pin regression also requires Fundamental lookup in its own table. Numeric algorithms, legacy fixtures, readiness rejection and run-start/decision-manifest guards remain intact.

## 41. Finding reconciliation

| Finding | T13E reconciliation | Remaining boundary |
| --- | --- | --- |
| RANK-005 | CLOSED for selected effective profile authority | Algorithm/calibration separate |
| CORE-008 | CLOSED; complete Regime effective config recoverable | Acquisition plan separate |
| CERI-001 | CONFIGURATION PORTION CLOSED | Provider ordering/selection algorithm OPEN |
| CERI-007 | CONFIGURATION PORTION CLOSED | Stale-penalty mathematics OPEN/PARTIAL |
| CERI-008 | CLOSED for new native/downstream rule authority | Legacy original-rule reconstruction deferred |
| SETUP-007 | Rule history and bounded cooldown CLOSED for certified alerts | Original-rule reconstruction of unanchored rebuilds/legacy remains PARTIAL; temporal fix not reopened |
| SETUP-010 | CONFIGURATION PORTION CLOSED | Other algorithm/reconstruction portions not claimed |
| WIN-003 | CLOSED | Original Ranking readiness, exact profile and temporal identity now enforced |
| PIPE-005/006/007 | CONFIGURATION PORTIONS CLOSED | Entry-point/target/refresh/recovery scope separate |
| XINT-010 | CLOSED for certified business configuration graph; repository-wide PARTIAL | Acquisition/legacy entry-point unification deferred |
| INV-CONFIG-001 | ENFORCED across certified graph/history/durable execution | Legacy UNKNOWN and privileged SQL excluded |

Original definitions were checked in audit 02 (CORE-008), 03 (CERI-008), 05 (SETUP-007) and 06 (WIN-003). WIN-003 contains Ranking readiness/profile/context authority; prior Phase-0/3 temporal/readiness enforcement plus this exact configuration certification closes that original scope. SETUP-007 also requests original-rule rebuild semantics: certified retained decisions and upper-bounded cooldown are closed, while an unanchored current-rule rebuild is a new operation and does not reconstruct absent original authority. That reconstruction boundary remains explicit; no previously closed temporal portion is reopened.

## 42. INV-CONFIG certification

Sources resolve before financial math into a typed `EffectiveConfigurationSnapshot`. Its family declares namespace, schema, resolver/version, precedence, value types and classifications. Its `EffectiveConfigurationIdentity` becomes the configuration dimension of Calculation Identity. Immutable evidence retains that exact snapshot. The durable compositional anchor addresses retained records and is separately bound to the owning job/pipeline/generation.

Queueing, unfinished-work retry, anchored public resume, child enqueue outside any live ContextVar, and stale-lease reclaim use persisted C1. Reclaim changes execution ownership token, not semantic authority. A parent reference mismatch or missing family never invokes a live resolver. Public resume retains market context and decision handoff; the handoff is frozen before provider scheduling can interrupt a completed core graph. A duplicate original job after successful resume correctly fails DECISION_MANIFEST_MISMATCH once the decision pointer has advanced; that rejection is not a configuration change.

Configuration identity is a declared behavior/provenance proof, never a complete reconstruction proof. Supported authority deletion/mutation is blocked. INV-CONFIG-001 is enforced within the certified boundary; legacy absent originals are never promoted.

## 43. Residual risks

Opaque native CERI/provider-selection and Winner compatibility hashes remain conservative independent guards. Configuration authority does not repair provider precedence algorithms or stale-penalty mathematics. Source/temporal manifests and acquisition completeness remain separate. Embedded snapshots add storage; the measured root bundle is roughly 1.38 MB before database encoding. No production throughput/SLA, crash durability, arbitrary injected Python safety or privileged-SQL tamper governance is claimed. Secrets must continue to be excluded when new settings/connectors are introduced.

Original-context replay/reconstruction; acquisition-plan semantics; refresh-cycle identity; target-scope freezing; entry-point unification; privileged direct-SQL governance; long-term configuration retention/storage optimization. No Phase-5 implementation is included.

## 44. Final Phase-4 verdict

PASS. See the machine certificate and final commit for lane evidence. No Phase-5 work, production rewrite, legacy backfill, merge or push.

| Finding | T13E reconciliation | Remaining boundary |
| --- | --- | --- |
| RANK-005 | CLOSED for selected effective profile authority | Algorithm/calibration separate |
| CORE-008 | CLOSED; complete Regime effective config recoverable | Acquisition plan separate |
| CERI-001 | CONFIGURATION PORTION CLOSED | Provider ordering/selection algorithm OPEN |
| CERI-007 | CONFIGURATION PORTION CLOSED | Stale-penalty mathematics OPEN/PARTIAL |
| CERI-008 | CLOSED for new native/downstream rule authority | Legacy original-rule reconstruction deferred |
| SETUP-007 | Rule history and bounded cooldown CLOSED for certified alerts | Original-rule reconstruction of unanchored rebuilds/legacy remains PARTIAL; temporal fix not reopened |
| SETUP-010 | CONFIGURATION PORTION CLOSED | Other algorithm/reconstruction portions not claimed |
| WIN-003 | CLOSED | Original Ranking readiness, exact profile and temporal identity now enforced |
| PIPE-005/006/007 | CONFIGURATION PORTIONS CLOSED | Entry-point/target/refresh/recovery scope separate |
| XINT-010 | CLOSED for certified business configuration graph; repository-wide PARTIAL | Acquisition/legacy entry-point unification deferred |
| INV-CONFIG-001 | ENFORCED across certified graph/history/durable execution | Legacy UNKNOWN and privileged SQL excluded |


### Final lane accounting

| Lane | Passed selected cases | Boundary |
| --- | --- | --- |
| T13E adversarial integration | 46 | Final 7 native PG cases plus 39 authority attacks (36 broader/PG plus 3 supplemental same-output native cases) |
| migration 0080 | 23 | 0080 targeted and populated native downgrade case; scope overlaps durable lane |
| deployed public-entry flow | 5 | Two master variants, native resume, unfinished retry and actual DSN public-worker test |
| durable anchors | 27 | Includes all-34 classification/28 missing-binding guard test |
| downstream decisions | 50 | Additional native master and existing full downstream modules in broader lane |
| contextual config | 34 | Regime/Sector/CERI/five IBMI modules |
| core config | 29 | Own family drift/isolation and native persisted evidence |
| readiness-policy authority | 23 | Individually parameterized native C1 evaluate/C2 version rejection |
| Phase-3 | 563 | Prior selected IDs rerun in final outcomes; native PG adds separate coverage |
| Phase-2 | 60 | Seven original PostgreSQL files; further units/history overlap broader |
| Phase-1 | 135 | Prior selected IDs rerun in final broader |
| Phase-0 | 157 | Selected invocations may overlap; native temporal PG separately covered |
| PostgreSQL | 306 | PG suite contains configuration unit overlap; actual integration count separately recorded |
| behavior parity | 340 | 309 context/decision plus 31 pure core; actual baseline and current; 556 captures |
| broader repository | 3299 | CI-style nonexternal/no-e2e lane, exclusions explicit |
| static gates | static commands | Configured CI Ruff plus 0080; changed-file format, compile, git diff; legacy diagnostics separately classified |

Selected lane counts overlap; unique union above is authoritative. Parity is repeated comparison, not extra unique test cases. Broad deselections are 7 explicit external cases; warnings are 22 broader and 172 PostgreSQL (repeated invocation warnings overlap).


### Changed-file manifest and cleanup

- `app/services/column_mapper.py`
- `app/services/configuration_delivery.py`
- `app/services/contextual_effective_configuration.py`
- `app/services/core_calculation_evidence.py`
- `app/services/core_effective_configuration.py`
- `app/services/fundamental_score_service.py`
- `app/services/pipeline_executor.py`
- `app/services/pipeline_service.py`
- `app/services/transition_preflight_plan_service.py`
- `docs/architecture/SWINGLENS_CALCULATION_LINEAGE_REGISTRY.md`
- `docs/architecture/SWINGLENS_EFFECTIVE_CONFIGURATION_PHASE4_CERTIFIED.md`
- `docs/audit/calculation-lineage/08_synthesis_report.md`
- `docs/remediation/calculation-lineage/T13E_configuration_authority_audit.json`
- `docs/remediation/calculation-lineage/T13E_configuration_certification_matrix.md`
- `docs/remediation/calculation-lineage/T13E_durable_anchor_matrix.md`
- `docs/remediation/calculation-lineage/T13E_phase4_configuration_certification.json`
- `docs/remediation/calculation-lineage/T13E_phase4_configuration_certification.md`
- `scripts/qa/t13e_authority_audit.py`
- `scripts/qa/t13e_behavior_probe.py`
- `scripts/qa/t13e_configuration_benchmark.py`
- `scripts/qa/t13e_core_behavior_probe.py`
- `scripts/qa/t13e_deployed_probe.py`
- `tests/integration/test_phase4_configuration_certification_postgresql.py`
- `tests/test_phase4_configuration_certification.py`
- `tests/test_phase4_same_output_configuration.py`

Only the verified task-owned PostgreSQL container and detached Phase-3 worktree were removed after the final native runs. Both pre-existing worktrees and Prometheus/Grafana containers remain. Source hashes, actual baseline/plugin verification, report digests and exact financial captures are recorded in the machine certificate.
