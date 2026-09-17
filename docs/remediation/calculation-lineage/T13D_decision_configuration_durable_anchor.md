# T13D — Decision configuration authority and durable anchors

## 1. Executive verdict

**PASS**. Own decision authority and durable delivery are implemented. The additive 0080 migration stores content-addressed snapshots, compositional anchors and immutable job/pipeline/generation bindings. No production rewrite or legacy backfill. Final source, gate counts, exact parity and benchmark evidence are retained in the companion certification JSON.

## 2. Baselines

| Baseline | Commit |
| --- | --- |
| Original audit | 3a9d47063be996908b7d5d1cc5769e0bbd033546 |
| Phase 2 | 7b015124807b8f895911d7e790fcbf01949ff85f |
| Phase 3 | 5422bcdf7703db891810d9e9c20a8f1770241fc9 |
| T13A | 7fa173c1e34752d457043fce5b4c5f22ec6e25d5 |
| T13B | ea8d48a510acbee9b88886390a9f775a36a4510a |
| T13C / T13D starting HEAD | 6884b0d6a9f5179a0ccdd97b8215f0e542f0032d |

Branch `codex/t13d-decision-config-durable-anchor`. One focused PASS commit is authorized. The final implementation commit contains this report; its hash is reported in the task reply. No merge or push.

## 3. Scope

Setup normalization/canonicalization/change detection, lifecycle/state repair/replay, Setup and CERI downstream alerts/changes, Winner prediction/outcome/cohort/generation, 23 named readiness policies, and durable enqueue/worker/retry/resume/child/reclaim boundaries. Source/temporal/readiness/write fences retain their previous authority. Acquisition plans, refresh-cycle/target scope identity, arbitrary direct APIs, privileged SQL and complete original-context reconstruction remain outside this certification.

## 4. Decision/config dependency graph

Root -> safe typed family snapshots -> content-addressed records -> compositional anchor -> immutable pipeline/job binding -> bulk delivery -> native adapters -> own decision identity/evidence. Exact upstream artifact pins retain upstream producer rules; own downstream semantic hash contains its own policy. Prediction -> retained original outcome/reference policy -> maturation. Generation -> immutable cohort/generation anchor -> statistics/estimates. No CERI-to-Ranking/Setup/Winner, IBMI-to-Winner or Sector-to-same-run-Ranking decision edge is added.

| Subsystem | Configuration family | Resolver | Behavioral keys | Operational exclusions | Config identity | Evidence binding | Historical read | Drift behavior |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Setup | decision.setup | resolve_setup_configuration | engine; canonicalization; family/phase; confidence/quality; registry; actionability; reconstructed origin; grace=3 | API/replay/retention; adapter-only fields | Own semantic hash + schema | Setup CoreCalculationEvidence + typed Calculation Identity | Retained tagged snapshot | C2 creates distinct proof; preconstructed mismatch fails |
| Lifecycle | decision.lifecycle | resolve_lifecycle_configuration | engine/states/phases/families/episodes/confidence/actionability/quality/registry/reconstructed; lifecycle-v1 | API/transport/retention; adapter-only fields | Own semantic hash + schema | Evaluation/transition evidence; prior exact pins | Retained tagged snapshot | Prior C1 retained; C2 new evaluation/repair |
| Alerts | decision.alerts.setup; decision.alerts.ceri; decision.ceri.changes | resolve_alert_configuration; resolve_ceri_decision_configuration | Selected logical DB/profile rules; filters/severity/minimum confidence/cooldown; dedup; enabled/change thresholds | Delivery credentials/ack/dismiss/transport; physical row addresses | Own semantic hash + schema | Setup alert decision/rule evidence; sealed CERI evidence/delta | Retained original rules and predecessors | C1 matching/cooldown retained; C2 new authority |
| Winner capture/prediction | decision.winner.prediction | resolve_winner_configuration(prediction) | engine/feature schema/filters/episode/entry models | Cohort/governance/retention/API; opaque native whole-file hash | Own semantic hash + schema | Typed prediction Calculation Identity + sealed lineage | Prediction creation snapshot | Cohort-only drift cannot change prediction config identity |
| Winner outcome/maturation | decision.winner.outcome | resolve_winner_configuration(outcome); winner_outcome_reference_configuration | entry models/horizons/outcome definitions/pending; selected benchmark/sector proxy/reference version | Cohort/model governance/transport; adapter-only fields | Own scoped semantic hash + schema | Prediction retains original outcome policy; definition metadata seal | Original prediction policy without current Sector lookup | Original C1 rules; unavailable code policy fails |
| Winner cohort | decision.winner.cohort | resolve_winner_configuration(cohort) | engine calculation version/schema/cohort hierarchy/priors/min n/intervals/grades/cold start/filters/definitions; cohort-v2.2 | Prediction episode rules/API/transport | Own semantic hash + schema | Cohort statistics and estimate metadata seal + existing manifest/model pins | Stored original own snapshot | New rescore has own current rules; prediction unchanged |
| Winner generation | decision.winner.generation | resolve_winner_configuration(generation) | Cohort roots + model governance/drift/evidence membership | Transport/slice timing; adapter-only fields | Own semantic hash + schema | Immutable winner-generation binding + exact native contract | Persisted generation anchor | C1 slices/retries; mismatching C2 rejected; legacy not promoted |
| Readiness policies | decision.readiness.<producer>.<consumer>[.<module>] | resolve_readiness_policy_configuration | 23 existing immutable named policies: versions/producer/consumer/module and permission contract | Scheduling/observability; producer thresholds remain upstream | Own semantic hash + schema | Bundle identity plus existing eligibility policy/version/fingerprint | Stored policy snapshot | Unsupported current-code C1 policy fails before permission/math |

