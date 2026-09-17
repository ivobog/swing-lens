# T14B — Core and contextual writer contract adoption

## 1. Executive verdict

PASS for all 32 assigned core/contextual and native supporting writer families and all 36 assigned initiator IDs: zero defects, zero unreconciled IDs. Writer authority is enforced at native semantic and retained evidence/projection boundaries. T14C/T14D caller and remaining writer adoption is not certified complete. The final 324-case Phase 0–4/attack lane and 3,448-case broader lane pass; exact receipts and resolved earlier failures are in `T14B_validation_summary.json`.

## 2. Baselines

Starting HEAD is `5eef0d08d783de132fecb14c5dedd66821f0949f` on `codex/t14b-core-contextual-writer-adoption`, with a clean incoming worktree. All five supplied baselines are ancestors. Python is 3.12.2; native PostgreSQL is 18.3 and Docker 29.6.2 is available. The sole migration head is `0080_effective_configuration`. See `T14B_starting_state.json` for exact commands and outputs.

Audited: `3a9d47063be996908b7d5d1cc5769e0bbd033546`; Phase-2: `7b015124807b8f895911d7e790fcbf01949ff85f`; Phase-3: `5422bcdf7703db891810d9e9c20a8f1770241fc9`; Phase-4: `d3157c8642c119a7e5d7a84c69f4a58c6d3866d8`; T14A: `5eef0d08d783de132fecb14c5dedd66821f0949f`.

## 3. T14A handoff reconciliation

The original handoff was checked before production edits. It assigns exactly 32 writer families and 36 initiator families to T14B; no family is renamed, split, dropped or silently transferred. `T14B_checked_handoff.json` retains every original record and the original handoff SHA-256. The machine certificate records each original owner, member site, assigned EF ID, alternative family, native authority and disposition.

