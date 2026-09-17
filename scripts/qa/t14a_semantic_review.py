# ruff: noqa: E501
"""Source-reviewed semantic ownership. Discovery never supplies review defaults.

The table records identify authority boundaries, not a claim that every caller
already enforces the Phase-5 declaration. Supporting artifacts retain their own
meaning; they are not promoted into immutable financial evidence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableReview:
    domain: str
    owner: str
    classification: str
    disposition: str
    proof: str


TABLE_REVIEWS: dict[str, TableReview] = {}

AUTHORITY_FIELDS = (
    "calculation_identity",
    "temporal",
    "evidence",
    "readiness",
    "configuration",
    "execution_fencing",
    "pipeline_run",
    "current_latest_fallback",
)


@dataclass(frozen=True)
class PathReview:
    entrypoint: str
    writer: str
    variant: str
    mode: str
    authorities: tuple[str, ...]
    classification: str
    disposition: str
    proof: str

    def __post_init__(self):
        assert len(self.authorities) == len(AUTHORITY_FIELDS)
        assert self.disposition in {"T14B", "T14C", "T14D", "SUPPORTED_DISTINCT_SEMANTICS"}
        assert all(value and "UNREVIEWED" not in value for value in self.authorities)


_F = "app/services/fundamental_score_service.py:recalculate_run_fundamentals"
_UPLOAD = "app/services/upload_service.py:create_upload_run"
_REVIEW = "app/routers/ceri_routes.py:review_ceri_event"
_OVERRIDE = (
    "app/services/ceri/manual_review_service.py:CeriManualReviewService.create_catalyst_override"
)
_PIPELINE_ROUTE = "app/routers/run_routes.py:run_full_pipeline_action"
_SQL_ESTIMATE = "scripts/winner_candidate_estimates.py:write"

# Exact branch-specific reviews. They never become a default for another caller,
# another writer, or a different optional-context invocation of the same API.
PATH_REVIEWS = (
    PathReview(
        "app/routers/upload_routes.py:upload_csv",
        _UPLOAD,
        "UPLOAD_RAW_AND_LEGACY_FUNDAMENTAL",
        "BOOTSTRAP",
        (
            "RAW_ADDRESS_RETAINED; FUNDAMENTAL_CALCULATION_IDENTITY_ABSENT",
            "UPLOAD_PROCESSED_AT_RETAINED; NO_MARKET_CALCULATION_CUTOFF",
            "RAW_ROWS_RETAINED; NO_NATIVE_FUNDAMENTAL_CORE_EVIDENCE",
            "UPLOAD_COMPLETION_IS_NOT_FUNDAMENTAL_READINESS",
            "CURRENT_TAXONOMY_AND_SCORE_DEFAULTS; NO_SEALED_FUNDAMENTAL_CONFIGURATION",
            "NOT_REQUIRED_SYNCHRONOUS",
            "EXACT_NEW_UPLOAD_ID; NO_PIPELINE_OWNER",
            "CURRENT_TAXONOMY_AND_SCORING_DEFAULTS_ARE_MUTATION_AUTHORITY",
        ),
        "CONFIRMED_BYPASS",
        "T14B",
        "create_upload_run validates CSV, retains RawCompanyRow, computes score_rows_v2 directly, constructs FundamentalScore with numeric v2.1/debug fields and commits. It never calls recalculate_run_fundamentals or persist_core_evidence; strict downstream eligibility cannot be inferred from surviving scores.",
    ),
    PathReview(
        _UPLOAD,
        _UPLOAD,
        "DIRECT_UPLOAD_RAW_AND_LEGACY_FUNDAMENTAL",
        "BOOTSTRAP",
        (
            "RAW_ADDRESS_RETAINED; FUNDAMENTAL_CALCULATION_IDENTITY_ABSENT",
            "UPLOAD_PROCESSED_AT_RETAINED; NO_MARKET_CALCULATION_CUTOFF",
            "RAW_ROWS_RETAINED; NO_NATIVE_FUNDAMENTAL_CORE_EVIDENCE",
            "UPLOAD_COMPLETION_IS_NOT_FUNDAMENTAL_READINESS",
            "CURRENT_TAXONOMY_AND_SCORE_DEFAULTS; NO_SEALED_FUNDAMENTAL_CONFIGURATION",
            "NOT_REQUIRED_SYNCHRONOUS; DURABLE_CALLER_MUST_SUPPLY_NATIVE_FENCE",
            "EXACT_NEW_UPLOAD_ID; NO_PIPELINE_OWNER",
            "CURRENT_TAXONOMY_AND_SCORING_DEFAULTS_ARE_MUTATION_AUTHORITY",
        ),
        "CONFIRMED_BYPASS",
        "T14B",
        "Public create_upload_run uses the same direct initial-upload scoring transaction as upload_csv; the Raw artifact is legitimate source evidence but its simultaneously inserted Fundamental values lack native Fundamental lineage.",
    ),
    PathReview(
        "app/routers/run_routes.py:recalculate_fundamentals_action",
        _F,
        "SYNCHRONOUS_FUNDAMENTAL_WITHOUT_CONTEXT",
        "LEGACY_UNCERTIFIED",
        (
            "ABSENT_WITHOUT_MARKET_CUTOFF_AND_PIPELINE_ID",
            "ABSENT_MARKET_CUTOFF",
            "EXACT_RUN_RAW_ROWS; CORE_EVIDENCE_SEAL_RETURNS_NONE_WITHOUT_IDENTITY",
            "NO_NATIVE_PRODUCER_READINESS_EVIDENCE",
            "CURRENT_RESOLVED_CONFIGURATION; NO_RETAINED_FUNDAMENTAL_SNAPSHOT",
            "NOT_REQUIRED_SYNCHRONOUS",
            "EXACT_REQUESTED_UPLOAD; PIPELINE_ID_ABSENT",
            "CURRENT_FUNDAMENTAL_RULE_RESOLUTION",
        ),
        "CONFIRMED_BYPASS",
        "T14D",
        "The route supplies only db/run_id. recalculate_run_fundamentals builds CalculationIdentity only when cutoff/pipeline is supplied (then both are mandatory), replaces serving scores, and supplies retained effective configuration to persist_core_evidence only when cutoff exists. With no identity the ledger helper returns None.",
    ),
    PathReview(
        "app/services/pipeline_executor.py:execute_full_pipeline",
        _F,
        "PIPELINE_FUNDAMENTAL_BOUND_CONTEXT",
        "CANONICAL_CALCULATION",
        (
            "NATIVE_FUNDAMENTAL_IDENTITY_BUILT_AND_CONFIGURATION_BOUND",
            "EXACT_PIPELINE_MARKET_CONTEXT",
            "EXACT_RUN_RAW_SOURCE_MANIFEST_AND_CORE_EVIDENCE",
            "NATIVE_FUNDAMENTAL_PRODUCER_ENVELOPE",
            "CORE_FUNDAMENTAL_EFFECTIVE_CONFIGURATION_DELIVERY",
            "DURABLE_WORKER_DOMAIN_COMMIT_FENCE; NOT_REQUIRED_FOR_SYNC_CALLER",
            "EXACT_PIPELINE_UPLOAD_AND_PIPELINE_ID",
            "NO_CURRENT_CONFIGURATION_SUBSTITUTION_ON_ANCHORED_RETRY",
        ),
        "EXISTING_CANONICAL_OWNER",
        "T14B",
        "execute_full_pipeline supplies market_cutoff and pipeline_run_id to the Fundamental dependency. Configuration delivery retains the core.fundamental family; recalculate_run_fundamentals rejects half-bound context and seals native evidence. This is the bound branch, not the no-context public invocation.",
    ),
    PathReview(
        _PIPELINE_ROUTE,
        _F,
        "PIPE003_USE_DURABLE_PIPELINE_FALSE",
        "LEGACY_UNCERTIFIED",
        (
            "FUNDAMENTAL_IDENTITY_ABSENT_IN_FALSE_BRANCH",
            "FUNDAMENTAL_MARKET_CONTEXT_ABSENT_IN_FALSE_BRANCH",
            "EXACT_UPLOAD_RAW_ROWS; NO_FUNDAMENTAL_NATIVE_EVIDENCE",
            "NO_FUNDAMENTAL_NATIVE_PRODUCER_ENVELOPE",
            "CURRENT_RULES; NO_PIPELINE_FROZEN_CONFIGURATION_BUNDLE",
            "NOT_REQUIRED_SYNCHRONOUS; NO_DURABLE_EXECUTION_TOKEN",
            "EXACT_UPLOAD_ID; NO_PIPELINE_RUN_OR_DURABLE_JOB",
            "CURRENT_RULES_AND_LIVE_FETCH_CAPABILITY_SELECT_FALSE_BRANCH_BEHAVIOR",
        ),
        "CONFIRMED_BYPASS",
        "T14D",
        "run_full_pipeline_action branches on settings.use_durable_pipeline. False calls Fundamental without owning context, then fetch planning/execution, Technical and Combined. It omits the full native orchestration stages and durable/configuration anchors. A later strict Combined rejection does not erase earlier commits. PIPE003 remains OPEN; no branch behavior is changed here.",
    ),
    PathReview(
        _REVIEW,
        _REVIEW,
        "HUMAN_EVENT_REVIEW_METADATA",
        "MAINTENANCE",
        (
            "NOT_REQUIRED_HUMAN_SOURCE_REVIEW",
            "NOT_REQUIRED_SCORE_CUTOFF; CURRENT_REVISION_TARGET_SELECTED",
            "EVENT_ID_AND_MANUAL_PRIOR_NEW_REVIEW_RETAINED; REVISION_FINGERPRINT_NOT_PINNED",
            "NOT_REQUIRED_HUMAN_REVIEW; DOES_NOT_GRANT_SCORE_READINESS",
            "NOT_REQUIRED_NO_SCORE_RULE_EVALUATION",
            "NOT_REQUIRED_SYNCHRONOUS; TARGET_SELECTION_NOT_LOCKED",
            "NOT_REQUIRED_EVENT_SCOPED",
            "IS_CURRENT_REVISION_AND_ACTIVE_REVIEW_ARE_MUTATION_AUTHORITY",
        ),
        "PRIVILEGED_MAINTENANCE",
        "T14D",
        "Local-admin guarded POST selects event and is_current revision, deduplicates active CATALYST_EVENT review, persists reviewer/reason/prior/new data, and changes review_state only. Arbitrary score values are not applied; native CeriSnapshotService/evidence remains a distinct writer. Exact revision version/concurrency checks need the later wrapper.",
    ),
    PathReview(
        _OVERRIDE,
        _OVERRIDE,
        "HUMAN_CATALYST_REVISION_OVERRIDE",
        "MAINTENANCE",
        (
            "NOT_REQUIRED_HUMAN_SOURCE_OVERRIDE",
            "ANNOUNCED_EFFECTIVE_FIELDS_EXPLICIT_OR_COPIED_FROM_TARGET; NO_SCORE_CUTOFF",
            "EXACT_SUPPLIED_REVISION_ID_PRIOR_NEW_REVIEW_AND_APPEND_REVISION; NO_TARGET_HASH_CHECK",
            "NOT_REQUIRED_HUMAN_OVERRIDE; DOES_NOT_CERTIFY_SCORE_ELIGIBILITY",
            "NOT_REQUIRED_NO_SCORE_ALGORITHM",
            "NO_INDEPENDENT_TARGET_LOCK; DURABLE_CALLER_FENCE_REQUIRED_IF_USED",
            "NOT_REQUIRED_CATALYST_REVISION_SCOPED",
            "SUPPLIED_CURRENT_REVISION_AND_EXPLICIT_NEW_VALUES; NO_CURRENT_SCORE_LOOKUP",
        ),
        "PRIVILEGED_MAINTENANCE",
        "T14D",
        "create_catalyst_override appends ManualReview plus revision_number+1 with prior_revision_id, marks supplied old revision non-current, retains provider/source address and reviewer/reason, and flushes. The HTTP metadata review does not call this source-override service. No immutable score evidence is authored here.",
    ),
    PathReview(
        "scripts/winner_candidate_estimates.py:main",
        _SQL_ESTIMATE,
        "REVIEWED_GENERATION11_SQL_ESTIMATE_LEGACY_SCHEMA",
        "LEGACY_UNCERTIFIED",
        (
            "NO_NATIVE_ESTIMATE_CALCULATION_IDENTITY",
            "REVIEWED_FROZEN_GENERATION11_MEMBERSHIP; SCHEMA0061_ONLY",
            "MANIFEST_GENERATION_ESTIMATE_MEMBERSHIP_AND_PROTECTED_STATE_HASH_CHECKS",
            "LEGACY_REVIEWED_CANDIDATE_POLICY; NOT_NATIVE_ESTIMATE_READINESS",
            "NO_PHASE4_SEALED_ESTIMATE_CONFIGURATION",
            "APPROVE_WRITE_ACTOR_REQUEST_KEY_AND_REVIEW_HASH; NO_DURABLE_TOKEN",
            "EXACT_GENERATION11_AND_REVIEWED_PREDICTION_SCOPE",
            "LEGACY_SCHEMA_AND_FIXED_GENERATION_PREFLIGHT; NO_CURRENT_HEAD_REACHABILITY",
        ),
        "LEGACY_WRITER",
        "T14D",
        "write requires approve_write/actor/request_key and reviewed generation/membership/protected-state hashes, inserts ORM estimates and INSERT SELECT evidence members, verifies protected serving IDs, then commits. _preflight requires EXACT EXPECTED_HEAD=0061_winner_estimate_policy, so current certified 0080 rejects it before business writes; retain historical deployed reachability rather than declaring it dead.",
    ),
)

# These are exact receiver families observed in the material-ambiguity packet.
# Only the enumerated callers qualify; a new caller with the same leaf name is
# deliberately unresolved. The old candidate edges remain in raw discovery.
NON_EDGE_REVIEWS: dict[tuple[str, str], str] = {}


def non_edges(callers, calls, proof):
    for caller in callers.split():
        for call in calls:
            NON_EDGE_REVIEWS[(caller, call)] = proof


non_edges(
    "app/observability/db_monitor.py:_git_metadata app/serve.py:_unix_listener app/serve.py:_windows_listener app/serve.py:_windows_process_name app/services/ceri/deployment_identity.py:_local_identity app/services/ib_gateway_health_service.py:is_gateway_process_running app/services/lifecycle_control.py:_git_sha app/worker_supervisor.py:_terminate_launcher app/worker_supervisor.py:_terminate_pid scripts/qa/scan_tracked_secrets.py:tracked_files",
    ("subprocess.run",),
    "Explicit subprocess module import and process/metadata command; receiver is stdlib subprocess, not a repository run() semantic writer.",
)
non_edges(
    "app/services/bar_cache_service.py:_hash_decimal app/services/bar_cache_service.py:_json_decimal app/services/winner_probability/evidence_manifest_service.py:_canonical_decimal",
    ("Decimal(str(value)).normalize",),
    "Explicit Decimal constructor canonicalizes a scalar; not CERI source normalization or ORM persistence.",
)
non_edges(
    "app/services/canonical_evidence.py:CanonicalEvidenceSerializer.canonicalize app/services/ceri/controlled_replay_service.py:_decimal app/services/ceri/revision_feature_service.py:_decimal_text app/services/ceri/snapshot_service.py:_canonical_json_value app/services/winner_probability/feature_extractor.py:_normalize",
    ("value.normalize",),
    "Decimal-typed scalar or isinstance(Decimal) branch normalizes numeric representation; no CERI normalization service receiver.",
)
non_edges(
    "app/services/ceri/estimate_normalizer.py:canonical_estimate_key",
    ("scale.normalize",),
    "Canonical estimate key has Decimal scale; scalar formatting is not dataset ingestion.",
)
non_edges(
    "app/services/ceri/point_in_time_query.py:canonical_estimate_key",
    ("(snapshot.canonical_scale or Decimal('1')).normalize",),
    "Stored Decimal canonical_scale or explicit Decimal fallback; scalar key construction does not mutate a source row.",
)
non_edges(
    "scripts/certify_sec_guidance_real_corpus.py:_decimal_string",
    ("number.normalize",),
    "Decimal number conversion for a corpus string; no source-normalization service invocation.",
)
non_edges(
    "app/services/relative_leadership.py:_market_session_dates app/services/technical_indicators.py:_canonical_market_session",
    ("timestamp.normalize", "timestamp.tz_convert('America/New_York').tz_localize(None).normalize"),
    "Pandas Timestamp market-session conversion returns a timestamp; it is not a CERI source writer.",
)
non_edges(
    "scripts/run_technical_v5_shadow_evaluation.py:_residual_series",
    (
        "pd.to_datetime(left['date'], utc=True).dt.tz_convert(None).dt.normalize",
        "pd.to_datetime(right['date'], utc=True).dt.tz_convert(None).dt.normalize",
        "(1.0 + residual).rolling(21, min_periods=21).apply",
        "(1.0 + residual).rolling(63, min_periods=63).apply",
    ),
    "Pandas datetime accessor/rolling numeric reduction over the shadow frame; no repair CLI or CERI normalization receiver.",
)
non_edges(
    "scripts/run_technical_v5_shadow_evaluation.py:_walk_forward",
    ("frame.groupby(['candidate', 'split']).apply",),
    "Pandas groupby aggregation constructs research metrics; it does not invoke the target-stop repair CLI apply().",
)
non_edges(
    "scripts/run_technical_v5_forensic_recalibration.py:_repair_sector_cache",
    ("result.apply",),
    "Pandas DataFrame applies a numeric row callback and returns a column; no Winner target-stop mutation.",
)
non_edges(
    "app/services/technical_score_service.py:TechnicalScoringOverlapCoordinator._activate_sequential_fallback app/services/technical_score_service.py:TechnicalScoringOverlapCoordinator.abort",
    ("future.cancel",),
    "Executor Future cancels unfinished numerical work; not SecDocumentStateService.cancel.",
)
non_edges(
    "app/services/technical_score_service.py:TechnicalScoringOverlapCoordinator._drain_completed",
    ("outstanding.cancel",),
    "Outstanding executor Future cancellation; no SEC document receiver or document mutation.",
)
non_edges(
    "scripts/qa/ib_fault_proxy.py:_handle_connection",
    ("task.cancel",),
    "asyncio Task cancellation for socket proxy relay; no SEC document state service.",
)
for _caller, _call, _proof in (
    (
        "app/observability/db_monitor.py:JsonlTelemetryWriter._write_line",
        "stream.write",
        "Opened telemetry file stream writes JSONL bytes; no ORM or candidate SQL writer.",
    ),
    (
        "app/observability/metrics.py:start_metrics_http_server.MetricsHandler.do_GET",
        "self.wfile.write",
        "BaseHTTPRequestHandler socket response stream writes Prometheus bytes; no database writer.",
    ),
    (
        "app/serve.py:main",
        "uvicorn.run",
        "Explicit uvicorn module launches ASGI runtime; no repository run() service.",
    ),
    (
        "app/services/ceri/sec/content_cache.py:SecDocumentContentCache.store",
        "compressed.write",
        "gzip file handle writes content cache bytes; financial persistence remains in source state services.",
    ),
    (
        "app/services/lifecycle_control.py:append_lifecycle_event",
        "os.write",
        "os file-descriptor write appends lifecycle JSONL; no database business writer.",
    ),
    (
        "scripts/build_sec_guidance_real_corpus.py:_write_jsonl",
        "handle.write",
        "Opened artifact file handle emits real-corpus JSONL; no source-row insertion.",
    ),
    (
        "scripts/materialize_sec_guidance_real_corpus_v1.py:materialize",
        "handle.write",
        "Opened output artifact file handle emits fixture corpus; no ORM or estimate SQL write().",
    ),
    (
        "scripts/qa/ib_fault_proxy.py:_relay",
        "writer.write",
        "asyncio StreamWriter forwards socket bytes; no repository writer receiver.",
    ),
    (
        "scripts/qa/ib_fault_proxy.py:main",
        "asyncio.run",
        "Explicit asyncio module executes proxy event loop; no financial run() service.",
    ),
):
    NON_EDGE_REVIEWS[(_caller, _call)] = _proof

# Constructor/caller proofs for the three material dispatches whose receiver is
# an injected callable/service rather than an annotated local variable.
REVIEWED_DISPATCH = {
    (
        "app/services/setup_lifecycle/evaluation_service.py:_canonicalize_run",
        "canonicalizer.canonicalize_run",
    ): (
        "app/services/setup_lifecycle/canonicalization.py:SetupLifecycleCanonicalizer.canonicalize_run",
        "evaluate_run passes self.canonicalizer; __init__ declares canonicalizer or SetupLifecycleCanonicalizer; signature adaptation changes only kwargs, not owner",
    ),
    (
        "app/services/winner_probability/pending_outcome_service.py:PendingOutcomeService.materialize_pending_outcomes",
        "self.obligation_service.ensure_for_outcomes",
    ): (
        "app/services/winner_probability/market_data_obligation_service.py:MarketDataObligationService.ensure_for_outcomes",
        "WinnerPredictionCaptureService.__init__ injects MarketDataObligationService when using native repository; otherwise None; branch explicitly checks non-None",
    ),
}


def tables(names, domain, owner, classification, disposition, proof):
    for name in names.split():
        assert name not in TABLE_REVIEWS, name
        TABLE_REVIEWS[name] = TableReview(domain, owner, classification, disposition, proof)


tables(
    "ceri_alert_events",
    "CERI_ALERT",
    "app/services/ceri/alert_service.py:CeriAlertService.persist_alert_for_change",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Anchored native change/rule creation records source IDs, configuration and business dedup; acknowledge/dismiss mutate status; purge invalidates licensed derivatives. Separate from Setup alerts.",
)
tables(
    "ceri_alert_rules",
    "CERI_ALERT",
    "app/services/ceri/alert_service.py:CeriAlertService._rule_for_change",
    "CONFIGURATION_AUTHORITY",
    "T14B",
    "Rule configuration is materialized by the CERI alert owner and frozen in created alert evidence; it is not telemetry.",
)
tables(
    "ceri_catalyst_events ceri_catalyst_sources ceri_estimate_snapshots ceri_earnings_actuals ceri_guidance_events",
    "CERI_SOURCE",
    "app/services/ceri/normalization_service.py:CeriNormalizationService._normalize_record",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Dataset dispatch normalizes exact retained provider records into source artifacts; guidance acceptance and consensus attachment are source decisions; purge redacts/invalidate derivatives under retained-evidence guards.",
)
tables(
    "ceri_catalyst_event_revisions",
    "CERI_SOURCE",
    "app/services/ceri/normalization_service.py:CeriNormalizationService._normalize_record",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Normalization revisions retain provider source and revision chronology. Human override appends a new revision and supersedes the old; review route instead edits review_state on current revision. Both alternates must remain visible.",
)
tables(
    "ceri_manual_reviews",
    "CERI_REVIEW",
    "app/services/ceri/manual_review_service.py:CeriManualReviewService.create_catalyst_override",
    "BUSINESS_SEMANTIC",
    "T14D",
    "Explicit human prior/new values, actor and reason; revision override differs from route CATALYST_EVENT review-state annotation. Neither creates a canonical CERI score or rewrites retained Core evidence.",
)
tables(
    "ceri_companies ceri_company_aliases",
    "CERI_SOURCE",
    "app/services/ceri/sec/identity_repair.py:resolve_and_persist_sec_identity",
    "DOMAIN_SUPPORTING_STATE",
    "T14B",
    "Ticker/provider/CIK identity feeds later ingestion and normalization. Batched/pipeline ensure, normalization, incremental CIK resolution, rebuild and explicit CLI are alternate callers; company identity is not a score.",
)
tables(
    "ceri_controlled_replays",
    "CERI",
    "app/services/ceri/controlled_replay_service.py:CeriControlledReplayService.replay",
    "AUDIT_ONLY",
    "T14D",
    "Controlled replay records selected historical/current-rule inputs and results; its optional feature/snapshot writes are separately classified. A replay name does not prove original-context reconstruction.",
)
tables(
    "ceri_derived_features ceri_feature_build_states",
    "CERI_SOURCE",
    "app/services/ceri/feature_rebuild_service.py:CeriFeatureRebuildService._persist_company",
    "DOMAIN_SUPPORTING_STATE",
    "T14B",
    "Feature rebuild persists company-derived values and source/build watermarks through row-copy or PostgreSQL upsert. Watermarks authorize incremental reuse; they are not operational-only bookkeeping.",
)
tables(
    "ceri_revision_features",
    "CERI_SOURCE",
    "app/services/ceri/revision_feature_service.py:CeriRevisionFeatureService.persist_feature",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Exact estimate/revision comparisons and acceleration feed score evidence. Rebuild batch and controlled replay are alternate persistence paths; copies/upsert are mechanisms, not extra algorithms.",
)
tables(
    "ceri_price_response_features",
    "CERI_SOURCE",
    "app/services/ceri/price_response_service.py:CeriPriceResponseService.persist",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Event effective session and observed price window determine reaction; rebuild also persists these artifacts and purge can invalidate them.",
)
tables(
    "ceri_evidence_dispositions",
    "CURRENT_PROJECTION",
    "app/services/ceri/evidence_eligibility.py:append_evidence_dispositions",
    "CURRENT_PROJECTION",
    "T14D",
    "Append-only disposition IDs choose eligibility/exclusion of retained CERI snapshots; PostgreSQL INSERT RETURNING is delivered by scalars. Quarantine does not rewrite immutable score evidence.",
)
tables(
    "ceri_sec_filing_documents",
    "CERI_SOURCE",
    "app/services/ceri/sec/state_service.py:SecDocumentStateService.register_document",
    "DOMAIN_SUPPORTING_STATE",
    "T14B",
    "Accession/document/content identity and availability feed exact ingestion selection; downloaded-content update and extraction completion are separate stages under SEC ownership. Document state is not financial calculation evidence.",
)
tables(
    "engine_parameters",
    "LEGACY",
    "UNSUPPORTED_READ_ONLY",
    "READ_ONLY",
    "READ_ONLY_NO_ACTION",
    "Legacy mapped model has no production persistence site or mutation caller in the scanned repository; no backfill or owner is fabricated.",
)
tables(
    "ib_contracts",
    "IBMI_SOURCE",
    "app/services/ib_contract_resolver.py:resolve_us_stock_contract",
    "DOMAIN_SUPPORTING_STATE",
    "T14B",
    "Qualified/ambiguous/failed contract identity gates provider acquisition. Clear/mark helpers belong to the same resolver; no new financial algorithm is counted for their field assignments.",
)
tables(
    "ib_execution_fills ib_flex_import_runs",
    "TRADE_JOURNAL",
    "app/services/ib_market_intelligence/flex.py:import_flex_report",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Retained Flex executions are idempotently imported with supersession and source provenance. Exclude-fill route is an alternate explicit human maintenance mutation, followed by episode reconstruction.",
)
tables(
    "ib_histogram_bins ib_histogram_snapshots",
    "IBMI_SOURCE",
    "app/services/ib_market_intelligence/orchestration.py:execute_histogram_fetch",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Contract/request/duration scope and observed histogram bins form provider source artifacts; native intelligence scoring is a separate consumer.",
)
tables(
    "ib_historical_metric_bars ib_historical_metric_revisions",
    "IBMI_SOURCE",
    "app/services/ib_market_intelligence/repository.py:persist_historical_metric_bar",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Provider metric bar scope/version and retained revisions support exact historical feature inputs; current bar replacement differs from immutable revision history.",
)
tables(
    "ib_market_intelligence_snapshots",
    "IBMI_SOURCE",
    "app/services/ib_market_intelligence/repository.py:persist_live_snapshot",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Observed live provider values and request timestamp are source snapshots, distinct from scored IB intelligence features and request telemetry.",
)
tables(
    "ib_scanner_candidates ib_scanner_runs",
    "IBMI_SOURCE",
    "app/services/ib_market_intelligence/orchestration.py:execute_scanner_run",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Scanner request/filter scope and provider ranks select a business universe; not merely process execution telemetry.",
)
tables(
    "ib_scanner_parameter_cache",
    "IBMI_SOURCE",
    "app/services/ib_market_intelligence/orchestration.py:execute_scanner_run",
    "CACHE",
    "T14B",
    "Provider scanner parameter definitions are cached for request construction; source/cache expiry governs reuse, not financial identity equivalence.",
)
tables(
    "ib_trade_episodes",
    "TRADE_JOURNAL",
    "app/services/ib_market_intelligence/journal.py:rebuild_trade_episodes",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Ordered nonsuperseded/nonexcluded execution fills determine positions/PnL; reconstruction updates matching episodes and supersedes absent keys.",
)
tables(
    "ib_trade_research_links",
    "TRADE_JOURNAL",
    "app/services/ib_market_intelligence/journal.py:match_episode_to_research",
    "BUSINESS_SEMANTIC",
    "T14B",
    "Latest completed research before execution cutoff within lookback selects explicit links. Ambiguity/unmatched are retained. This mutation authority selector differs from read-only UI convenience.",
)
tables(
    "market_calculation_contexts",
    "PIPELINE",
    "app/services/market_calculation_context_service.py:create_pipeline_market_context",
    "DOMAIN_SUPPORTING_STATE",
    "T14D",
    "Market session/cutoff/calendar identity is persisted once for owning pipeline; preflight reserve/attach is a distinct explicit scope variant, not a fresh latest-context fallback.",
)
tables(
    "price_series_versions",
    "PRICE",
    "app/services/price_series_version_service.py:maintain_price_series_versions",
    "DOMAIN_SUPPORTING_STATE",
    "T14B",
    "Observed price-bar/revision changes update series versions and digests used in technical cache/source identity.",
)
tables(
    "setup_lifecycle_episodes",
    "LIFECYCLE_TRANSITION",
    "app/services/setup_lifecycle/episode_service.py:SetupLifecycleEpisodeService.apply_snapshot",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Native evaluation/actionability opens/updates/closes episodes and denormalizes snapshots; primary selection is a mutable projection. Repository add/delete are mechanisms and maintenance uses current rules.",
)
tables(
    "setup_lifecycle_evaluation_runs",
    "LIFECYCLE_EVALUATION",
    "app/services/setup_lifecycle/evaluation_service.py:SetupLifecycleEvaluationService.evaluate_run",
    "DOMAIN_SUPPORTING_STATE",
    "T14C",
    "Evaluation scope/mode/version/config/run are recorded for artifacts. Counts/heartbeat/completion are operational fields of this business scope; replay creates its own evaluation owner.",
)
tables(
    "setup_lifecycle_events",
    "LIFECYCLE_TRANSITION",
    "app/services/setup_lifecycle/episode_service.py:SetupLifecycleEpisodeService.apply_snapshot",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Event transition/state/scope and exact evaluation evidence determine business meaning; bulk add and current-event supersession are persistence/projection mechanisms.",
)
tables(
    "setup_signal_snapshot_current_selections setup_signal_snapshot_selection_events",
    "CURRENT_PROJECTION",
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.advance_canonical_selection",
    "CURRENT_PROJECTION",
    "T14C",
    "Separate locked compatible-scope pointer and selection audit preserve snapshot immutability; batched advancement must retain the same native eligibility/ordering predicates.",
)
tables(
    "signal_alert_events",
    "ALERT_DECISION",
    "app/services/setup_lifecycle/alert_service.py:SetupLifecycleAlertService._persist_alert",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Native rule/source/cooldown decision feeds dedicated retained alert evidence and serving event; repository add is a delivery mechanism, purge is separate maintenance.",
)
tables(
    "signal_alert_rules signal_alert_rule_evidence",
    "ALERT_DECISION",
    "app/services/setup_lifecycle/decision_evidence.py:persist_alert_rule_evidence",
    "CONFIGURATION_AUTHORITY",
    "T14C",
    "Seed/upsert current rule definitions and retained exact rule evidence are distinct; dedicated alert decisions pin the retained rule version.",
)
tables(
    "signal_change_events",
    "SETUP",
    "app/services/setup_lifecycle/change_detector.py:SetupLifecycleChangeDetector.detect_and_persist",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Previous/current canonical snapshot comparison under native signal registry creates source-pinned change events; repository add is not its own comparison algorithm.",
)
tables(
    "technical_feature_artifacts",
    "TECHNICAL",
    "app/services/technical_artifact_cache.py:upsert_local_artifact",
    "CACHE",
    "T14B",
    "Exact source/price-series/config/engine key controls cached feature reuse. Access invalidation and shadow-validation counters alter cache support state; no standalone canonical technical evidence is created.",
)
tables(
    "transition_preflight_plans transition_decision_handoff_manifests",
    "PIPELINE",
    "app/services/transition_preflight_plan_service.py:freeze_transition_decision_handoff_manifest",
    "DOMAIN_SUPPORTING_STATE",
    "T14D",
    "Reserved owner context, reviewed selection fingerprint and exact evidence are frozen for downstream decisions; plan creation/expiry/cancellation are separate control operations.",
)
tables(
    "winner_calibration_bins",
    "WINNER_DIAGNOSTICS",
    "app/services/winner_probability/calibration_service.py:CalibrationService.persist_bins",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Caller-supplied reliability report, outcome definition, estimate kind/model/segment are persisted; this lower public writer does not itself resolve original training membership/config authority.",
)
tables(
    "winner_drift_metrics",
    "WINNER_DIAGNOSTICS",
    "app/services/winner_probability/drift_service.py:DriftService.persist_metrics",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Caller-supplied baseline/recent result, definition/date/model scope feed model promotion gates; persisted source-window/configuration closure is absent at this public writer boundary.",
)
tables(
    "winner_similarity_links",
    "WINNER_DIAGNOSTICS",
    "app/services/winner_probability/similarity_service.py:SimilarityService.persist_neighbors",
    "CACHE",
    "T14C",
    "Native ranking checks PIT evidence safety; public persistence accepts supplied neighbors and cutoff/cache version. Exact neighbor/outcome revision authority must survive delivery; cache label alone grants no safety.",
)
tables(
    "winner_cohort_definitions",
    "WINNER_COHORT",
    "app/services/winner_probability/cohort_definition.py:CohortDefinitionService.ensure_definition",
    "CONFIGURATION_AUTHORITY",
    "T14C",
    "Definition key freezes cohort matching dimensions and outcome configuration; current-rule definition creation differs from retained historical evidence selection.",
)
tables(
    "winner_cohort_refresh_state",
    "CURRENT_PROJECTION",
    "app/services/winner_probability/cohort_generation_service.py:CohortGenerationService._activate_locked",
    "CURRENT_PROJECTION",
    "T14C",
    "Locked refresh watermark and generation activation select compatible completed generation; EvidenceWatermarkService state creation supports this pointer, not another cohort algorithm.",
)
tables(
    "winner_evidence_manifests winner_evidence_manifest_members winner_estimate_evidence_members",
    "WINNER_COHORT",
    "app/services/winner_probability/evidence_manifest_service.py:EvidenceManifestService.create_or_get_manifest",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Frozen member IDs/outcome revisions/eligibility/temporal decisions and included-as-of define reusable evidence. Native member helpers share owner; candidate-estimate CLI's empty manifest/SQL membership is an explicit alternate.",
)
tables(
    "winner_estimate_publication_requests",
    "WINNER_PUBLICATION",
    "app/services/winner_probability/estimate_publication_service.py:WinnerEstimatePublicationService.publish",
    "AUDIT_ONLY",
    "T14C",
    "Reviewed actor/request/hash and protected-state checks record atomic candidate-to-serving publication; actual business estimate projection writes must also be inventoried.",
)
tables(
    "winner_market_data_obligations",
    "PRICE",
    "app/services/winner_probability/market_data_obligation_service.py:MarketDataObligationService.ensure_for_outcomes",
    "DOMAIN_SUPPORTING_STATE",
    "T14C",
    "Frozen prediction outcome-contract horizons establish exact missing-price scope; evaluation and fetch-result transitions drive maturation coverage, not solely request telemetry.",
)
tables(
    "winner_model_versions winner_model_lifecycle_events",
    "WINNER_MODEL",
    "app/services/winner_probability/model_registry.py:ModelRegistry.register_model",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Artifact/schema/definition/training cutoff register shadow models; promotion requires native quantitative/calibration/drift gates and retirement protects active fallback. Lifecycle events retain actor/reason; dirty promote/retire writes must be included.",
)
tables(
    "winner_model_training_runs",
    "WINNER_DIAGNOSTICS",
    "app/services/winner_probability/model_training.py:ShadowModelTrainingService.persist_training_run",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Caller report persists algorithm/schema/cutoff/fold plan/metrics/artifact; native training filters pre-cutoff examples, but public persistence does not independently resolve retained membership or sealed configuration.",
)
tables(
    "winner_outcome_definitions",
    "WINNER_OUTCOME",
    "app/services/winner_probability/pending_outcome_service.py:PendingOutcomeService._ensure_outcome_definition",
    "CONFIGURATION_AUTHORITY",
    "T14C",
    "Pending outcome materialization pins prediction's frozen original outcome contract; current defaults cannot silently replace retained definition semantics.",
)
tables(
    "winner_prediction_episodes",
    "WINNER_PREDICTION",
    "app/services/winner_probability/episode_service.py:WinnerEpisodeService.assign_episode",
    "DOMAIN_SUPPORTING_STATE",
    "T14C",
    "Prediction ticker/timeframe/continuity assigns episodes used in evidence de-overlap. Repository add is a mechanism; episode selection is a distinct scope operation.",
)
tables(
    "winner_temporal_validity_decisions",
    "WINNER_PREDICTION",
    "app/services/winner_probability/temporal_validation_service.py:TemporalValidationService.record",
    "BUSINESS_SEMANTIC",
    "T14C",
    "Native capture temporal validation and explicit certification/quarantine retain decisions and update serving validity. Quarantine/certification are current-state support operations, not prediction recomputation.",
)
tables(
    "winner_training_eligibility_decisions winner_training_outcome_replays",
    "WINNER_COHORT",
    "app/services/winner_probability/pre11_compatibility_service.py:Pre11CompatibilityWriteService.persist_decisions_and_replays",
    "BUSINESS_SEMANTIC",
    "T14D",
    "These append-only compatibility decision/replay tables are written by Pre11CompatibilityWriteService. Native capture TrainingEligibilityPolicy writes classification into prediction lineage, not these tables. Retained legacy classification preserves original prediction contracts.",
)