## 5. Setup configuration inventory

`decision.setup` uses the actual native engine, canonicalization, family/phase/fallback, confidence/data-quality, registry, actionability, reconstructed-origin rules and code-owned grace of three sessions. Native registry is one STRUCTURE authority unit (profile definitions interpreted by the declared native parser, including nested defaults). The inventory enumerates every typed entry/source/classification; STRUCTURE counts are not scalar leaf counts.

## 6. Setup authority/adoption

`resolve_setup_configuration` freezes before SnapshotBuilder/canonicalizer/change-detector behavior. Native DTO carries the exact immutable snapshot into repository/evidence persistence. Existing typed Calculation Identity replaces its configuration dimension with this own semantic hash/schema. Retry writer recovers the retained snapshot, never today's files. Preconstructed C2 native services reject an active C1 bundle before math.

## 7. Lifecycle configuration inventory

`decision.lifecycle` retains native engine/states/phases/families/episodes/confidence/actionability/quality/registry/reconstructed rules plus declared lifecycle algorithm version. API/replay/retention and unrelated native adapter fields are operational, outside own semantic hashing.

## 8. Lifecycle authority/adoption

Engine, episode and actionability services reconstruct owned adapters from the retained snapshot before math. Evaluation identity names its own lifecycle namespace and exact Setup/prior evaluation/prior transition pins; transition evidence retains evaluation authority. Prior C1 remains immutable; C2 is a new evaluation. No universal inferred compatibility rule or original-context claim. Gap repair creates a new own identity rather than reusing the predecessor's fingerprint.

## 9. Alert configuration inventory

Setup own alert family includes actual selected logical rule values (enabled/severity/scope/family/cooldown/minimum confidence/version/conditions/market restrictions), profile alert/exclusion policy and code dedup/cooldown contracts. Physical row IDs are retained only as operational addresses. CERI changes and alerts have separate own namespaces; selected CERI DB rules are frozen before matching. Rule lists are sorted by logical ID.

## 10. Alert rule/cooldown/dedup authority

Matching uses the cached selected C1 values, including supplied rule batches. Current DB rows supply addresses only during delivery. Builtin seeding inserts missing addresses with ON CONFLICT DO NOTHING and cannot overwrite current C2 financial rule values. Selected logical rule_sources retain actual PROFILE/DATABASE winners, separate from operational physical row IDs. Logical dedup remains native; historical cooldown/dedup queries retain their session upper bounds and exact predecessor evidence. CERI cooldown uses retained logical rule name with legacy physical-ID fallback, preventing row replacement from erasing history. Acknowledgement/dismissal remain operational; rule/config/cooldown/dedup proof members cannot be revised. The generic configuration seal also rejects UNKNOWN-to-known backfill.

## 11. Winner configuration inventory

Four Winner families enumerate actual prediction feature/entry/episode, original outcome horizon/target/stop/reference, cohort probability/grade/filter and generation governance/drift/membership rules. Shared native whole-file hash is retained as operational compatibility metadata, not substituted for own semantic identity. Family-specific inventory and matrix are above.

## 12. Winner configuration granularity

Cohort-only edits leave prediction configuration identity unchanged; prediction feature/episode edits change it. Outcome definition edits change outcome authority independently. Generation includes cohort and its own governance/drift/membership policy. Model version/source artifact identity and evidence membership remain separate native pins. The own prediction semantic vector excludes only the native whole-file diagnostic config_hash; the persisted raw feature-vector hash stays byte-for-byte compatible with baseline.

## 13. Winner prediction configuration

Capture resolves all four Winner families once per run before feature behavior. Owned native adapters cache these immutable snapshots; external direct extractor callers freeze before feature math. Root delivery selects C1; an owned C2 adapter under C1 fails. New prediction lineage retains its own prediction snapshot and typed identity, the original outcome snapshot and selected reference contract. Recapture checks the retained original outcome contract before it can attach new outcome rows; conflicting current rules fail explicitly.

## 14. Winner outcome/maturation configuration

Pending/maturation uses the original prediction's retained outcome policy, not the queued job's newly resolved current outcome rules. Benchmark SPY/selected sector proxy/version are frozen at prediction creation and part of the scoped outcome semantic identity. Missing known reference or unsupported code policy fails. Definition reuse verifies actual entry/horizon/target/stop/same-bar/primary values. Legacy auxiliary reference use is marked LEGACY_UNKNOWN_CURRENT_RULES_REFERENCE, never original reconstruction. Native maturation validates forward entry/horizon and target/stop against the retained contract, takes same-bar policy and benchmark/sector references from that original snapshot, and ignores mutable definition policy. Native price revision/availability fences remain unchanged.

