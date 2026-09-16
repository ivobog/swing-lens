# SwingLens Effective Configuration — Phase 4 Certified Architecture

Certification date: 2026-09-17. Status: PASS.

## 1. Certified baseline

The original audit is `3a9d47063be996908b7d5d1cc5769e0bbd033546`; Phase 2 is `7b015124807b8f895911d7e790fcbf01949ff85f`; Phase 3 is `5422bcdf7703db891810d9e9c20a8f1770241fc9`. T13E started clean at `b846e37bdfe823416ccb8c9b98c452243cb5ee0a`, on T13D’s branch. Work uses `codex/t13e-phase4-config-certification`. Local main and origin/main were both `febe376be67f01589199cd3ba55af98fd54001ed`. All required Phase-2/3/T13A-D ancestors were verified before edits.

T13A `7fa173c1e34752d457043fce5b4c5f22ec6e25d5` → T13B `ea8d48a510acbee9b88886390a9f775a36a4510a` → T13C `6884b0d6a9f5179a0ccdd97b8215f0e542f0032d` → T13D `b846e37bdfe823416ccb8c9b98c452243cb5ee0a`. The focused T13E commit contains certification, five deployed defects and regressions; final commit identity is provided by git and the final response, avoiding a self-referential embedded commit hash.

## 2. Effective configuration model

Sources resolve before financial math into a typed `EffectiveConfigurationSnapshot`. Its family declares namespace, schema, resolver/version, precedence, value types and classifications. Its `EffectiveConfigurationIdentity` becomes the configuration dimension of Calculation Identity. Immutable evidence retains that exact snapshot. The durable compositional anchor addresses retained records and is separately bound to the owning job/pipeline/generation.

## 3. Semantic vs provenance identity

Semantic hash includes the family/schema and behavior-affecting typed values. Resolution hash additionally addresses retained provenance and operational values. Identical semantics with different source provenance keep the financial identity while retaining different resolution evidence. Source locations, descriptions and transport controls cannot invalidate unrelated financial policy. Semantic equality does not prove algorithm correctness, input completeness, software compatibility or a historical acquisition plan.

## 4. Configuration classifications

BEHAVIORAL, OPERATIONAL, OBSERVABILITY, DISPLAY and SECURITY_SECRET are declared entry classifications. Behavioral values alone govern financial semantic hashing. Secrets are removed before retained canonical material is built; classification and secret-key guards prevent transport credentials from becoming financial keys. The source audit separately classifies scheduling, explicit current rules, legacy and acquisition boundaries.

## 5. Resolver graph

The full root bundle has **51 scoped entries**: 8 core entries (Fundamental, Technical, Combined, five Ranking profiles), 8 contextual entries (Regime, Sector, CERI, five IBMI modules), 9 decision entries (Setup, Lifecycle, two Alerts, CERI changes, four Winner families), 23 readiness policies and 3 execution adapters. There are **14 named family resolver functions plus the bulk root composer (15 authority entry functions)**; repeated scoped calls are not extra resolver implementations. The configuration matrix lists all 16 required subsystem rows.

## 6. Core calculators

Fundamental freezes native v2 scoring plus CSV column aliases before mapping; family schema is now `core.fundamental-v2`. Technical freezes selected Pine/v4/v5 behavior and benchmark selection; inactive v5 and irrelevant Pine labels remain excluded. Combined owns its decision/weight policy and binds upstream immutable evidence. Ranking freezes the selected normalized profile, preserving profile-name collisions as distinct behavior. Changes to Defensive Quality cannot invalidate Momentum Swing. The native default worker executes all four after every current YAML source becomes invalid.

## 7. Contextual calculators

Regime and Sector retain native typed policy, thresholds, weights, freshness and fallback meaning. CERI retains confidence/freshness/provider priority/consumer coupling; downstream changes and alerts have their own decision families. IBMI freezes the selected histogram/liquidity/options_activity/short_pressure/volatility metric section and contributor behavior. Retry/network budgets remain operational. IBMI enum names are normalized before delivered lookup. Native PostgreSQL tests cover all constituent families; the fresh-process master adds actual CERI raw-estimate rebuild/capture/change children and IBMI Liquidity.

## 8. Decision calculators