| Family ID | Canonical owner | Current role | Contract / transaction | Assigned EF IDs |
| --- | --- | --- | --- | --- |
| WF_CERI_CONSENSUS_ATTACH | `app/services/ceri/surprise_feature_service.py:CeriSurpriseFeatureService.attach_consensus_snapshot` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | EF_538276c91abb138c |
| WF_CERI_FEATURE_BUILD | `app/services/ceri/feature_rebuild_service.py:CeriFeatureRebuildService._persist_company` | CANONICAL_WRITER | Enforced / persisted validation | Supporting mechanism |
| WF_CERI_GUIDANCE_ELIGIBILITY | `app/services/ceri/guidance_normalizer.py:apply_guidance_eligibility` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | EF_dcf27689d2681c17 |
| WF_CERI_PRICE_RESPONSE | `app/services/ceri/price_response_service.py:CeriPriceResponseService.persist` | CANONICAL_WRITER | Enforced / persisted validation | EF_20e06c4f9457355d |
| WF_CERI_RAW_SOURCE | `app/services/ceri/source_record_service.py:CeriSourceRecordService.store_source_record` | CANONICAL_WRITER | Enforced / persisted validation | EF_ad585eacf12c98d7 |
| WF_CERI_REVISION_FEATURE | `app/services/ceri/revision_feature_service.py:CeriRevisionFeatureService.persist_feature` | CANONICAL_WRITER | Enforced / persisted validation | EF_14ac8eafdc1a1ae0, EF_3aa9732708e9982e |
| WF_CERI_SCORE | `app/services/ceri/snapshot_service.py:CeriSnapshotService.persist_snapshot` | CANONICAL_WRITER | Enforced / persisted validation | EF_82522331e642b008 |
| WF_CERI_SOURCE_IDENTITY | `app/services/ceri/identity_resolver.py:CeriIdentityResolver.resolve_source_record` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | EF_213662a6d137bcac |
| WF_CERI_SOURCE_NORMALIZATION | `app/services/ceri/normalization_service.py:CeriNormalizationService._normalize_record` | CANONICAL_WRITER | Enforced / persisted validation | Supporting mechanism |
| WF_COMBINED_REFRESH | `app/services/combined_decision.py:refresh_combined_results` | CANONICAL_WRITER | Enforced / persisted validation | EF_a27b7ead7124b2da |
| WF_CORE_CURRENT_PROJECTION | `app/services/core_calculation_evidence.py:_advance_current_projection` | CURRENT_PROJECTION_WRITER | Enforced / persisted validation | EF_021cfb93d6813955, EF_3984d25edba4f45f, EF_756b7b07d4f365c0, EF_7fabc787dc6ffca2, EF_82522331e642b008, EF_874835d87c21855e, EF_8971757daefc9594, EF_a27b7ead7124b2da, EF_a3fd956e147403b1, EF_a8016e0b9d69e5be, EF_d526d0a0522f099b, EF_de562c9ceb6ee65a, EF_e90cea98144e2940 |
| WF_CORE_RETAINED_EVIDENCE | `app/services/core_calculation_evidence.py:persist_core_evidence` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | EF_021cfb93d6813955, EF_3984d25edba4f45f, EF_756b7b07d4f365c0, EF_7fabc787dc6ffca2, EF_82522331e642b008, EF_874835d87c21855e, EF_8971757daefc9594, EF_a27b7ead7124b2da, EF_a3fd956e147403b1, EF_a8016e0b9d69e5be, EF_d526d0a0522f099b, EF_de562c9ceb6ee65a, EF_e90cea98144e2940 |
| WF_FUNDAMENTAL_RECALCULATION | `app/services/fundamental_score_service.py:recalculate_run_fundamentals` | CANONICAL_WRITER | Enforced / persisted validation | EF_e90cea98144e2940 |
| WF_IBMI_HISTORICAL_SOURCE | `app/services/ib_market_intelligence/repository.py:persist_historical_metric_bar` | CANONICAL_WRITER | Enforced / persisted validation | EF_812da22fe41fbcba |
| WF_IBMI_LIVE_SOURCE | `app/services/ib_market_intelligence/repository.py:persist_live_snapshot` | CANONICAL_WRITER | Enforced / persisted validation | EF_be226eaac46d1ab2 |
| WF_IBMI_NATIVE_FEATURE | `app/services/ib_market_intelligence/repository.py:persist_feature` | CANONICAL_WRITER | Enforced / persisted validation | EF_8971757daefc9594 |
| WF_IB_CONTRACT_IDENTITY | `app/services/ib_contract_resolver.py:resolve_us_stock_contract` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | EF_919b08f9bc6d4b63 |
| WF_IB_FLEX_IMPORT | `app/services/ib_market_intelligence/flex.py:import_flex_report` | CANONICAL_WRITER | Enforced / persisted validation | EF_5f39587263ee923e |
| WF_IB_HISTOGRAM_SCANNER | `app/services/ib_market_intelligence/orchestration.py:execute_histogram_fetch` | CANONICAL_WRITER | Enforced / persisted validation | Supporting mechanism |
| WF_IB_SCANNER_SOURCE | `app/services/ib_market_intelligence/orchestration.py:execute_scanner_run` | CANONICAL_WRITER | Enforced / persisted validation | Supporting mechanism |
| WF_PRICE_INGESTION | `app/services/bar_cache_service.py:cache_bars` | CANONICAL_WRITER | Enforced / persisted validation | EF_3d469f86339ccac7 |
| WF_PRICE_SERIES_VERSION | `app/services/price_series_version_service.py:maintain_price_series_versions` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | EF_3d469f86339ccac7, EF_866ac2c173ac81d7 |
| WF_RANKING_LEGACY_REPLACEMENT | `app/services/ranking_profile_service.py:_replace_legacy_ranking` | LEGACY_WRITER | Enforced / persisted validation | EF_a3fd956e147403b1, EF_a8016e0b9d69e5be, EF_d526d0a0522f099b |
| WF_RANKING_PERSISTENCE | `app/services/ranking_profile_service.py:_persist_rankings` | CANONICAL_WRITER | Enforced / persisted validation | EF_a3fd956e147403b1, EF_a8016e0b9d69e5be, EF_d526d0a0522f099b |
| WF_REGIME_DERIVED_DELETE | `app/services/market_regime_repository.py:MarketRegimeRepository.delete_for_run` | CURRENT_PROJECTION_WRITER | Enforced / persisted validation | EF_fd8cd8fbbbba07f7 |
| WF_REGIME_SNAPSHOT | `app/services/market_regime_repository.py:MarketRegimeRepository.upsert_snapshot` | CANONICAL_WRITER | Enforced / persisted validation | EF_874835d87c21855e |
| WF_SECTOR_SNAPSHOT | `app/services/sector_rotation_repository.py:SectorRotationRepository.save_snapshot` | CANONICAL_WRITER | Enforced / persisted validation | EF_021cfb93d6813955 |
| WF_SEC_DOCUMENT_SOURCE | `app/services/ceri/sec/state_service.py:SecDocumentStateService.register_document` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | EF_44e2b88da09c0be6, EF_5acf98114588686d, EF_f0d208ce35a33ed6 |
| WF_SEC_INCREMENTAL_IDENTITY | `app/services/ceri/sec/incremental_ingestion.py:SecGuidanceIncrementalIngestionService._resolve_and_persist_cik` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | Supporting mechanism |
| WF_TECHNICAL_FEATURE_CACHE | `app/services/technical_artifact_cache.py:upsert_local_artifact` | DOMAIN_SUPPORTING_STATE | Enforced / persisted validation | EF_3984d25edba4f45f, EF_431a8947c0b31cbf, EF_7eea11a8cded4a8a, EF_cb55d161e779c8be, EF_de562c9ceb6ee65a, EF_fb5089df555b2b2f |
| WF_TECHNICAL_FINALIZATION | `app/services/technical_score_service.py:finalize_technical_scores` | CANONICAL_WRITER | Enforced / persisted validation | EF_3984d25edba4f45f, EF_756b7b07d4f365c0, EF_de562c9ceb6ee65a |
| WF_UPLOAD_INITIALIZATION | `app/services/upload_service.py:create_upload_run` | LEGACY_WRITER | Enforced / persisted validation | EF_ab4102ebb9197c4d, EF_da68f1fde41c070d |