## 15. Winner cohort configuration

Probability entrypoints resolve own cohort authority before math; all estimates/statistics, including insufficient results, retain own snapshot. New current-rules rescores retain current own cohort policy plus existing model/source/version/manifest pins. Original prediction remains immutable. A duplicate generation estimate with incompatible own proof fails explicitly.

## 16. Winner generation configuration

New generation creation is marked by the native insert result and receives immutable cohort/generation binding before materialization. Existing known binding is validated and loaded before slice math. Existing missing binding cannot be retroactively certified from today's rules. Standalone legacy processing keeps the native opaque hash guard and explicitly marks NEW outputs CURRENT_RULES_LEGACY_GENERATION; an anchored certified retry with missing generation authority fails.

## 17. Readiness policy authority

All 23 preexisting immutable named Technical/contextual/Winner consumer policies have own family/schema/semantic identity. Root freezes them once; native evaluate validates the current executable policy against C1 before permission/math. Version/material-field drift fails. Producer thresholds remain their producer's configuration, and no READY/DEGRADED/blocked/legacy permission is widened.

## 18. Pipeline configuration anchor

Three additive tables: effective_configuration_records keyed by resolution hash; execution_configuration_anchors keyed by complete integrity hash with business semantic fingerprint; execution_configuration_bindings with exact unique job/pipeline/Winner-generation FK scope. One-scope CHECK and RESTRICT FKs preserve references. Anchor fingerprint composes semantic identities only; integrity also covers provenance and record addresses. ORM and PostgreSQL triggers prohibit update/delete. Existing native artifact JSON configuration members are sealed at ORM/service boundaries; privileged SQL governance remains deferred.

## 19. Durable job delivery

A genuinely new root resolves authorized current values once before enqueue, bulk inserts content-addressed records and saves a tiny {anchor_id,fingerprint} payload reference. Worker validates immutable job/pipeline/parent binding before bulk loading and native math, then scopes delivery alongside the existing lease/domain-write fence. Repeated native loader/resolver calls reopen no current files and issue no config DB reads. Root classes load only needed Setup/Winner/CERI/IB family closures plus common core/context/readiness dependencies.

| Stage / job type | Business calculation? | Configuration family/families | Anchor point | Payload/reference | Retry behavior | Resume behavior | Current-config reread? | Certification |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Fundamental | Yes | core.fundamental | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Technical | Yes | core.technical | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Combined | Yes | core.combined | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Ranking | Yes | core.ranking:<profile> | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Regime | Yes | contextual.regime | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Sector | Yes | contextual.sector | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| CERI | Yes | contextual.ceri + downstream changes/alerts | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| IBMI job-driven metrics | Yes (acquisition separate) | Five contextual.ibmi metric families | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Setup | Yes | decision.setup | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Lifecycle | Yes | decision.lifecycle | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Alerts | Yes | decision.alerts.setup/ceri | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Winner capture | Yes | decision.winner.prediction/outcome/cohort/generation | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Winner maturation | Yes | Original prediction outcome policy + root delivery | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Winner cohort/generation | Yes | decision.winner.cohort/generation | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| FULL_PIPELINE | Yes | 51 default family/scope references; active selected rules and 16 switches | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |
| Continuation/child jobs | As parent | Inherited parent bundle; no child root resolution | New root enqueue; family before math | effective_configuration_anchor {anchor_id,fingerprint} | Same immutable C1 | Persisted pipeline/generation C1 | None for certified behavior; OP/transport separate | Native resolver/PG attacks + parity; T13E integrated deployment pending |

Complete native handler registry (not additional end-to-end certification):

| Registered native job type | Delivery authority |
| --- | --- |
| CERI_ALERT_REBUILD | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_BACKFILL | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_CAPTURE_RUN | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_CHANGE_DETECTION | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_FEATURE_BATCH | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_NORMALIZE | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_NORMALIZE_BATCH | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_PROVIDER_INGEST | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_PROVIDER_INGEST_BATCH | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_PURGE_LICENSED_DATA | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_REBUILD_FEATURES | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| CERI_RUN_FINALIZE | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| FULL_PIPELINE | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| IB_FETCH | Acquisition/transport/repair probe; outside business configuration anchoring |
| IB_FLEX_IMPORT | Acquisition/transport/repair probe; outside business configuration anchoring |
| IB_HISTOGRAM_FETCH | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| IB_INTELLIGENCE_HISTORICAL_REFRESH | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| IB_INTELLIGENCE_LIVE_SNAPSHOT | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| IB_INTELLIGENCE_REBUILD_FEATURES | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| IB_SCANNER_RUN | Acquisition/transport/repair probe; outside business configuration anchoring |
| MARKET_DATA_PREWARM | Acquisition/transport/repair probe; outside business configuration anchoring |
| SEC_READINESS_REPAIR | Acquisition/transport/repair probe; outside business configuration anchoring |
| SETUP_ALERT_REBUILD | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| SETUP_LIFECYCLE_DAILY_MAINTENANCE | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| SETUP_LIFECYCLE_EVALUATE_RUN | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| SETUP_LIFECYCLE_REPAIR_TICKER | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| SETUP_LIFECYCLE_REPLAY | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| WINNER_COHORT_REFRESH | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| WINNER_HISTORICAL_BACKFILL | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| WINNER_LATEST_RESCORE | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| WINNER_OUTCOME_MATURATION | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| WINNER_OUTCOME_REVISION_CHECK | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| WINNER_PREDICTION_CAPTURE | Immutable root/parent/pipeline C1 binding; missing/mismatch fails; no certified behavioral current reread |
| WORKER_RECOVERY_PROBE | Acquisition/transport/repair probe; outside business configuration anchoring |