Setup snapshots retain signal registry, family, confidence, actionability and freshness policies before normalization. Lifecycle evaluation and transitions retain their own state/phase/episode/observation-gap policy. Setup and CERI Alerts freeze selected rule values, cooldown and dedup semantics, with immutable rule/decision evidence. Current database rule mutation cannot rewrite a historical decision. Native master creates Setup/Winner, runs observation-gap maintenance, and produces an actual expiry AlertDecision under C1 after live sources become unusable C2.

## 9. Winner configuration families

Winner prediction, outcome, cohort and generation have distinct behavioral roots in `WINNER_ROOTS`. Cohort minimum-N changes do not change prediction semantics. Original prediction vectors and outcome contracts remain retained. Maturation uses original retained same-bar/proxy policy and versioned outcomes. Latest rescore creates new estimate/model lineage over the captured vector and does not mutate original prediction policy. Generation/slice bindings preserve original cohort policy; legacy generations may create honestly labeled CURRENT_RULES_LEGACY_GENERATION proof without backfilling their original authority.

## 10. Readiness-policy configuration

All **23 named native consumer policies** are inventoried and individually executed under C1; changing each policy version to C2 fails with READINESS_POLICY_CONFIGURATION_ANCHOR_MISMATCH while the retained decision DTO and policy snapshot remain unchanged. Policy meaning participates in its own effective family and the retained native consumer eligibility decision. No current policy reinterpretation of old readiness is certified.

## 11. Calculation Identity integration

Sources resolve before financial math into a typed `EffectiveConfigurationSnapshot`. Its family declares namespace, schema, resolver/version, precedence, value types and classifications. Its `EffectiveConfigurationIdentity` becomes the configuration dimension of Calculation Identity. Immutable evidence retains that exact snapshot. The durable compositional anchor addresses retained records and is separately bound to the owning job/pipeline/generation.

Semantic hash includes the family/schema and behavior-affecting typed values. Resolution hash additionally addresses retained provenance and operational values. Identical semantics with different source provenance keep the financial identity while retaining different resolution evidence. Source locations, descriptions and transport controls cannot invalidate unrelated financial policy. Semantic equality does not prove algorithm correctness, input completeness, software compatibility or a historical acquisition plan.

## 12. Immutable evidence integration

Certified evidence writers require a typed snapshot matching the exact Calculation Identity. CoreCalculationEvidence retains payload plus at-creation readiness/configuration metadata; decision ledgers/sealed lineage retain their own rules. Historical hydration uses the retained payload, not current resolvers. Compatibility projection comparison validates financial fields and source pins before canonicalizing physical row addresses to immutable evidence addresses. At-creation metadata is reattached from the ledger, never reevaluated under current rules.

## 13. Pipeline configuration anchors

Root enqueue resolves once and persists `effective_configuration_records` by resolution hash. Anchor `fingerprint` hashes sorted semantic identities; `anchor_id` is the integrity hash covering reference addresses and provenance. Tiny payload references carry only anchor_id/fingerprint. Same semantics/different provenance may change integrity/address while keeping the business fingerprint. Behavioral switch drift changes the fingerprint; worker/runtime operational drift does not. Immutable bindings identify one job, pipeline or Winner generation.

## 14. Durable job bindings

The production `default_job_handlers()` registry has **34 handlers**, with **28 protected business/delivery handlers** and six nonbusiness acquisition/runtime handlers. The worker validates its immutable binding, reference integrity, family/schema, required entries, material authority and supported code policy before the native handler. All 28 missing-binding attacks execute the real default registry, fail closed and leave zero financial evidence. Mixed acquisition/business CERI and IB handlers remain protected conservatively; external acquisition planning is not frozen by this certification.

## 15. Retry/resume/child/reclaim semantics

Queueing, unfinished-work retry, anchored public resume, child enqueue outside any live ContextVar, and stale-lease reclaim use persisted C1. Reclaim changes execution ownership token, not semantic authority. A parent reference mismatch or missing family never invokes a live resolver. Public resume retains market context and decision handoff; the handoff is frozen before provider scheduling can interrupt a completed core graph. A duplicate original job after successful resume correctly fails DECISION_MANIFEST_MISMATCH once the decision pointer has advanced; that rejection is not a configuration change.

## 16. Historical reads

A second fresh application process retrieves unchanged evidence/config records/Lifecycle/transitions/Alerts/Winner lineage while every current YAML file is invalid. The master then restores valid C2 changes across 15 financial namespaces, enqueues new work through the public route, and verifies C1 history unchanged while C2 receives new identities and completes native calculations. Frozen neutral DTOs remain readable across supported policy/code changes even when execution compatibility fails closed.