The 32 detailed records in the machine certificate retain member persistence sites, tables, authority, modes, projection policy, parallel-family ownership, test paths and legacy/bypass disposition. Source adapter rejection and structural proofs are identified separately from native financial positive commits.

## 4. Mutation contract adoption model

Writers reuse the existing `DomainMutationContext`, `MutationSemanticMode`, `MutationAuthorityPolicy`, `MutationValidationResult` and Phase-0 job-row fence. The adapter constructs a declaration from explicit native arguments; construction grants no permission. Persistence resolves and validates actual retained configuration, calculation context, immutable sources and frozen permission under the same transaction. No alternate financial mutation model or new schema is introduced.

## 5. Fundamental writers

`recalculate_run_fundamentals` requires an explicit cutoff, owning pipeline and full `core.fundamental` snapshot for real database writes before deleting serving rows. Sealing verifies the exact stored raw row, run/ticker/digest, native identity, retained C1 bindings and execution attempt. The output math is unchanged.

## 6. Initial-upload Fundamental path

`create_upload_run` retains RAW BOOTSTRAP authority over the exact uploaded file path/digest, rechecks the digest before insertion, and uses the native execution fence when applicable. Its initial numeric Fundamental values are explicitly `LEGACY_UNCERTIFIED` / `LEGACY_SERVING_ONLY`; they create no Fundamental core ledger or readiness authority. Certified readers reject this role even if a pointer is forged.

## 7. Fundamental recalculation variants

The formerly confirmed unbound recalculation branch rejects real-Session calls lacking cutoff/pipeline/full configuration. The unchanged synchronous route and non-durable pipeline branch therefore fail at the canonical boundary before financial replacement. Safe delivery and user-facing handling remain T14D. Fake database calculators remain pure numerical fixtures, not authority proof.

## 8. Technical writers

`finalize_technical_scores` requires explicit cutoff/pipeline/full `core.technical` configuration. Native technical input envelopes retain bounded price manifests, temporal basis, benchmark/sector/cohort manifests and the configuration used for feature/scoring behavior. Sealing reprojects exact price addresses at the declared cutoff/session and checks PIT fingerprints; cache reuse does not substitute for these source checks.

## 9. Combined writers

`refresh_combined_results` requires full configuration and exact per-ticker Fundamental/Technical core evidence IDs. It selects those named sources, checks native source kind/address, identity/context/time, configuration, immutable payload integrity and frozen consumer permission. Mutated serving financial values cannot impersonate their immutable target. The earnings-risk behavior and score formulas are unchanged.

## 10. Ranking writers

`_persist_rankings` validates exact native Fundamental/Technical/Combined source authority, readiness/permission and the complete frozen ranking profile. Private value-copy and legacy replacement mechanisms require its semantic transaction. The profile name and complete profile configuration must match; prior-Sector context follows its own older-session/full-config rule, never same-run feedback.

## 11. Regime writers

