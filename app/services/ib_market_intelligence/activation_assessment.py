"""Read-only, versioned activation assessment; never changes business status.

This deliberately narrow gate supports complete native research cohorts. It does
not authorize entry, substitute missing producers, or reinterpret old evidence.
"""

from collections import Counter
from decimal import Decimal

from sqlalchemy import select

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.decision_effective_configuration import freeze_decision_configuration
from app.services.warning_taxonomy import (
    WARNING_CATALOG,
    WARNING_TAXONOMY_VERSION,
    normalize_warning_code,
)

REQUIRED_STAGES = (
    "VALIDATING_RUN",
    "SCORING_FUNDAMENTALS",
    "FETCHING_MARKET_DATA",
    "SCORING_TECHNICALS",
    "MARKET_REGIME_SNAPSHOT",
    "COMBINING_RESULTS",
    "RANKING_PROFILES",
    "SECTOR_ROTATION_SNAPSHOT",
    "CERI_PROVIDER_INGEST",
    "FREEZING_DECISION_HANDOFF_MANIFEST",
    "CAPTURING_SETUP_SIGNALS",
    "EVALUATING_SETUP_LIFECYCLES",
    "CAPTURING_WINNER_PREDICTIONS",
)
ZERO_COUNTERS = (
    "incomplete_rows",
    "ib_failure_count",
    "ceri_failed",
    "setup_lifecycle_failed",
    "winner_prediction_failed",
    "ceri_stale",
    "ceri_conflicted",
    "ceri_quarantined",
    "ceri_unrated",
    "ceri_capture_skipped",
    "setup_lifecycle_capture_skipped",
    "setup_lifecycle_evaluation_skipped",
    "winner_prediction_capture_skipped",
    "technical_error_count",
    "fetch_failed",
    "market_regime_low_confidence",
)
SETUP_POLICY_WARNINGS = frozenset(
    {
        "DATA_QUALITY_INSUFFICIENT",
        "MISSING_MARKET_REGIME",
        "MISSING_OPTIONAL_CONTEXT",
        "MISSING_REQUIRED_CLASSIFICATION",
        "MISSING_REQUIRED_SETUP_SCORE",
        "MISSING_REQUIRED_TECHNICAL_SCORE",
        "MISSING_SECTOR_ROTATION",
        "TECHNICAL_CONSUMER_INELIGIBLE",
    }
)


def activation_configuration():
    allowed = sorted(
        row.code
        for row in WARNING_CATALOG.values()
        if row.category.value in {"STRATEGY_RESTRICTION", "INVESTMENT_RISK"}
        and not row.blocks_integrity_readiness
    )
    return freeze_decision_configuration(
        "decision.ibmi.activation",
        {
            "algorithm": "native-advisory-only-partial-v1",
            "required_stages": list(REQUIRED_STAGES),
            "zero_counters": list(ZERO_COUNTERS),
            "warning_catalog_version": WARNING_TAXONOMY_VERSION,
            "permitted_warnings": allowed,
            "business_status_unchanged": True,
            "validate_native_source_scopes": True,
            "replay_volatility_premium": True,
            "policy_exclusion": "certified-risk-only-technical-v1-abstention",
            "setup_policy_projection_warnings": sorted(SETUP_POLICY_WARNINGS),
        },
        {
            "algorithm",
            "required_stages",
            "zero_counters",
            "warning_catalog_version",
            "permitted_warnings",
            "business_status_unchanged",
            "validate_native_source_scopes",
            "replay_volatility_premium",
            "policy_exclusion",
            "setup_policy_projection_warnings",
        },
    )