## 20. Retry semantics

Retry reloads C1 from the immutable binding, independently of changed lease token, current Settings/profile files or rule rows. Missing record/family, tampered payload, another legitimate C2 anchor substitution, incompatible namespace/scope, or unsupported executable policy fails before handler math. No fallback to current/default configuration.

## 21. Resume semantics

Public resume_pipeline and SEC-repair continuation enqueue through the same native enqueue authority. The persisted pipeline binding supplies C1 while native temporal context/checkpoint/source evidence guards remain intact. A legacy pipeline without binding cannot be silently resumed as certified using current config. No original-context replay claim is added.

## 22. Child/continuation inheritance

Child jobs inherit parent immutable binding even when enqueue occurs outside a live delivery scope. Active scope and pipeline/parent references must agree; supplied different C2 is rejected. Batch CERI normalize/provider/feature/finalize and Winner continuation jobs retain the same root. If a child requests a family absent from its inherited closure, it fails rather than reparsing current rules.

## 23. Stale-lease recovery

Real stale lease claim/recovery/reclaim changes execution ownership token and preserves the same content-addressed C1 binding. Existing domain write fences still prevent former lease owner writes. Token identity is never financial configuration authority.

## 24. Current-rules operations

Ticker/current-state repairs are authorized new calculations under frozen current rules and marked CURRENT_STATE_REPAIR; lifecycle REPLAY evaluations are CURRENT_RULES_RETROSPECTIVE. Exact prior evidence stays unchanged. Legacy base identity without typed schema keeps LEGACY_UNKNOWN source/temporal/context dimensions; malformed claimed typed identities fail. Backfill/rebuild is not proof of original execution. Direct unanchored legacy handlers remain explicit current-rules entrypoints; durable business workers reject unanchored jobs.

## 25. Historical config reads

History reads original retained tagged configuration, validates its semantic/resolution identity and performs neutral native decoding without current files/defaults. Unsupported old code policy can be inspected historically even when execution rejects it. Full retained Setup/Lifecycle/Alert/Winner records survive new-world reads; ack/dismiss do not change proof.

## 26. Legacy behavior

Missing original configuration means UNKNOWN/LEGACY_UNKNOWN. No migration rewrites or backfills old artifacts. Existing opaque generation/config hashes retain their native guard but do not prove full original authority. New current-rules legacy outputs carry their own new policy and explicit semantics; legacy original evidence remains unknown.

## 27. Configuration drift tests

Tests cover C1-to-C2 Setup/Lifecycle/Alert/Winner/CERI/readiness/feature-flag drift; roundtrip without live reads; actual source/default trace; provenance-only and operational/secret changes; queued/retry/public resume/child/reclaim; preconstructed C2 service rejection; valid alternate anchor substitution; SQL immutability; code-policy drift; native decision/artifact persistence, repair and retrospective semantics. Section 34 maps all 33 required families.

## 28. Feature-flag classification

Sixteen execution switches are frozen: technical v5/shadow/persistence; Setup step/handoff; Winner master/capture/generation-v2 selection; CERI master/capture/alerts/provider ingest/backfill; three IBMI modules. They alter durable calculation availability/output. Scheduling-only worker fairness/polling/automatic queue creation/slice limits/CERI batching remain outside semantic identity. Observability/shadow query comparison is separate. Native CERI explicit alerts_enabled request is resolved at root enqueue.

## 29. Secret safety

Only whitelisted business Settings switches are persisted. Runtime credentials/transport Settings remain RAM/live operational authority. Shared T13A secret classification/redaction excludes raw secrets and secret-derived digests from semantic/resolution payloads and anchors. Synthetic credential tests plus operational/behavioral root fingerprint tests demonstrate exclusions; no real credentials are printed or sent.

## 30. Static authority audit

The deterministic companion inventory is generated by scripts/qa/t13d_configuration_inventory.py. It separately counts typed configuration entries (STRUCTURE aggregate units), bounded native config/rule attribute reads, Settings keys, loader/getter/profile-parse boundaries and reviewed exclusions. All material sources in this scope are known; unrecognized Settings/environment keys make the inventory fail. This is a bounded source census with manual native call-graph review, not a repository-wide dataflow or arbitrary injected Python implementation proof. Final typed entries: {'BEHAVIORAL': 659, 'OPERATIONAL': 529}. Source-site counts: {'CURRENT_RULES_EXPLICIT': 6, 'DISPLAY_ONLY': 1, 'OBSERVABILITY_ONLY': 12, 'OPERATIONAL_ONLY': 135, 'OUT_OF_SCOPE': 9, 'SCHEDULING_ONLY': 32, 'T13D_FROZEN': 444}; TEST_ONLY=0, SECRET=0, UNKNOWN_REMAINING=0. These are separate units and do not claim arbitrary dataflow coverage.