`MarketRegimeRepository.upsert_snapshot` seals an explicit full policy and native contextual source envelope with exact market/participation/leadership PIT frames. Same-session reuse follows native temporal/calendar compatibility and pinned configuration. Legacy derived deletion requires an explicit maintenance flag, refuses retained Regime history even with a cleared serving link, and deletes only the checked locked IDs so concurrent rows cannot be swept into a broad run deletion.

## 12. Sector writers

`SectorRotationRepository.save_snapshot` requires an explicit identity, full `contextual.sector` snapshot and exact native input envelope. It validates named Regime/Ranking parents, each used financial contributor, raw row digest/ownership and ETF price manifests. Frozen native eligibility remains authoritative; no universal eight-domain source checklist is added. Older prior-Sector context must be strictly earlier and configuration-compatible.

## 13. CERI writers

`CeriSnapshotService.persist_snapshot` and the dedicated decision sealer write the compatibility snapshot and immutable envelope atomically. They require the full CERI rule snapshot, exact source manifest, company/ticker ownership, native time/view basis and actual source observations. Optional IBMI contributors retain their own native role, module, time/subject compatibility and frozen permission.

## 14. CERI human-review separation

Human review metadata and source override families retain their separate T14C/T14D semantics and ownership; they do not rewrite immutable score evidence. Alert/change production is a separate family and is not absorbed into CERI score persistence. Explicit latest-corrected source views remain different from AS_KNOWN PIT views.

## 15. IBMI writers

`persist_feature` requires native immutable constituents and an explicit cutoff to certify features. Historical/live/request/price/histogram sources are resolved by exact addresses, ticker, basis, session/cutoff and manifest fingerprint. Legacy feature output without constituents remains explicit serving-only and creates no core evidence. Flex source import is assigned T14B; authoritative `WF_IB_TRADE_EPISODES` remains T14D and its ownership is not redefined.

## 16. Current projections

`_advance_current_projection` validates an explicit immutable evidence target, kind/run/ticker/profile/configuration and scope. PostgreSQL advisory transaction locks serialize first creation when no pointer row exists; row locks protect existing pointers. Older evidence cannot replace a newer pointer. The four single-value core serving writers also reject an older replay that would leave serving state behind its newer pointer.

## 17. Supporting state

Native acquisition/normalization writers declare their actual request/DTO/row inputs using the existing domain policy. Persisted row arguments are compared with exact database addresses/state under locks; missing or staged mismatched source inputs reject. Private persistence helpers require the named native source/financial transaction. Source facts are not assigned invented financial identity/configuration/global dependencies.

## 18. Cache safety

The existing `LocalArtifactKey` is a distinct supporting contract: native feature configuration/version, source-series versions, PIT manifest, exact session and cutoff form the key. Upsert rejects a forged signature; reads and validation check key/schema/version, metadata and payload checksum. A shadow match requires matching nonempty fresh/cached fingerprints and no error; payload replacement invalidates old shadow proof. Native cache mutations retain the actual durable job-row fence through outer commit.

## 19. Parallel writer dispositions

Every assigned alternative remains named in the certificate. Upload and ranking legacy replacement are classified separately; derived Regime deletion is bounded legacy maintenance. CERI licensed purge/current-rule replay, SEC repair/CLI identity, Winner link/metadata/outcome/calibration/model families and fill exclusions retain their original T14C/T14D or distinct semantics. No supported history writer is treated as interchangeable merely because it touches the same table.