def assess_activation_manifest(manifest, *, configuration=None):
    """Assess a retained manifest. Missing facts and unknown reasons fail closed."""
    config = configuration or activation_configuration()
    config.require_family("decision.ibmi.activation")
    if config.values != activation_configuration().values:
        raise ValueError("ACTIVATION_CONFIGURATION_UNSUPPORTED")
    blockers = []
    if manifest.get("business_status") not in {"PARTIAL", "COMPLETED"}:
        blockers.append("NON_SUCCESS_TERMINAL_BUSINESS_STATUS")
    stages = manifest.get("stages") or []
    if Counter(row["name"] for row in stages) != Counter(REQUIRED_STAGES) or any(
        row.get("status") != "COMPLETED" or row.get("error") for row in stages
    ):
        blockers.append("REQUIRED_STAGE_NOT_SUCCESSFUL")
    counters = manifest.get("counters") or {}
    for key in ZERO_COUNTERS:
        if type(counters.get(key)) is not int or counters[key] != 0:
            blockers.append("NONZERO_OR_MISSING_COUNTER:" + key)
    checks = manifest.get("checks") or {}
    required_checks = {
        "certified_sources",
        "scope_complete",
        "fundamental_technical_admitted",
        "ranking_complete",
        "ibmi_included_fresh_compatible",
        "arithmetic_reconciled",
        "native_data_complete",
        "policy_exclusions_accounted",
        "partial_reasons_accounted",
    }
    for key in sorted(required_checks):
        if checks.get(key) is not True:
            blockers.append("REQUIRED_EVIDENCE_CHECK_FAILED:" + key)
    allowed = set(config.values["permitted_warnings"])
    for item in manifest.get("warnings", []):
        if normalize_warning_code(item["code"]) not in allowed:
            blockers.append("NON_ADVISORY_WARNING:" + item["code"])
    if not manifest.get("tickers") or not manifest.get("evidence"):
        blockers.append("EMPTY_EVIDENCE_SCOPE")
    if manifest.get("unexplained_reasons") != []:
        blockers.append("UNEXPLAINED_REASONS")
    return Canonical.canonicalize(
        {
            "accepted": not blockers,
            "assessment_version": "native-advisory-only-partial-v1",
            "business_status": manifest.get("business_status"),
            "blockers": sorted(set(blockers)),
            "configuration": config.snapshot.as_dict(),
            "manifest": manifest,
            "manifest_fingerprint": Canonical.fingerprint(manifest),
            "trading_authority": False,
        }
    )