## 31. Performance/query impact

Content-addressed root records are bulk inserted; the cold bundle loader uses one anchor SELECT plus one bulk configuration SELECT; immutable binding validation performs separate bounded boundary lookups. A real PostgreSQL test proves exactly two cold-load queries and zero database/file reads for 100 repeated native loader/resolver batches. Immutable shared snapshot hashes/identity are memoized safely; mutable external native DTOs are not memoized. Native Setup/Winner adapters reuse batch-owned snapshots. Computational benchmark records pipeline bundle resolution/serialization, 100 actual Setup builder calls and 100 actual Winner feature extractions; rule DB is empty fake preload, so this is not end-to-end pipeline/acquisition/cohort/storage latency. Full T13A artifact snapshots remain embedded; measured sizes are explicit, not claimed small. Durable job payload duplicates only tiny references.

Measured computational benchmark: bundle 2197.428 ms; 100 native Setup builds 2711.186 ms; 100 Winner feature extractions 159.507 ms. Serialized 51-family bundle totals 1,256,726 bytes. Individual decision family bytes: decision.alerts.setup=135384, decision.lifecycle=156742, decision.setup=155933, decision.winner.cohort=34901, decision.winner.generation=36907, decision.winner.outcome=28666, decision.winner.prediction=30879. This is a local measurement, without a production SLA/improvement claim.

## 32. PostgreSQL certification

Disposable PostgreSQL 16 only, task-owned swinglens-t13d-pg-20260916, loopback 26316. Safety verifier confirms GUID disposable DB before destructive Alembic operations; fixtures drop their own databases. Fresh full migration, 0079-to-0080 upgrade, downgrade/re-upgrade, command.check/model drift, one-scope constraints, four RESTRICT FKs, immutable SQL triggers, actual decision rows/history, substitution/missing/lease/resume boundaries are certified. Test-only fsync/synchronous_commit/full_page_writes were disabled for migration throughput; no physical power-loss durability claim. No production DB/service is touched.

## 33. READY/business parity

Identical AST/functionally identical baseline/current probe over 309 tests captures 537 native business outputs. Canonical JSON is exactly equal, 1,636,998 bytes; SHA-256 cf237a6dae492647fbd7732abfcbebc86d7edc446975bac80a0c9267b8f7ff2f. Baseline detached worktree is starting HEAD. Formatting-only probe update preserves identical AST. Both final probes run against identical AST and explicit 2026-09-16 18:00 UTC input for capture fixtures lacking an explicit cutoff. An earlier comparison crossed the US close; supplying the same input to both checkouts restored exact dates and hashes without omitting them. Final baseline/current runs pass against frozen final source. No unexpected financial/classification/score/probability/grade/cooldown/dedup delta under unchanged values. New configuration/identity/semantics metadata and intentional fail-closed boundary behavior are expected.

## 34. Tests

Final lane accounting: Broad excludes integration/e2e/external and established infrastructure-only load gates; final PostgreSQL lane covers all named Phase-0/1/2/3 and T13A/B/C native cases plus T13D. Overlapping unit cases are not summed as independent coverage. Development failures were deterministic fixture expectation/type defects (new migration head, incomplete DTO), import-order/legacy-base defects corrected and regression tested, and an invalid existing alert decision enum fixed in the test. No automatic flaky retry policy or production safety assertion is weakened.