| Family | Parallel family | Current classification | Retained task ownership |
| --- | --- | --- | --- |
| WF_CERI_CONSENSUS_ATTACH | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_CONSENSUS_ATTACH | WF_CERI_SOURCE_NORMALIZATION | CANONICAL_WRITER | T14B |
| WF_CERI_FEATURE_BUILD | WF_CERI_COMPANY_BOOTSTRAP | DOMAIN_SUPPORTING_STATE | T14D |
| WF_CERI_FEATURE_BUILD | WF_CERI_CURRENT_RULE_REPLAY | CURRENT_RULES_WRITER | T14D |
| WF_CERI_FEATURE_BUILD | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_FEATURE_BUILD | WF_CERI_PRICE_RESPONSE | CANONICAL_WRITER | T14B |
| WF_CERI_FEATURE_BUILD | WF_CERI_REVISION_FEATURE | CANONICAL_WRITER | T14B |
| WF_CERI_FEATURE_BUILD | WF_CERI_SOURCE_NORMALIZATION | CANONICAL_WRITER | T14B |
| WF_CERI_FEATURE_BUILD | WF_PIPELINE_STAGE_STATE | DOMAIN_SUPPORTING_STATE | T14D |
| WF_CERI_FEATURE_BUILD | WF_SEC_CIK_CLI | CURRENT_RULES_WRITER | T14D |
| WF_CERI_FEATURE_BUILD | WF_SEC_IDENTITY_REPAIR | CURRENT_RULES_WRITER | T14D |
| WF_CERI_FEATURE_BUILD | WF_SEC_INCREMENTAL_IDENTITY | DOMAIN_SUPPORTING_STATE | T14B |
| WF_CERI_GUIDANCE_ELIGIBILITY | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_GUIDANCE_ELIGIBILITY | WF_CERI_SOURCE_NORMALIZATION | CANONICAL_WRITER | T14B |
| WF_CERI_PRICE_RESPONSE | WF_CERI_FEATURE_BUILD | CANONICAL_WRITER | T14B |
| WF_CERI_PRICE_RESPONSE | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_RAW_SOURCE | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_RAW_SOURCE | WF_CERI_SOURCE_IDENTITY | DOMAIN_SUPPORTING_STATE | T14B |
| WF_CERI_RAW_SOURCE | WF_CERI_SOURCE_NORMALIZATION | CANONICAL_WRITER | T14B |
| WF_CERI_REVISION_FEATURE | WF_CERI_CURRENT_RULE_REPLAY | CURRENT_RULES_WRITER | T14D |
| WF_CERI_REVISION_FEATURE | WF_CERI_FEATURE_BUILD | CANONICAL_WRITER | T14B |
| WF_CERI_REVISION_FEATURE | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_SCORE | WF_CERI_CURRENT_RULE_REPLAY | CURRENT_RULES_WRITER | T14D |
| WF_CERI_SCORE | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_SOURCE_IDENTITY | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_SOURCE_IDENTITY | WF_CERI_RAW_SOURCE | CANONICAL_WRITER | T14B |
| WF_CERI_SOURCE_IDENTITY | WF_CERI_SOURCE_NORMALIZATION | CANONICAL_WRITER | T14B |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_COMPANY_BOOTSTRAP | DOMAIN_SUPPORTING_STATE | T14D |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_CONSENSUS_ATTACH | DOMAIN_SUPPORTING_STATE | T14B |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_FEATURE_BUILD | CANONICAL_WRITER | T14B |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_GUIDANCE_ELIGIBILITY | DOMAIN_SUPPORTING_STATE | T14B |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_HUMAN_SOURCE_OVERRIDE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_LICENSED_PURGE | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_RAW_SOURCE | CANONICAL_WRITER | T14B |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_REVIEW_METADATA | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_CERI_SOURCE_NORMALIZATION | WF_CERI_SOURCE_IDENTITY | DOMAIN_SUPPORTING_STATE | T14B |
| WF_CERI_SOURCE_NORMALIZATION | WF_PIPELINE_STAGE_STATE | DOMAIN_SUPPORTING_STATE | T14D |
| WF_CERI_SOURCE_NORMALIZATION | WF_SEC_CIK_CLI | CURRENT_RULES_WRITER | T14D |
| WF_CERI_SOURCE_NORMALIZATION | WF_SEC_IDENTITY_REPAIR | CURRENT_RULES_WRITER | T14D |
| WF_CERI_SOURCE_NORMALIZATION | WF_SEC_INCREMENTAL_IDENTITY | DOMAIN_SUPPORTING_STATE | T14B |
| WF_COMBINED_REFRESH | WF_WINNER_CAPTURE_TRAINING_METADATA | DOMAIN_SUPPORTING_STATE | T14C |
| WF_COMBINED_REFRESH | WF_WINNER_FORWARD_MATURATION | CANONICAL_WRITER | T14C |
| WF_COMBINED_REFRESH | WF_WINNER_PREDICTION_CAPTURE | CANONICAL_WRITER | T14C |
| WF_COMBINED_REFRESH | WF_WINNER_TEMPORAL_VALIDITY | CANONICAL_WRITER | T14D |
| WF_COMBINED_REFRESH | WF_WINNER_TYPED_REPOSITORY | DOMAIN_SUPPORTING_STATE | T14C |
| WF_FUNDAMENTAL_RECALCULATION | WF_UPLOAD_INITIALIZATION | LEGACY_WRITER | T14B |
| WF_IB_FLEX_IMPORT | WF_IB_FILL_EXCLUSION | SUPPORTED_DISTINCT_WRITER | T14D |
| WF_RANKING_LEGACY_REPLACEMENT | WF_RANKING_PERSISTENCE | CANONICAL_WRITER | T14B |
| WF_RANKING_PERSISTENCE | WF_RANKING_LEGACY_REPLACEMENT | LEGACY_WRITER | T14B |
| WF_REGIME_DERIVED_DELETE | WF_REGIME_SNAPSHOT | CANONICAL_WRITER | T14B |
| WF_REGIME_SNAPSHOT | WF_REGIME_DERIVED_DELETE | CURRENT_PROJECTION_WRITER | T14B |
| WF_SEC_INCREMENTAL_IDENTITY | WF_CERI_COMPANY_BOOTSTRAP | DOMAIN_SUPPORTING_STATE | T14D |
| WF_SEC_INCREMENTAL_IDENTITY | WF_CERI_FEATURE_BUILD | CANONICAL_WRITER | T14B |
| WF_SEC_INCREMENTAL_IDENTITY | WF_CERI_SOURCE_NORMALIZATION | CANONICAL_WRITER | T14B |
| WF_SEC_INCREMENTAL_IDENTITY | WF_PIPELINE_STAGE_STATE | DOMAIN_SUPPORTING_STATE | T14D |
| WF_SEC_INCREMENTAL_IDENTITY | WF_SEC_CIK_CLI | CURRENT_RULES_WRITER | T14D |
| WF_SEC_INCREMENTAL_IDENTITY | WF_SEC_IDENTITY_REPAIR | CURRENT_RULES_WRITER | T14D |
| WF_UPLOAD_INITIALIZATION | WF_FUNDAMENTAL_RECALCULATION | CANONICAL_WRITER | T14B |
| WF_UPLOAD_INITIALIZATION | WF_PIPELINE_STAGE_STATE | DOMAIN_SUPPORTING_STATE | T14D |