def assess_pipeline_activation(db, pipeline_id):
    """Construct facts from exact sealed source pointers in the native database."""
    from app.models.ceri_tables import CeriScoreSnapshot
    from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
    from app.models.tables import (
        CombinedResult,
        CoreCalculationEvidence,
        CoreCalculationEvidenceSource,
        FundamentalScore,
        MarketRegimeSnapshot,
        PipelineRun,
        PipelineStep,
        RankingResult,
        RawCompanyRow,
        SectorRotationSnapshot,
        SetupSignalSnapshot,
        TechnicalScore,
        WinnerAcquisitionDecisionRecord,
    )
    from app.services.core_effective_configuration import core_configuration_from_evidence
    from app.services.core_mutation_authority import _validate_evidence, validate_source_values
    from app.services.producer_readiness import readiness_from_evidence
    from app.services.technical_consumer_eligibility import (
        TECHNICAL_TO_COMBINED,
        TECHNICAL_TO_RANKING,
    )
    from app.services.technical_research_admission import policy_for_core_configuration

    pipeline = db.get(PipelineRun, pipeline_id)
    if pipeline is None:
        raise ValueError("ACTIVATION_PIPELINE_MISSING")
    result = dict(pipeline.result_json or {})
    run = pipeline.upload_run_id
    tickers = sorted(
        set(db.scalars(select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == run)))
    )
    manifest = {
        "pipeline_id": pipeline.id,
        "run_id": run,
        "business_status": pipeline.status,
        "tickers": tickers,
        "stages": [
            {"name": row.step_name, "status": row.status, "error": row.error_message}
            for row in db.scalars(
                select(PipelineStep).where(PipelineStep.pipeline_run_id == pipeline.id)
            )
        ],
        "counters": {key: result.get(key) for key in ZERO_COUNTERS},
        "warnings": [],
        "evidence": [],
        "policy_exclusions": [],
        "unexplained_reasons": [],
        "checks": {},
    }
    checks = manifest["checks"]
    checks.update(
        {
            key: True
            for key in (
                "certified_sources",
                "scope_complete",
                "fundamental_technical_admitted",
                "ranking_complete",
                "ibmi_included_fresh_compatible",
                "arithmetic_reconciled",
                "native_data_complete",
                "policy_exclusions_accounted",
                "partial_reasons_accounted",
            )
        }
    )
    if (
        pipeline.error_message
        or result.get("degraded") is not False
        or result.get("failure_reason")
    ):
        manifest["unexplained_reasons"].append("PIPELINE_EXECUTION_OR_DEGRADATION")

    def rows(model):
        return list(db.scalars(select(model).where(model.run_id == run)))

    def certified(row):
        evidence = db.get(CoreCalculationEvidence, row.evidence_id)
        _validate_evidence(evidence)
        if evidence.artifact_kind != "IBMI":
            temporal = evidence.calculation_identity_json["temporal"]
            if (
                evidence.run_id != run
                or temporal["as_of_session"]["value"] != result["input_as_of_session"]
                or temporal["calendar"]["value"]["calendar_version"]
                != result["market_calendar_version"]
            ):
                raise ValueError("ACTIVATION_NATIVE_SOURCE_SCOPE_MISMATCH")
        if evidence.artifact_kind == "SETUP":
            from app.services.core_calculation_evidence import calculation_evidence_payload
            from app.services.setup_lifecycle.decision_evidence import _SETUP_PROJECTION_FIELDS

            current = calculation_evidence_payload(row, excluded_columns=_SETUP_PROJECTION_FIELDS)
            if any(
                current.get(key) != value
                for key, value in evidence.payload_json.items()
                if key in current
            ):
                raise ValueError("ACTIVATION_SETUP_PROJECTION_MISMATCH")
        elif evidence.artifact_kind == "SECTOR":
            from sqlalchemy import Float, Numeric, inspect

            from app.models.tables import SectorRotationRow

            def matches(current, payload):
                columns = inspect(current).mapper.columns
                for key, value in payload.items():
                    if key in {"rows", "producer_readiness", "effective_configuration_at_creation"}:
                        continue
                    name = key if key in columns else key + "_json"
                    if name not in columns:
                        continue
                    actual = getattr(current, name)
                    if (
                        actual is not None
                        and value is not None
                        and isinstance(columns[name].type, (Numeric, Float))
                    ):
                        equal = Decimal(str(actual)) == Decimal(str(value))
                    else:
                        equal = Canonical.canonicalize({key: actual})[key] == value
                    if not equal:
                        return False
                return True

            children = {
                child.sector_slug: child
                for child in db.scalars(
                    select(SectorRotationRow).where(SectorRotationRow.snapshot_id == row.id)
                )
            }
            expected = evidence.payload_json
            if (
                not matches(row, expected)
                or len(children) != len(expected["rows"])
                or any(
                    item["sector_slug"] not in children
                    or not matches(children[item["sector_slug"]], item)
                    for item in expected["rows"]
                )
            ):
                raise ValueError("ACTIVATION_SECTOR_PROJECTION_MISMATCH")
        elif evidence.artifact_kind != "CERI":
            validate_source_values(
                row, evidence, db=db, locked_source_rows={(type(row), row.id): row}
            )
        else:
            # CERI comparison pointers are supporting projections; financial output is sealed.
            for key, value in evidence.payload_json["decision_output"].items():
                if key in {"comparison_state", "comparison_snapshot_id"}:
                    continue
                if hasattr(row, key) and Canonical.canonicalize(getattr(row, key)) != value:
                    raise ValueError("ACTIVATION_CERI_PROJECTION_MISMATCH:" + key)
        manifest["evidence"].append(
            {
                "kind": evidence.artifact_kind,
                "id": evidence.id,
                "fingerprint": evidence.payload_fingerprint,
                "ticker": evidence.ticker,
            }
        )
        return evidence

    try:
        fundamental, technical, combined, ranking = [
            rows(model)
            for model in (FundamentalScore, TechnicalScore, CombinedResult, RankingResult)
        ]
        groups = [fundamental, technical, combined]
        checks["scope_complete"] = all(
            Counter(row.ticker for row in group) == Counter(tickers) for group in groups
        ) and len(ranking) == len(tickers) * result.get("ranking_profiles", 0)
        sources = {}
        for row in (*fundamental, *technical):
            evidence = certified(row)
            sources[(evidence.artifact_kind, row.ticker)] = evidence
            codes = evidence.payload_json.get("warning_flags_json", [])
            if evidence.artifact_kind == "FUNDAMENTAL":
                codes = (evidence.payload_json.get("v2_warning_flags_json") or {}).get("flags", [])
            for code in codes:
                manifest["warnings"].append(
                    {"kind": evidence.artifact_kind, "ticker": row.ticker, "code": code}
                )
        technical_errors = sum(
            bool(readiness_from_evidence(sources[("TECHNICAL", ticker)]).blocking_reasons)
            for ticker in tickers
        )
        if manifest["counters"]["technical_error_count"] is None:
            manifest["counters"]["technical_error_count"] = technical_errors
        fetch_step = db.scalar(
            select(PipelineStep).where(
                PipelineStep.pipeline_run_id == pipeline.id,
                PipelineStep.step_name == "FETCHING_MARKET_DATA",
            )
        )
        fetch = fetch_step.result_json or {}
        fetch_valid = (
            not fetch.get("blockers")
            and sorted(fetch.get("technical_tickers", [])) == tickers
            and sorted(fetch.get("downstream_tickers", [])) == tickers
            and all(
                item.get("disposition") == "READY"
                and item.get("technical_eligible") is True
                and item.get("downstream_eligible") is True
                for item in (fetch.get("tickers") or {}).values()
            )
            and sorted(fetch.get("tickers", {})) == tickers
        )
        if manifest["counters"]["fetch_failed"] is None:
            manifest["counters"]["fetch_failed"] = int(not fetch_valid)
        checks["native_data_complete"] &= fetch_valid
        for row in (*combined, *ranking):
            evidence = certified(row)
            payload = evidence.payload_json
            debug = payload["debug_json"]
            complete = all(
                payload.get(key) is True
                for key in ("is_complete", "has_fundamental", "has_technical")
            )
            checks["fundamental_technical_admitted"] &= complete
            config = core_configuration_from_evidence(evidence)
            default = (
                TECHNICAL_TO_COMBINED
                if evidence.artifact_kind == "COMBINED"
                else TECHNICAL_TO_RANKING
            )
            policy, frozen = policy_for_core_configuration(config.values, default)
            from app.services.producer_readiness import NativeReadinessMetrics

            permission = policy.evaluate(
                readiness_from_evidence(sources[("TECHNICAL", row.ticker)]),
                config=NativeReadinessMetrics.freeze({"configuration": frozen.snapshot.as_dict()}),
            )
            actual = debug["technical_consumer_eligibility"]["decision"]
            expected = permission.to_dto()
            matches = Canonical.dumps(actual) == Canonical.dumps(expected)
            if not matches:
                manifest["unexplained_reasons"].append(
                    "TECHNICAL_PERMISSION_REPLAY_MISMATCH:"
                    + str(evidence.id)
                    + ":"
                    + ",".join(
                        key
                        for key in expected
                        if Canonical.dumps(expected[key]) != Canonical.dumps(actual.get(key))
                    )
                )
            checks["fundamental_technical_admitted"] &= matches and actual["status"] == "ELIGIBLE"
            fund = debug["contextual_consumer_eligibility"]["fundamental"]
            fund_admitted = fund["included"] is True and fund["decision"]["status"] == "ELIGIBLE"
            if fund.get("fundamental_admission_mode") == "ACTIVE":
                from app.services.contextual_consumer_eligibility import _fundamental_frozen_input
                from app.services.fundamental_admission_policy import (
                    evaluate_fundamental_admission,
                    fundamental_input_from_frozen_row,
                )

                native_input = _fundamental_frozen_input(sources[("FUNDAMENTAL", row.ticker)])
                replay = evaluate_fundamental_admission(
                    fundamental_input_from_frozen_row(
                        native_input, consumer_edge="FUNDAMENTAL_TO_" + evidence.artifact_kind
                    ),
                    authoritative=True,
                )
                fund_admitted = (
                    fund["included"] is True
                    and replay.eligibility.value == "INCLUDED"
                    and Canonical.dumps(replay.canonical_payload())
                    == Canonical.dumps(fund.get("orthogonal_decision"))
                )
            checks["fundamental_technical_admitted"] &= fund_admitted
            if actual.get("justification", {}).get("research_only"):
                checks["fundamental_technical_admitted"] &= (
                    payload.get("position_size_hint") == "No new entry"
                )
            for code in payload.get("warning_flags_json", []):
                manifest["warnings"].append(
                    {"kind": evidence.artifact_kind, "ticker": row.ticker, "code": code}
                )
        profiles = {row.ranking_profile for row in ranking}
        checks["ranking_complete"] = len(profiles) == result.get("ranking_profiles") and all(
            Counter(row.ticker for row in ranking if row.ranking_profile == profile)
            == Counter(tickers)
            for profile in profiles
        )

        def feature_for(evidence, permission, module):
            producer_id = permission["decision"]["producer_evidence_id"]
            feature = db.scalar(
                select(IBIntelligenceFeature).where(
                    IBIntelligenceFeature.evidence_id == producer_id
                )
            )
            producer = certified(feature)
            edge = db.scalar(
                select(CoreCalculationEvidenceSource).where(
                    CoreCalculationEvidenceSource.evidence_id == evidence.id,
                    CoreCalculationEvidenceSource.source_evidence_id == producer_id,
                )
            )
            temporal = evidence.calculation_identity_json["temporal"]
            from datetime import datetime

            cutoff = datetime.fromisoformat(
                temporal["calculation_cutoff"]["value"].replace("Z", "+00:00")
            )
            valid = (
                permission["included"] is True
                and permission["decision"]["status"] == "ELIGIBLE"
                and (
                    producer.artifact_kind == "IBMI"
                    and feature.module == module
                    and edge is not None
                    and feature.ticker == evidence.ticker
                    and feature.coverage_status == "AVAILABLE"
                    and feature.freshness_status == "AVAILABLE"
                    and feature.calculated_at <= cutoff
                    and feature.calculation_cutoff_at <= cutoff
                    and str(feature.as_of_session) == temporal["as_of_session"]["value"]
                    and feature.calendar_version
                    == temporal["calendar"]["value"]["calendar_version"]
                    and readiness_from_evidence(producer).status.value == "READY"
                )
            )
            checks["ibmi_included_fresh_compatible"] &= valid
            return feature

        liquidity_count = 0
        for row in ranking:
            evidence = db.get(CoreCalculationEvidence, row.evidence_id)
            debug = evidence.payload_json["debug_json"]
            overlay = debug["profile"]["tradeability_overlay"]
            if not overlay["enabled"]:
                continue
            feature = feature_for(
                evidence, debug["contextual_consumer_eligibility"]["ibmi_liquidity"], "LIQUIDITY"
            )
            volume = feature.components_json["dollar_volume"]
            below = (
                volume is not None
                and overlay["minimum_dollar_volume"] is not None
                and volume < overlay["minimum_dollar_volume"]
            )
            raw = (
                overlay["very_poor_penalty"]
                if feature.classification == "VERY_POOR"
                else overlay["poor_penalty"]
                if feature.classification == "POOR" or below
                else 0
            )
            expected = round(min(raw, overlay["maximum_penalty"]), 4)
            calc = debug["calculation"]
            score = round(
                max(0, min(10, calc["weighted_score_before_penalty"] - calc["total_penalty"])), 4
            )
            checks["arithmetic_reconciled"] &= (
                Decimal(str(expected))
                == Decimal(str(row.penalties_json.get("ibkr_tradeability", 0)))
                and Decimal(str(score)) == row.profile_score
            )
            liquidity_count += 1
        checks["ibmi_included_fresh_compatible"] &= liquidity_count == 3 * len(tickers)
        ceri = rows(CeriScoreSnapshot)
        checks["scope_complete"] &= Counter(row.ticker for row in ceri) == Counter(tickers)
        checks["native_data_complete"] &= result.get(
            "ceri_completion_state"
        ) == "CERTIFIED" and result.get("ceri_certified_capture_count") == len(tickers)
        for row in ceri:
            evidence = certified(row)
            checks["native_data_complete"] &= not readiness_from_evidence(evidence).blocking_reasons
            output = evidence.payload_json["decision_output"]
            lineage = output["evidence_lineage_json"]
            permission = lineage["contextual_consumer_eligibility"]["ibmi_volatility"]
            volatility = feature_for(evidence, permission, "VOLATILITY")
            checks["native_data_complete"] &= (
                bool(lineage.get("revision_feature_ids"))
                and output.get("opportunity_unrated_reason") != "NO_ELIGIBLE_ESTIMATE_INPUT"
            )
            ledger = output["event_risk_ledger_json"]
            premium = sum(
                Decimal(str(item["value"]))
                for item in ledger["penalties"]
                if item["name"] == "ibkr_options_event_premium"
            )
            from app.services.ib_market_intelligence.calculations import options_event_premium_score
            from app.services.ib_market_intelligence.dtos import FeatureResult

            producer = db.get(CoreCalculationEvidence, volatility.evidence_id)
            maximum = producer.payload_json["effective_configuration"]["resolved_config"][
                "volatility"
            ]["ceri_risk_max_contribution"]
            native_feature = FeatureResult(
                "VOLATILITY",
                volatility.classification,
                None,
                volatility.confidence,
                volatility.freshness_status,
                volatility.coverage_status,
                volatility.components_json,
            )
            checks["arithmetic_reconciled"] &= premium == Decimal(
                str(options_event_premium_score(native_feature, maximum=float(maximum)))
            )
            other = sum(
                Decimal(str(item["value"]))
                for item in ledger["penalties"]
                if item["name"] != "ibkr_options_event_premium"
            )
            dominant = max(
                (Decimal(str(item["score"])) for item in ledger["components"]), default=Decimal(0)
            )
            cap = Decimal(
                str(
                    evidence.payload_json["effective_rule_payload"]["resolved_config"][
                        "event_risk"
                    ]["secondary_penalty_cap"]
                )
            )
            checks["arithmetic_reconciled"] &= (
                min(Decimal(10), dominant + min(cap, premium + other)) == row.event_risk_score
            )

        regime = rows(MarketRegimeSnapshot)
        checks["native_data_complete"] &= len(regime) == 1 and str(
            regime[0].confidence
        ).lower() in {"normal", "high"}
        if manifest["counters"]["market_regime_low_confidence"] is None:
            manifest["counters"]["market_regime_low_confidence"] = int(
                not (len(regime) == 1 and str(regime[0].confidence).lower() in {"normal", "high"})
            )
        if regime:
            certified(regime[0])
        sector = rows(SectorRotationSnapshot)
        checks["native_data_complete"] &= len(sector) == 1
        if sector:
            certified(sector[0])
        # Re-evaluate unchanged strict Setup/Winner consumers on the same retained run.
        from app.services.market_calculation_context_service import market_context_for_pipeline
        from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
        from app.services.technical_consumer_eligibility import TECHNICAL_TO_SETUP
        from app.services.winner_probability.consumer_eligibility import (
            WinnerSourceEligibilityError,
            winner_decision_inputs,
        )

        context = SetupLifecycleSourceLoader().load_run_context(
            db, run, market_cutoff=market_context_for_pipeline(db, pipeline)
        )
        snapshots = rows(SetupSignalSnapshot)
        checks["policy_exclusions_accounted"] &= Counter(
            row.ticker for row in snapshots
        ) == Counter(tickers)
        expected_exclusions = Counter()
        for ticker_context in context.tickers:
            symbol = ticker_context.raw_row.ticker
            tech = readiness_from_evidence(sources[("TECHNICAL", symbol)])
            setup_permission = TECHNICAL_TO_SETUP.evaluate(tech).to_dto()
            snapshot = next(row for row in snapshots if row.ticker == symbol)
            certified(snapshot)
            recorded = snapshot.source_lineage_json["technical_consumer_eligibility"]["decision"]
            checks["policy_exclusions_accounted"] &= recorded == setup_permission
            if setup_permission["status"] != "ELIGIBLE":
                checks["policy_exclusions_accounted"] &= (
                    setup_permission["reasons"] == ["DEGRADED_POLICY_UNDECIDED"]
                    and snapshot.dual_score is None
                )
                checks["policy_exclusions_accounted"] &= (
                    "TECHNICAL_CONSUMER_INELIGIBLE" in snapshot.warning_flags_json
                    and set(snapshot.warning_flags_json) <= SETUP_POLICY_WARNINGS
                )
            try:
                winner_decision_inputs(context, ticker_context)
            except WinnerSourceEligibilityError as error:
                decisions = error.decisions.canonical_payload()
                checks["policy_exclusions_accounted"] &= (
                    error.reason == "winner_readiness_undecided" and error.sources == ("technical",)
                )
                expected_exclusions[error.reason] += 1
                manifest["policy_exclusions"].append(
                    {
                        "ticker": symbol,
                        "setup": setup_permission,
                        "winner_reason": error.reason,
                        "winner": decisions,
                    }
                )
        # The append-only acquisition ledger retains exact reasons omitted by the summary DTO.
        attempts = list(
            db.scalars(
                select(WinnerAcquisitionDecisionRecord).where(
                    WinnerAcquisitionDecisionRecord.upload_run_id == run
                )
            )
        )
        checks["policy_exclusions_accounted"] &= Counter(row.ticker for row in attempts) == Counter(
            tickers
        )
        for attempt in attempts:
            frozen = attempt.decision_payload_json
            checks["policy_exclusions_accounted"] &= (
                Canonical.fingerprint(frozen) == attempt.decision_fingerprint
                and attempt.acquisition_outcome == "EXCLUDED"
            )
            expected = next(
                item for item in manifest["policy_exclusions"] if item["ticker"] == attempt.ticker
            )
            observed = {
                item["source"]: {k: v for k, v in item.items() if k != "source"}
                for item in frozen["source_decisions"]
            }
            checks["policy_exclusions_accounted"] &= (
                observed == expected["winner"]
                and attempt.eligibility_reason == expected["winner_reason"]
            )
            manifest["evidence"].append(
                {
                    "kind": "WINNER_ACQUISITION",
                    "id": attempt.id,
                    "fingerprint": attempt.decision_fingerprint,
                    "ticker": attempt.ticker,
                }
            )
        checks["policy_exclusions_accounted"] &= sum(expected_exclusions.values()) == result.get(
            "winner_prediction_excluded"
        )
        warning_rows = sum(bool(row.warning_flags_json) for row in combined)
        checks["partial_reasons_accounted"] = result.get("warning_rows") == warning_rows and (
            pipeline.status != "PARTIAL" or warning_rows > 0
        )
        checks["native_data_complete"] &= all(
            readiness_from_evidence(sources[("TECHNICAL", ticker)]).blocking_reasons == ()
            for ticker in tickers
        )
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration) as error:
        checks["certified_sources"] = False
        manifest["unexplained_reasons"].append(type(error).__name__ + ":" + str(error))
    return assess_activation_manifest(manifest)