| Required family | Concrete proof |
| --- | --- |
| 1. SETUP CONFIG DRIFT | Unit setup_drift_preserves_original; actual_setup_builder; PG native_setup_lifecycle_winner |
| 2. SETUP READY PARITY | Exact 537-output native baseline/current parity; Setup builder regression suite |
| 3. LIFECYCLE CONFIG DRIFT | Unit lifecycle_drift_preserves_prior_configuration; actual_lifecycle_engine |
| 4. PRIOR LIFECYCLE CONFIG | PG native_setup_lifecycle_winner repair/retrospective and predecessor immutability |
| 5. ALERT RULE DRIFT | Unit actual_alert_rule_matching; PG alert_configuration_history; c1_builtin_rule_seed |
| 6. ALERT TRANSPORT EXCLUSION | Unit secret_transport_exclusion; PG secret_and_operational_settings; pipeline_fingerprint |
| 7. WINNER PREDICTION CONFIG DRIFT | Unit prediction_model_drift; prediction_semantic_vector; native PG prediction persistence |
| 8. WINNER COHORT CONFIG DRIFT | Unit cohort_only_drift; cohort/estimator and generation regression suites |
| 9. WINNER OUTCOME CONFIG | Unit outcome_contract_drift; frozen_maturation_proxy; native_maturation_same_bar_policy |
| 10. READINESS POLICY DRIFT | Unit named_readiness_policy; PG readiness_policy_code_drift |
| 11. QUEUED JOB CONFIG DRIFT | PG queued_job_retry_and_resume_keep_c1_after_current_drift |
| 12. RETRY CONFIG DRIFT | PG queued_job_retry_and_resume; real native job/domain-write-fence regressions |
| 13. RESUME CONFIG DRIFT | PG public_resume_pipeline_preserves_c1_and_temporal_context |
| 14. CHILD JOB INHERITANCE | PG child_inherits_parent_even_without_live_execution_context |
| 15. STALE LEASE REQUEUE | PG stale_reclaim_changes_lease_but_preserves_configuration |
| 16. CURRENT-RULES REPAIR | PG native_setup_lifecycle_winner current-state gap repair; legacy unit repair |
| 17. CURRENT-RULES RETROSPECTIVE | PG native_setup_lifecycle_winner public retrospective replay |
| 18. HISTORICAL CONFIG READS | Parameterized retained_configuration_round_trip; native PG history file-read tripwire |
| 19. LEGACY | Unit repair_preserves_unknown_legacy; PG missing_anchor_and_legacy_job; legacy regression suites |
| 20. SAME VALUES DIFFERENT PROVENANCE | Unit same_values_different_native_provenance_keep_semantics |
| 21. OPERATIONAL FLAG CHANGE | Unit real_setup_operational_change; PG pipeline_fingerprint_tracks_only_business_switches |
| 22. BEHAVIORAL FLAG CHANGE | Unit secret_transport_exclusion_behavioral_flag; PG pipeline_fingerprint |
| 23. SECRET EXCLUSION | Shared T13A secret/redaction tests; PG secret_and_operational_settings_are_absent |
| 24. PIPELINE CONFIG FINGERPRINT | PG pipeline_fingerprint; anchor integrity and semantic fingerprint tests |
| 25. ANCHOR TAMPER | Unit snapshot_tamper; PG worker_rejects_valid_other_anchor; unknown_decision_authority; SQL seals |
| 26. MISSING ANCHOR | PG missing_anchor_and_legacy_job; stage_missing_from_bundle_never_resolves_current |
| 27. PHASE-3 READINESS REGRESSION | Prior Phase-3 563-node membership checked in current broad/native PG lanes |
| 28. T13C REGRESSION | T13C 30 unit cases plus 4 native PostgreSQL cases |
| 29. T13B REGRESSION | T13B 24 unit cases plus 5 native PostgreSQL cases |
| 30. T13A REGRESSION | T13A 41 unit cases plus 2 native PostgreSQL cases |
| 31. PHASE-2 REGRESSION | Phase-2 immutable native evidence/model/chain certification and original assertion suites |
| 32. PHASE-1 REGRESSION | Prior Phase-1 135-node membership checked in current broad/native PG lanes |
| 33. PHASE-0 REGRESSION | Prior Phase-0 86 + supplement 71 node memberships checked in current lanes |

| Lane / subset | Passed | Warning events / accounting |
| --- | ---: | --- |
| broad | 3263 | 23 warning events; 7 established deselections |
| postgres | 263 | 218 warning events; 0 established deselections |
| T13D focused | 50 | overlapping containing-lane subset |
| Setup file family (includes lifecycle/alerts) | 265 | overlapping containing-lane subset |
| Lifecycle/repair/actionability file families | 29 | overlapping containing-lane subset |
| Alert file families | 54 | overlapping containing-lane subset |
| Winner file family | 534 | overlapping containing-lane subset |
| T13A units | 41 | overlapping containing-lane subset |
| T13B units | 24 | overlapping containing-lane subset |
| T13C units | 30 | overlapping containing-lane subset |
| T13A native PG | 2 | overlapping containing-lane subset |
| T13B native PG | 5 | overlapping containing-lane subset |
| T13C native PG | 4 | overlapping containing-lane subset |
| Native PostgreSQL selected cases | 138 | overlapping containing-lane subset |
| phase3 | 563 | prior node membership; no missing nodes |
| phase1 | 135 | prior node membership; no missing nodes |
| phase0-certified-final | 86 | prior node membership; no missing nodes |
| phase0-supplement-certified-final | 71 | prior node membership; no missing nodes |
| phase2 | 60 | prior node membership; no missing nodes |
| Business parity | 309 per checkout | 537 exactly equal native outputs |
| Unique broad + native PostgreSQL | 3401 | 125 unit overlaps counted once |
| Static gates | PASS | Ruff/check/format, 65 compiled files, diff check, one Alembic head, scoped UNKNOWN=0 |
| Schema assertion guard | 157 | one existing HTTPX warning; additional scoped guard |

Broader run disposition: the full selected run had 3,262 passed and one deterministic readiness-fixture mismatch. The fake target-stop definition was aligned with the original contract already retained by its captured prediction. All seven tests in that readiness file then passed; every original financial/readiness assertion remains intact. The certified broad union contains 3,263 unique cases and seven established deselections. It is not described as a single 3,263-pass invocation. The broad warning row includes the original 22 events plus one closure event.