## 17. Legacy semantics

Old artifacts without original authority remain UNKNOWN/NOT_PROVIDED/legacy; neither current files nor a guessed hash can certify them. Existing schema-v1 Fundamental snapshots remain neutrally readable but cannot execute the new alias-dependent family. Pre-0080 queued work without a retained binding fails closed. Populated downgrade/re-upgrade preserves preexisting business data; dropping authority tables cannot recreate missing historical configuration. No legacy backfill or production rewrite was performed.

## 18. Current-rules operations

The native master executes a parentless C2 DRY_RUN_REPLAY over real retained snapshots and verifies no historical ledger mutation. Explicit current-rule repair/replay/rescore operations freeze their current policy for the new operation and retain honest CURRENT_STATE_REPAIR/CURRENT_RULES_RETROSPECTIVE or new estimate/model lineage. They do not rewrite original evidence or claim ORIGINAL_CONTEXT. The audit’s six native parser call sites are new-root source reads, not six repair APIs. Direct unanchored legacy APIs are explicit current-rule entry points outside durable graph certification. **Original-context replay/reconstruction claim: NONE.**

## 19. Secret handling

The sentinel campaign covers a real PostgreSQL login password in DATABASE_URL, actual EODHD/IB credentials in fresh public/worker processes, and all retained records, anchors, evidence, repr, serialized output and captured stdout/stderr. Sentinel must never appear in authority material. Six canonical contract cases cover DSN/provider/IB/webhook/SMTP/session secret keys. There is no native SMTP/webhook/session-secret connector in this repository, so those cases prove canonical exclusion rather than an invented deployed connector. Transport credentials do not change AlertDecision semantics.

## 20. Migration 0080

Only migration **0080_effective_configuration** (parent **0079_setup_lifecycle_alert_ev**) is required. Fresh upgrade, populated 0079 upgrade, populated downgrade/re-upgrade, Alembic check/ORM parity, constraint validation and immutable UPDATE/DELETE attacks are exercised on verified disposable PostgreSQL 16. No schema changes beyond 0080, production migration or historical backfill. Supported retention cannot delete a referenced authority record: all three tables reject UPDATE/DELETE; bindings use RESTRICT owner/anchor foreign keys.

`effective_configuration_records`: resolution_hash VARCHAR(64) PK, namespace TEXT, semantic_hash VARCHAR(64), payload_json JSONB, created_at TIMESTAMPTZ. `execution_configuration_anchors`: anchor_id VARCHAR(64) PK, fingerprint VARCHAR(64), payload_json JSONB. `execution_configuration_bindings`: binding_key VARCHAR(80) PK, anchor_id FK RESTRICT, unique optional job_id/pipeline_run_id/winner_cohort_generation_id FKs RESTRICT, exactly-one-scope CHECK. Each table has a BEFORE UPDATE OR DELETE immutable trigger backed by reject_effective_configuration_mutation; ORM mutation protection complements PostgreSQL triggers. Records embedded in anchor JSON are validated on every load; there is no invented relational FK from JSON to records.

## 21. Certified boundaries

Certification covers supported newly produced artifacts, the normal durable public upload/pipeline/resume path, registered worker delivery guards, native downstream handlers and addressed immutable history. External IB/provider readiness and acquisition scheduling are explicit test boundaries over cached native raw prices/estimates. No financial calculator, default registry, identity, evidence writer, claim/heartbeat/domain fence or durable loader is replaced. The public upload uses the application’s normal SessionLocal/get_db with no dependency override. Root/retry/resume/history processes have distinct PIDs.

## 22. Residual risks

Opaque native CERI/provider-selection and Winner compatibility hashes remain conservative independent guards. Configuration authority does not repair provider precedence algorithms or stale-penalty mathematics. Source/temporal manifests and acquisition completeness remain separate. Embedded snapshots add storage; the measured root bundle is roughly 1.38 MB before database encoding. No production throughput/SLA, crash durability, arbitrary injected Python safety or privileged-SQL tamper governance is claimed. Secrets must continue to be excluded when new settings/connectors are introduced.

## 23. Deferred later phases

Original-context replay/reconstruction; acquisition-plan semantics; refresh-cycle identity; target-scope freezing; entry-point unification; privileged direct-SQL governance; long-term configuration retention/storage optimization. No Phase-5 implementation is included.