## 20. Initiator adoption

All 36 original T14B EF IDs map to their exact assigned writer dependencies. Their source-qualified reconciliation is recorded without claiming adoption of the 84 T14C or 168 T14D initiators. The three existing supported-distinct configuration-delivery families retain their Phase-4 ownership.

| Initiator ID | Assigned writer families | Native boundary disposition |
| --- | --- | --- |
| EF_021cfb93d6813955 | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_SECTOR_SNAPSHOT | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_14ac8eafdc1a1ae0 | WF_CERI_REVISION_FEATURE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_20e06c4f9457355d | WF_CERI_PRICE_RESPONSE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_213662a6d137bcac | WF_CERI_SOURCE_IDENTITY | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_3984d25edba4f45f | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_TECHNICAL_FEATURE_CACHE, WF_TECHNICAL_FINALIZATION | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_3aa9732708e9982e | WF_CERI_REVISION_FEATURE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_3d469f86339ccac7 | WF_PRICE_INGESTION, WF_PRICE_SERIES_VERSION | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_431a8947c0b31cbf | WF_TECHNICAL_FEATURE_CACHE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_44e2b88da09c0be6 | WF_SEC_DOCUMENT_SOURCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_538276c91abb138c | WF_CERI_CONSENSUS_ATTACH | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_5acf98114588686d | WF_SEC_DOCUMENT_SOURCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_5f39587263ee923e | WF_IB_FLEX_IMPORT | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_756b7b07d4f365c0 | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_TECHNICAL_FINALIZATION | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_7eea11a8cded4a8a | WF_TECHNICAL_FEATURE_CACHE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_7fabc787dc6ffca2 | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_812da22fe41fbcba | WF_IBMI_HISTORICAL_SOURCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_82522331e642b008 | WF_CERI_SCORE, WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_866ac2c173ac81d7 | WF_PRICE_SERIES_VERSION | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_874835d87c21855e | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_REGIME_SNAPSHOT | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_8971757daefc9594 | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_IBMI_NATIVE_FEATURE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_919b08f9bc6d4b63 | WF_IB_CONTRACT_IDENTITY | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_a27b7ead7124b2da | WF_COMBINED_REFRESH, WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_a3fd956e147403b1 | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_RANKING_LEGACY_REPLACEMENT, WF_RANKING_PERSISTENCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_a8016e0b9d69e5be | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_RANKING_LEGACY_REPLACEMENT, WF_RANKING_PERSISTENCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_ab4102ebb9197c4d | WF_UPLOAD_INITIALIZATION | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_ad585eacf12c98d7 | WF_CERI_RAW_SOURCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_be226eaac46d1ab2 | WF_IBMI_LIVE_SOURCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_cb55d161e779c8be | WF_TECHNICAL_FEATURE_CACHE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_d526d0a0522f099b | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_RANKING_LEGACY_REPLACEMENT, WF_RANKING_PERSISTENCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_da68f1fde41c070d | WF_UPLOAD_INITIALIZATION | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_dcf27689d2681c17 | WF_CERI_GUIDANCE_ELIGIBILITY | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_de562c9ceb6ee65a | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_TECHNICAL_FEATURE_CACHE, WF_TECHNICAL_FINALIZATION | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_e90cea98144e2940 | WF_CORE_CURRENT_PROJECTION, WF_CORE_RETAINED_EVIDENCE, WF_FUNDAMENTAL_RECALCULATION | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_f0d208ce35a33ed6 | WF_SEC_DOCUMENT_SOURCE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_fb5089df555b2b2f | WF_TECHNICAL_FEATURE_CACHE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |
| EF_fd8cd8fbbbba07f7 | WF_REGIME_DERIVED_DELETE | T14B native writer boundary reconciled; no T14C/T14D caller adoption claim |