PostgreSQL run disposition: the full selected run had 262 passed and one deterministic new test-fixture failure (custom ConfigurationEntry constructor). After fixing only that fixture, all 20 T13D PostgreSQL cases were rerun and passed against the final test file. The certified union contains all 263 unique cases, including 138 integration cases and 125 units; it is not described as a single 263-pass invocation. All other source remains identical to the final application manifest. Warning events in the combined PG row sum both runs, including overlap. No unresolved failed/skipped tests or collection errors. Seven existing external IBMI cases are deselected. HTTPX/Starlette, SQLite datetime-adapter, Alembic path-separator and existing mutually dependent lifecycle-FK schema-sort warnings are disclosed; no flaky retry policy. The earlier native recapture closure exposed an incorrect entry accessor and result-field fixture; both were corrected before the full selected run.

## 35. Finding reconciliation

| Finding | T13D status | Boundary |
| --- | --- | --- |
| SETUP-007 | CONFIGURATION PORTION CLOSED for new certified alerts | Exact original rules/own snapshot and upper-bounded predecessor evidence; legacy unknown |
| SETUP-010 | CONFIGURATION PORTION CLOSED | Own Setup/Lifecycle policy retained; reconstruction/other algorithm portions not claimed |
| WIN-003 | CONFIGURATION PORTION CLOSED | Four own families; original prediction/outcome policy separate from current rescore/model source |
| CERI-008 | DOWNSTREAM CONFIGURATION PORTION CLOSED | Own changes/alerts; exact selected rules; opaque native feature-selection coupling retained |
| PIPE-005 | CONFIGURATION PORTION CLOSED | Root enqueue and immutable pipeline/job binding |
| PIPE-006 | CONFIGURATION PORTION CLOSED | Durable retry/child/resume C1 delivery; temporal/source manifests still independent |
| PIPE-007 | CONFIGURATION PORTION CLOSED | Stale reclaim changes token, not config; full recovery integration T13E |
| XINT-010 | PARTIAL; certified configuration authority/delivery enforced | T13E integration, acquisition and entrypoint unification remain |
| INV-CONFIG-001 | ENFORCED on supported certified calculations; PARTIAL repository-wide | Legacy UNKNOWN, direct current-rules APIs and privileged SQL not promoted to certification |

Prior reports remain immutable historical baseline records; the lineage registry receives a dated scoped reconciliation rather than rewriting its original audit findings.

## 36. T13E handoff

| Producer/calculator | Config family | Config identity | Resolution point | Evidence binding | Pipeline anchor | Job propagation | Historical read | Retry semantics | Certification |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Fundamental | core.fundamental | T13A semantic hash/schema | Native ranker resolve before score | FundamentalScore/CoreCalculationEvidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Technical | core.technical | T13A semantic hash/schema | Native feature/scoring context | TechnicalScore/CoreCalculationEvidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Combined | core.combined | T13A semantic hash/schema | Combined decision context | CombinedResult/CoreCalculationEvidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Ranking | core.ranking:<profile> | T13A semantic hash/schema | Selected profile before ranking | RankingResult/CoreCalculationEvidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Regime | contextual.regime | T13A semantic hash/schema | Native Regime snapshot build | MarketRegimeSnapshot/CoreCalculationEvidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Sector | contextual.sector | T13A semantic hash/schema | Native Sector snapshot build | SectorRotationSnapshot/CoreCalculationEvidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| CERI | contextual.ceri + decision.ceri.changes/alerts.ceri | T13A semantic hash/schema | Native capture; downstream matching/change scope | CERI core evidence + sealed changes/alerts | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| IBMI | contextual.ibmi.<five metric modules> | T13A semantic hash/schema | Native metric build before decision contribution | IBMI constituent CoreCalculationEvidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Setup | decision.setup | T13A semantic hash/schema | SnapshotBuilder before normalization | Setup CoreCalculationEvidence + typed identity | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Lifecycle | decision.lifecycle | T13A semantic hash/schema | Engine/service before evaluation | Lifecycle evaluation/transition ledgers | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Alerts | decision.alerts.setup; decision.alerts.ceri | T13A semantic hash/schema | Rules cached before matching | Alert own rule/decision proof or sealed evidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Winner prediction | decision.winner.prediction | T13A semantic hash/schema | Run capture before feature math | Prediction sealed lineage + typed identity | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Winner outcome/maturation | decision.winner.outcome + reference_policy | T13A semantic hash/schema | Prediction retains original rules before outcomes | Original prediction policy; outcome definition seal | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Winner cohort/generation | decision.winner.cohort/generation | T13A semantic hash/schema | Capture/refresh before materialization; slice validates binding | Statistics/estimates seal; generation immutable binding | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |
| Readiness policies | 23 decision.readiness.* | T13A semantic hash/schema | Root resolves once; native evaluate validates current code | Own bundle snapshot + native eligibility evidence | Immutable pipeline binding -> compositional anchor | Tiny job reference; immutable job/parent binding | Retained tagged values; absent original remains UNKNOWN | Same C1; explicit missing/mismatch/unsupported-code failure | T13A-D focused/native PG + exact business probe; T13E graph integration pending |