## 21. Transaction/fencing behavior

Financial owners and lower sealers use nested atomic scopes; rejection rolls back the caller transaction as well so an earlier autoflush or caught exception cannot later commit partial serving/evidence/projection state. Actual RUNNING status and execution token are checked on the locked job row. Source paths reapply the native fence across intermediate commits; cache and projection locks remain held until the effective outer commit.

## 22. Evidence validation

Sealers verify native kind/run/ticker/profile, identity fingerprint, payload fingerprint, evidence key and exact source addresses. Raw, price and contextual manifests are independently rebuilt/projected from stored facts. Consumed values agree with their sealed targets; original mutable inputs are checked in full, and native reduced frozen projections check supplied values plus the exact locked physical evidence address. Post-seal Technical cache-fallback diagnostics do not replace immutable financial values. Numeric comparisons honor the SQL representation without recomputing formulas. The original Technical cache-fallback overlay changes four serving diagnostic fields after sealing; immutable consumer values/readiness remain authoritative. Its explicit native marker is honored without changing numeric formulas. Older retained ledgers preserve their original address representation; full configuration and native format checks still govern new certification.

## 23. Readiness validation

Creation readiness comes from the referenced immutable ledger. Supplied frozen readiness must match it; the typed decision must match the existing native consumer policy/version/fingerprint/address. No mutable READY bit or execution status substitutes for financial readiness, and no new opportunistic financial policy is introduced.

## 24. Configuration validation

Full native family schema, behavioral fields, semantic/resolution integrity and executable producer compatibility are checked. Canonical outputs match the actual retained C1 pipeline and job bindings. Pinned producer configuration is compared with retained parent slots when present; optional sources do not create missing global configuration/dependency requirements. Historical readers remain independent of current YAML/environment.

## 25. Temporal validation

Financial owners require explicit identity time/calendar/basis and exact persisted context when bound. Source and price observations are checked against native cutoff/session/PIT projection; IBMI and cross-run contextual inputs use their own compatibility rules. Canonical persistence never repairs missing authority using current/latest rows, clocks, sessions or configuration.

## 26. Run/pipeline validation

The uploaded run exists, its owning pipeline/context/fingerprint agree, and durable job run/payload pipeline match the declaration. Source-specific company/ticker/module/profile ownership is checked independently; a request carrying valid-looking fields for another run or pipeline cannot commit.

## 27. Retry/reclaim

Retained C1 calculation/configuration authority is separate from the rotating execution token. PostgreSQL composition demonstrates stale T1 rejection and T2 commit without replacing C1. Native retries reuse retained immutable evidence where keys match; missing C1 authority is rejected rather than repaired from C2.

## 28. Legacy behavior

Legacy/serving-only output is readable in its historical/display role and cannot become certified producer evidence by pointer assignment or a successful execution flag. No legacy backfill, original-context reconstruction, production rewrite or migration is performed. Retained Phase-0–4 fixtures are explicitly seeded pre-adoption history and are not counted as live writer permits.

## 29. Negative dependency certification