All 15 required rows are present. This is T13E's graph checklist, not a T13E completion claim.

## 37. Residual risks

T13E must certify the complete deployed graph, cross-process queue/restart/continuation cohorts and retained-rule interactions under real project-scale data. Storage/retention planning must respect RESTRICT immutable audit records and embedded artifact snapshot sizes. Current provider/price acquisition can still change new source evidence under existing lineage guards; acquisition plans, refresh-cycle/target freezing and privileged SQL remain separate. Opaque native CERI/Winner compatibility hashes remain conservative guards. No original-context reconstruction, arbitrary dependency injection safety, physical DB crash certification or production latency improvement is claimed.

## 38. Final verdict

**PASS**. Final focused/native PostgreSQL, broader regressions, exact parity, source/static/schema and documentation checks pass. One focused commit is authorized. Production/runtime mutations are limited to local task files/branch, task-owned disposable PostgreSQL/test tuning and detached baseline worktree. No production schema/data/config rewrite, legacy backfill, unrelated container/worktree mutation, merge or push.


Changed-file manifest (69 task-owned files; initial worktree clean):

- `app/models/ceri_tables.py`
- `app/models/tables.py`
- `app/services/background_job_service.py`
- `app/services/background_worker.py`
- `app/services/ceri/alert_service.py`
- `app/services/ceri/change_detection_service.py`
- `app/services/ceri/config.py`
- `app/services/ceri/job_handlers.py`
- `app/services/combined_decision.py`
- `app/services/contextual_consumer_eligibility.py`
- `app/services/contextual_effective_configuration.py`
- `app/services/core_calculation_evidence.py`
- `app/services/core_effective_configuration.py`
- `app/services/effective_configuration.py`
- `app/services/fundamental_ranker_v2.py`
- `app/services/ib_market_intelligence/config.py`
- `app/services/ib_market_intelligence/orchestration.py`
- `app/services/market_regime_policy.py`
- `app/services/pipeline_executor.py`
- `app/services/ranking_profile_config.py`
- `app/services/sector_rotation_config.py`
- `app/services/setup_lifecycle/actionability_policy.py`
- `app/services/setup_lifecycle/alert_service.py`
- `app/services/setup_lifecycle/canonicalization.py`
- `app/services/setup_lifecycle/change_detector.py`
- `app/services/setup_lifecycle/config.py`
- `app/services/setup_lifecycle/decision_evidence.py`
- `app/services/setup_lifecycle/episode_service.py`
- `app/services/setup_lifecycle/evaluation_service.py`
- `app/services/setup_lifecycle/job_handlers.py`
- `app/services/setup_lifecycle/lifecycle_engine.py`
- `app/services/setup_lifecycle/replay_service.py`
- `app/services/setup_lifecycle/repository.py`
- `app/services/setup_lifecycle/snapshot_builder.py`
- `app/services/technical_consumer_eligibility.py`
- `app/services/technical_indicators.py`
- `app/services/technical_scoring_config.py`
- `app/services/technical_scoring_v5_config.py`
- `app/services/winner_probability/calculation_identity.py`
- `app/services/winner_probability/capture_service.py`
- `app/services/winner_probability/cohort_generation_service.py`
- `app/services/winner_probability/cohort_materialization_service.py`
- `app/services/winner_probability/config.py`
- `app/services/winner_probability/feature_extractor.py`
- `app/services/winner_probability/job_handlers.py`
- `app/services/winner_probability/outcome_service.py`
- `app/services/winner_probability/pending_outcome_service.py`
- `app/services/winner_probability/probability_estimator.py`
- `app/settings.py`
- `docs/architecture/SWINGLENS_CALCULATION_LINEAGE_REGISTRY.md`
- `docs/architecture/SWINGLENS_EFFECTIVE_CONFIGURATION_CONTRACT.md`
- `tests/integration/test_effective_configuration_postgresql.py`
- `tests/integration/test_phase2_immutable_evidence_certification.py`
- `tests/setup_lifecycle/test_change_detector.py`
- `tests/test_domain_write_fence.py`
- `tests/winner_probability/test_readiness_frozen_operations.py`
- `tests/winner_probability/test_t12e_readiness_certification.py`
- `alembic/versions/20260916_0080_effective_configuration_records.py`
- `app/services/configuration_artifact_immutability.py`
- `app/services/configuration_delivery.py`
- `app/services/decision_effective_configuration.py`
- `docs/remediation/calculation-lineage/T13D_decision_configuration_certification.json`
- `docs/remediation/calculation-lineage/T13D_decision_configuration_durable_anchor.md`
- `docs/remediation/calculation-lineage/T13D_decision_configuration_inventory.json`
- `scripts/qa/t13d_behavior_probe.py`
- `scripts/qa/t13d_configuration_benchmark.py`
- `scripts/qa/t13d_configuration_inventory.py`
- `tests/integration/test_decision_configuration_delivery_postgresql.py`
- `tests/test_decision_effective_configuration.py`

Task-owned disposable PostgreSQL container and detached baseline worktree were removed after certification. The baseline business output and lane logs remain in the ignored local QA directory. Both pre-existing user worktrees and monitoring containers were preserved.