Preserve CERI→Ranking, CERI→Setup, CERI→Winner, Setup→Winner, Lifecycle→Winner, IBMI→Winner-direct and Sector→same-run-Ranking as forbidden required dependencies. Only named native contributors are checked. Winner retains independent acquisition; CERI/Setup are not new global gates.

## 30. PostgreSQL certification

The final full Phase 0–4 plus transactional attacks passes 324 tests, no failures/skips, in 2050.02 seconds. Module counts, exact command and warning summary are archived in `T14B_validation_summary.json`. It includes all original 306 Phase 0–4 cases plus 18 domain-fence/authority/cache/source/value/legacy cases. The seven deployed-process Phase-4 tests cover public upload/native execution, frozen C1 after current C2, actual reclaim/retry, resume, downgrade/reupgrade and secret sentinels. All eight native financial writers pass dedicated configuration-adoption PostgreSQL positives. Additional source acquisition has ten unique passing cases: nine unaffected cases plus the bounded CERI authority-read case revalidated separately; its original calculation-query limit remains unchanged. Tests use native PostgreSQL 18.3 on task-owned port 55447 and guarded disposable databases migrated through 0080. No production/live provider/trading runtime is exercised.

## 31. Behavior parity

Fixed native calculator outputs for Fundamental, Technical, Combined, Ranking, Regime, Sector, CERI and IBMI are byte-identical before/after, SHA-256 `0ac9d0ecc7bf981916dcaa4310043f446fc3d2aeaae07fdaf65509ff7293547c`. This is numerical calculator evidence; actual native financial writer commits are certified separately in PostgreSQL. Expected changes are rejection/classification of unsafe authority and invalid cache/projection metadata, not scoring formulas or thresholds.

## 32. Static re-audit

PASS: Ruff check, format (69 Python files), compileall, diff whitespace, one Alembic head and no model/migration drift. Final broader lane: 3,448 passed, seven optional live-IB skips, 321 deselected, 22 warnings; it includes 33 exact T14B boundary/handoff tests and all 20 T14A deterministic inventory regressions. The frozen 427-file production/QA epoch is unchanged across final runs; all 155 current source pins and the original three T14A history hashes match. Current finite inventory: 99 writer families, 291 initiators, 188 business sites; 35 canonical, ten supported-distinct, four projection, eight current-rules, three legacy, one direct-SQL legacy, zero confirmed bypass, four potential bypass (Winner T14C), 34 supporting-state. The source census records 284 writer sites, 191 routes and 34 handlers, with every closure blocker empty. T14B owns current review/pins; original T14A raw discovery/handoff/history remain unchanged. Raw paths/edges remain discovery evidence. Secret scan and task-runtime cleanup receipts are in the validation summary.

## 33. Finding reconciliation

PIPE-008: core/contextual canonical writer closure, partial overall repository scope. PIPE-003/PIPE-007: writer-safe fail-closed behavior; unsafe caller delivery remains T14D. SETUP-005: upstream authority hardened, main Setup/Lifecycle adoption remains T14C/T14D. XINT-006: partial writer enforcement. INV-ENTRY-001: core/contextual enforcement complete, repository-wide adoption still partial.

## 34. T14C/T14D handoff interactions

T14C must adopt Setup/Lifecycle/Alerts, Winner capture/estimation/outcomes/cohorts/generations/publication and the four remaining potential bypass families. T14D must unify standalone/repair/admin/CLI/legacy/bootstrap/scheduler delivery, safe explicit operation modes and authoritative trade episodes. Neither task may restore latest/current authority fallback or rewrite retained C1 during retry.

## 35. Residual risks

T14E integration certification remains after T14C/D. Source acquisition/background refresh scope, original-context reconstruction and privileged external SQL governance remain separate. Native locked validation may retain transactions through source/manifest work; no performance claim or live provider/trading certification is made. No production runtime mutation, migration, rewrite or backfill is required or performed; only task-owned synthetic test databases and processes are used.

## 36. Final verdict

PASS: 32/32 writer families and 36/36 initiators reconciled, zero defects/unreconciled IDs, final PostgreSQL/Phase 0–4 and broader/static gates green, eight native calculator outputs unchanged. The appended architecture section certifies this bounded writer adoption. This certificate resolves its final HEAD to the focused commit containing these files (`feat: enforce mutation authority in core writers`); no self-referential hash is embedded. T14C/T14D/T14E and the separately listed acquisition/reconstruction/SQL-governance work remain deferred.
