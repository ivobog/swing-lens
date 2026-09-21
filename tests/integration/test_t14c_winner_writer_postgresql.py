"""Actual Winner writer adoption against disposable PostgreSQL."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from native_mutation_support import seed_native_core
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_t14c_decision_writer_postgresql import _native_liquidity_source

from app.models.tables import (
    UploadRun,
    WinnerModelVersion,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
)
from app.services.decision_effective_configuration import (
    resolve_setup_configuration,
    resolve_winner_configuration,
)
from app.services.transition_preflight_plan_service import (
    freeze_transition_decision_handoff_manifest,
)
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.model_artifact_service import artifact_hash
from app.services.winner_probability.model_authority import (
    baseline_artifact,
    model_body,
    validate_model_source,
)
from app.services.winner_probability.model_registry import ModelRegistry
from app.services.winner_probability.prediction_authority import validate_prediction_source

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_native_winner_complete_capture_and_immutable_retry(contextual_engine):
    config = load_winner_probability_config()
    config = replace(config, engine=replace(config.engine, enabled=True))
    configurations = tuple(
        resolve_winner_configuration(config, family=family)
        for family in ("prediction", "outcome", "cohort", "generation")
    ) + (resolve_setup_configuration(),)
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(
            db,
            extra_configurations=configurations,
            raw_values={**_native_liquidity_source(), "upcoming_earnings_date": "2026-11-10"},
        )
        db.commit()
        handoff = freeze_transition_decision_handoff_manifest(
            db, upload_run_id=7, market_cutoff=cutoff
        )
        handoff_id = handoff.id
        db.commit()
        decision_at = datetime.now(UTC)
        from app.models.tables import IBContract

        db.add(
            IBContract(
                ticker="ACME",
                ib_conid=140014,
                symbol="ACME",
                exchange="SMART",
                primary_exchange="NASDAQ",
                currency="USD",
                sec_type="STK",
                resolution_status="RESOLVED",
            )
        )
        db.commit()
        _native_capture_child_rollback(db, config, cutoff, handoff_id, decision_at)
        service = WinnerPredictionCaptureService()
        result = service.capture_run(
            db,
            run_id=7,
            config=config,
            market_cutoff=cutoff,
            decision_handoff_manifest_id=handoff_id,
            decision_at=decision_at,
        )
        assert result.failed == 0, str(result.as_dict())
        assert result.inserted == 1, str(result.as_dict())
        prediction = db.scalar(select(WinnerPredictionSnapshot))
        validate_prediction_source(db, prediction)
        from app.models.tables import WinnerPredictionEpisode
        from app.services.winner_probability.episode_service import validate_episode_source

        validate_episode_source(db, db.get(WinnerPredictionEpisode, prediction.episode_id))
        assert db.scalar(select(func.count()).select_from(WinnerProbabilityEstimate)) == 1

        db.commit()
        retry = service.capture_run(
            db,
            run_id=7,
            config=config,
            market_cutoff=cutoff,
            decision_handoff_manifest_id=handoff_id,
            decision_at=decision_at,
        )
        assert retry.failed == 0 and retry.duplicate == 1, retry.as_dict()
        assert prediction.prediction_as_of_date == cutoff.latest_completed_session
        definition = db.scalar(
            select(WinnerOutcomeDefinition).where(WinnerOutcomeDefinition.is_primary)
        )
        db.commit()
        artifact = baseline_artifact(config, definition.id)
        model_args = {
            "model_key": "native-cohort",
            "algorithm": "cohort",
            "outcome_definition_id": definition.id,
            "entry_model": definition.entry_model,
            "training_cutoff_at": prediction.source_data_cutoff_at,
            "artifact_hash": artifact_hash(artifact),
            "artifact_format": "cohort_baseline",
            "artifact_schema_version": "winner-model-artifact-v1",
            "feature_schema_version": config.feature_schema.version,
            "calculation_version": config.engine.calculation_version,
            "config_hash": config.config_hash,
        }
        prototype = WinnerModelVersion(
            **model_args,
            hyperparameters_json={},
            metrics_json={},
            preprocessing_json={"feature_order": ["setup_family"]},
            calibration_json={"method": "beta_binomial", "version": "1"},
            dependency_versions_json={},
        )
        registry = ModelRegistry()
        model = registry.register_model(
            db,
            **model_args,
            actor="native-test",
            reason="create",
            config=config,
            artifact_payload=artifact,
            preprocessing=prototype.preprocessing_json,
            calibration=prototype.calibration_json,
            mutation_context=_model_context(prototype, config, "register_model", "create"),
        )
        db.commit()
        validate_model_source(db, model)
        model_id = model.id
        db.add(UploadRun(id=979, filename="model-attack.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="CALIBRATION_PIN_REQUIRED"):
            registry.promote_model(
                db,
                model_id=model_id,
                actor="native-test",
                reason="promote",
                config=config,
                mutation_context=_model_context(model, config, "promote_model", "promote"),
            )
        db.commit()
        assert db.get(UploadRun, 979) is None
        _native_empty_diagnostics(db, model, prediction, definition, config)
        retirement_config = replace(
            config,
            engine=replace(
                config.engine,
                calculation_version="current-governance-v2",
            ),
        )
        registry.retire_model(
            db,
            model_id=model_id,
            actor="native-test",
            reason="retire",
            config=retirement_config,
            mutation_context=_model_context(model, retirement_config, "retire_model", "retire"),
        )
        db.commit()
        assert db.get(WinnerModelVersion, model_id).status == "RETIRED"
        from app.services.winner_probability.probability_estimator import ProbabilityEstimator

        db.add(UploadRun(id=978, filename="estimate-attack.csv", status="COMPLETED"))
        disabled = replace(config, engine=replace(config.engine, enabled=False))
        with pytest.raises(ValueError, match="ESTIMATE_DISABLED"):
            ProbabilityEstimator().create_decision_time_estimate(
                db, prediction=prediction, outcome_definition=definition, config=disabled
            )
        db.commit()
        assert db.get(UploadRun, 978) is None
        assert db.scalar(select(func.count()).select_from(WinnerProbabilityEstimate)) == 1
        _native_capture_attacks(
            db, service, config, cutoff, handoff_id, decision_at, prediction, definition
        )
        _native_outcome_operation(db, prediction, definition)
        _native_generation_operation(db, prediction, definition, config)
        _native_shadow_governance(db, prediction, definition, config)


def _native_capture_child_rollback(db, config, cutoff, handoff_id, decision_at):
    from app.models.tables import (
        WinnerForwardOutcome,
        WinnerPredictionEpisode,
        WinnerTargetStopOutcome,
    )
    from app.services.winner_probability.decision_time_estimate_service import (
        DecisionTimeEstimateService,
    )

    class DisabledEstimate(DecisionTimeEstimateService):
        def create_decision_time_estimate(self, db, *, prediction, outcome_definition, config):
            assert prediction.id is not None
            assert db.scalar(select(func.count()).select_from(WinnerForwardOutcome)) > 0
            return super().create_decision_time_estimate(
                db,
                prediction=prediction,
                outcome_definition=outcome_definition,
                config=replace(config, engine=replace(config.engine, enabled=False)),
            )

    rejected = WinnerPredictionCaptureService(
        decision_time_estimate_service=DisabledEstimate()
    ).capture_run(
        db,
        run_id=7,
        config=config,
        market_cutoff=cutoff,
        decision_handoff_manifest_id=handoff_id,
        decision_at=decision_at,
    )
    assert rejected.failed == 1 and rejected.inserted == 0, rejected.as_dict()
    assert "ESTIMATE_DISABLED" in str(rejected.as_dict())
    for model in (
        WinnerPredictionSnapshot,
        WinnerPredictionEpisode,
        WinnerForwardOutcome,
        WinnerTargetStopOutcome,
        WinnerProbabilityEstimate,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    db.commit()


def _native_empty_diagnostics(db, model, prediction, definition, config):
    from dataclasses import asdict

    from app.services.market_clock_service import MarketClockService
    from app.services.winner_probability.calibration_service import CalibrationService
    from app.services.winner_probability.drift_service import DriftService
    from app.services.winner_probability.evidence_manifest_service import EvidenceManifestService
    from app.services.winner_probability.model_training import ShadowModelTrainingService
    from app.services.winner_probability.mutation_authority import validate_diagnostic_artifact
    from app.services.winner_probability.similarity_service import SimilarityService

    effective = resolve_winner_configuration(config, family="generation")
    population = EvidenceManifestService().create_or_get_manifest(db, evidence=()).manifest
    clock = MarketClockService().cutoff_for(datetime.now(UTC), reason="NATIVE_DIAGNOSTIC_OPERATION")
    calibration = CalibrationService()
    report = calibration.calculate(())
    contract = {
        "artifact": "CALIBRATION",
        "report": asdict(report),
        "estimate_kind": "DECISION_TIME",
        "segment": {},
        "estimate_ids": (),
    }
    bins = calibration.persist_bins(
        db,
        report=report,
        outcome_definition_id=definition.id,
        estimate_kind="DECISION_TIME",
        model_version_id=model.id,
        effective_configuration=effective,
        mutation_context=_diagnostic_context(
            model, population, effective, clock, "CalibrationService.persist_bins", contract
        ),
    )
    assert len(bins) == 10
    validate_diagnostic_artifact(bins[0], "segment_json")
    drift = DriftService()
    results = drift.calculate(
        baseline=(), recent=(), comparison_window="native-empty", config=config
    )
    contract = {
        "artifact": "DRIFT",
        "results": [asdict(row) for row in results],
        "as_of_date": clock.latest_completed_session,
        "baseline_estimate_ids": (),
        "recent_estimate_ids": (),
        "estimate_kind": "DECISION_TIME",
    }
    metrics = drift.persist_metrics(
        db,
        results=results,
        outcome_definition_id=definition.id,
        model_version_id=model.id,
        as_of_date=clock.latest_completed_session,
        effective_configuration=effective,
        mutation_context=_diagnostic_context(
            model, population, effective, clock, "DriftService.persist_metrics", contract
        ),
    )
    assert len(metrics) == 4
    validate_diagnostic_artifact(metrics[0], "segment_json")
    trainer = ShadowModelTrainingService()
    training = trainer.train_shadow_report(
        (),
        feature_names=("setup_family",),
        outcome_definition_id=definition.id,
        training_cutoff_at=clock.cutoff_at,
        config=config,
    )
    contract = {
        "artifact": "TRAINING",
        "report": asdict(training),
        "training_cutoff_at": clock.cutoff_at,
        "training_parameters": {},
    }
    run = trainer.persist_training_run(
        db,
        report=training,
        outcome_definition_id=definition.id,
        training_cutoff_at=clock.cutoff_at,
        effective_configuration=effective,
        mutation_context=_diagnostic_context(
            definition,
            population,
            effective,
            clock,
            "ShadowModelTrainingService.persist_training_run",
            contract,
        ),
    )
    validate_diagnostic_artifact(run, "fold_plan_json")
    contract = {
        "artifact": "SIMILARITY",
        "neighbors": [],
        "source_cutoff_at": clock.cutoff_at,
        "cache_version": "similarity-v1",
        "feature_names": ("setup_family",),
        "feature_weights": None,
        "limit": 10,
        "one_per_episode": True,
    }
    assert (
        SimilarityService().persist_neighbors(
            db,
            prediction=prediction,
            outcome_definition=definition,
            neighbors=(),
            source_cutoff_at=clock.cutoff_at,
            cache_version="similarity-v1",
            feature_names=("setup_family",),
            effective_configuration=effective,
            mutation_context=_diagnostic_context(
                prediction,
                population,
                effective,
                clock,
                "SimilarityService.persist_neighbors",
                contract,
            ),
        )
        == ()
    )
    db.commit()


def _diagnostic_context(subject, population, effective, clock, writer, contract):
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.core_calculation_evidence import calculation_evidence_payload
    from app.services.domain_mutation import (
        DomainMutationContext,
        MutationDomain,
        MutationEntryPointDescriptor,
        MutationEvidenceReference,
        MutationSemanticMode,
        MutationWriterDescriptor,
    )

    body = calculation_evidence_payload(
        subject,
        excluded_columns={"status", "activated_at", "retired_at"}
        if isinstance(subject, WinnerModelVersion)
        else set(),
    )
    fingerprint = Canonical.fingerprint(contract)
    return DomainMutationContext(
        domain=MutationDomain.WINNER_DIAGNOSTICS,
        semantic_mode=MutationSemanticMode.CANONICAL_CALCULATION,
        entrypoint=MutationEntryPointDescriptor("native-diagnostic-test", "NATIVE_SEMANTIC_WRITER"),
        writer=MutationWriterDescriptor(
            writer, "phase5-winner-diagnostic-v1", MutationDomain.WINNER_DIAGNOSTICS
        ),
        reason="diagnostic native proof",
        configuration=effective.snapshot.identity,
        temporal=clock,
        evidence=(
            MutationEvidenceReference(
                "diagnostic_subject", subject.__tablename__, subject.id, Canonical.fingerprint(body)
            ),
            MutationEvidenceReference(
                "input_manifest", population.__tablename__, population.id, population.manifest_hash
            ),
            MutationEvidenceReference(
                "diagnostic_contract", "native_manifest", fingerprint, fingerprint
            ),
        ),
    )


def _model_context(model, config, action, reason):
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.domain_mutation import (
        DomainMutationContext,
        MutationDomain,
        MutationEntryPointDescriptor,
        MutationEvidenceReference,
        MutationSemanticMode,
        MutationWriterDescriptor,
    )
    from app.services.market_clock_service import MarketClockService

    body = model_body(model)
    request = {
        "action": action,
        "actor": "native-test",
        "reason": reason,
        "model_key": model.model_key,
        "model_id": model.id,
        "replacement_id": None,
        "allow_without_active_fallback": False,
    }
    return DomainMutationContext(
        domain=MutationDomain.WINNER_MODEL,
        semantic_mode=MutationSemanticMode.MAINTENANCE,
        entrypoint=MutationEntryPointDescriptor("native-model-test", "NATIVE_SEMANTIC_WRITER"),
        writer=MutationWriterDescriptor(
            "ModelRegistry." + action, "phase5-winner-model-v1", MutationDomain.WINNER_MODEL
        ),
        reason=reason,
        configuration=resolve_winner_configuration(config, family="generation").snapshot.identity,
        temporal=MarketClockService().cutoff_for(
            datetime.now(UTC), reason="NATIVE_MODEL_OPERATION"
        ),
        evidence=tuple(
            MutationEvidenceReference(
                role,
                "native_manifest",
                Canonical.fingerprint(payload),
                Canonical.fingerprint(payload),
            )
            for role, payload in (("model_version", body), ("governance_action", request))
        ),
    )


def _native_capture_attacks(
    db, service, config, cutoff, handoff_id, decision_at, prediction, definition
):
    from app.services.winner_probability.calculation_identity import acquire_winner_sources
    from app.services.winner_probability.capture_service import _MutableCaptureCounts
    from app.services.winner_probability.feature_extractor import WinnerFeatureExtractor
    from app.services.winner_probability.probability_estimator import ProbabilityEstimator

    context = service.repository.load_run_context(
        db, 7, market_cutoff=cutoff, decision_handoff_manifest_id=handoff_id
    )
    acquisition = acquire_winner_sources(
        context, context.tickers[0], run_id=7, market_cutoff=cutoff, winner_config=config
    )
    args = dict(
        run_id=7,
        run_context=context,
        ticker_context=context.tickers[0],
        config=config,
        primary_definition=definition,
        captured_at=datetime.now(UTC),
        decision_at=decision_at,
        reconstruction_method=None,
        source_quality_flags=(),
        production_training_allowed=None,
        acquisition=acquisition,
        totals=_MutableCaptureCounts(),
    )
    db.add(UploadRun(id=977, filename="clock-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="EXPLICIT_DECISION_TIME_REQUIRED"):
        service._capture_ticker(db, **{**args, "decision_at": None})
    db.commit()
    assert db.get(UploadRun, 977) is None

    class WrongFeatures(WinnerFeatureExtractor):
        def extract(self, *values, **keywords):
            features = super().extract(*values, **keywords)
            return replace(features, feature_json={**features.feature_json, "combined_score": "99"})

    wrong = WinnerPredictionCaptureService(feature_extractor=WrongFeatures())
    db.add(UploadRun(id=976, filename="vector-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="FROZEN_VECTOR_SOURCE_MISMATCH"):
        wrong._capture_ticker(db, **args)
    db.commit()
    assert db.get(UploadRun, 976) is None
    original = prediction.technical_score
    prediction.technical_score = 99
    db.add(UploadRun(id=975, filename="body-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
        ProbabilityEstimator().create_decision_time_estimate(
            db, prediction=prediction, outcome_definition=definition, config=config
        )
    db.commit()
    assert prediction.technical_score == original
    assert db.get(UploadRun, 975) is None
    drift = replace(
        config, feature_schema=replace(config.feature_schema, version="wrong-native-schema")
    )
    db.add(UploadRun(id=974, filename="configuration-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="CAPTURE_CONFIGURATION_MISMATCH"):
        ProbabilityEstimator().create_decision_time_estimate(
            db, prediction=prediction, outcome_definition=definition, config=drift
        )
    db.commit()
    assert db.get(UploadRun, 974) is None
    from app.services.winner_probability.training_eligibility import TrainingEligibilityPolicy

    db.add(UploadRun(id=969, filename="training-metadata-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="CAPTURE_TRAINING_METADATA_CREATION_ONLY"):
        TrainingEligibilityPolicy().persist_capture_decision(prediction)
    db.commit()
    assert db.get(UploadRun, 969) is None
    assert db.scalar(select(func.count()).select_from(WinnerPredictionSnapshot)) == 1
    assert db.scalar(select(func.count()).select_from(WinnerProbabilityEstimate)) == 1


def _native_outcome_operation(db, prediction, definition):
    from datetime import time, timedelta

    from app.models.tables import PriceBar, WinnerForwardOutcome, WinnerTargetStopOutcome
    from app.services.bar_cache_service import price_bar_data_hash
    from app.services.winner_probability.market_data_obligation_service import (
        required_outcome_sessions,
    )
    from app.services.winner_probability.outcome_authority import validate_outcome_body
    from app.services.winner_probability.outcome_revision_service import OutcomeRevisionService
    from app.services.winner_probability.outcome_service import (
        OutcomeMaturationService,
        _benchmark_for_prediction,
        _sector_proxy_for_prediction,
    )

    outcome = db.scalar(
        select(WinnerForwardOutcome).where(
            WinnerForwardOutcome.prediction_id == prediction.id,
            WinnerForwardOutcome.entry_model == definition.entry_model,
            WinnerForwardOutcome.horizon_sessions == definition.horizon_sessions,
        )
    )
    symbols = {
        prediction.ticker,
        _benchmark_for_prediction(prediction),
        _sector_proxy_for_prediction(prediction),
    } - {None}
    for symbol in symbols:
        for day in required_outcome_sessions(outcome.entry_session, outcome.horizon_sessions):
            row = PriceBar(
                ticker=symbol,
                bar_date=day,
                timeframe="1 day",
                what_to_show="ADJUSTED_LAST",
                open=100,
                high=110,
                low=99,
                close=108,
                volume=100000,
                source="T14C_EXPLICIT_SIMULATED_OUTCOME_FRAME",
            )
            row.data_hash = price_bar_data_hash(row)
            db.add(row)
    db.commit()
    # Test fixture simulates the later maturation operation explicitly; this is
    # independent operation time, not a fabricated original capture context.
    now = datetime.combine(outcome.due_session + timedelta(days=1), time(22), tzinfo=UTC)
    from app.models.tables import WinnerMarketDataObligation
    from app.services.winner_probability.market_data_obligation_service import (
        MarketDataObligationService,
    )

    obligations = list(
        db.scalars(
            select(WinnerMarketDataObligation).where(
                WinnerMarketDataObligation.forward_outcome_id == outcome.id,
            )
        )
    )
    sync = MarketDataObligationService().evaluate(db, obligations=obligations, now=now)
    db.commit()
    assert sync.satisfied == 1 and sync.fetch_required == 1
    db.add(UploadRun(id=966, filename="missing-outcome-clock.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="EXPLICIT_OPERATION_TIME_REQUIRED"):
        OutcomeMaturationService().process_due_outcomes(db)
    db.commit()
    assert db.get(UploadRun, 966) is None
    service = OutcomeMaturationService()
    original_features = prediction.feature_vector_hash
    from concurrent.futures import ThreadPoolExecutor

    first_id = outcome.id
    engine = db.get_bind()
    db.commit()

    def concurrent_maturation():
        with Session(engine) as worker:
            row = worker.get(WinnerForwardOutcome, first_id)
            result = OutcomeMaturationService().process_forward_outcome(worker, row, now=now)
            worker.commit()
            return result.matured, result.target_stop_matured

    with ThreadPoolExecutor(max_workers=2) as workers:
        first_results = list(workers.map(lambda _: concurrent_maturation(), range(2)))
    assert sum(item[0] for item in first_results) == 1
    assert sum(item[1] for item in first_results) == 1
    db.refresh(outcome)
    result = service.process_forward_outcome(db, outcome, now=now)
    db.commit()
    assert result.matured == 0 and result.target_stop_matured == 0
    original_proof = validate_outcome_body(outcome)
    assert all(
        item["revision_identity_status"] == "REVISION_IDENTITY_UNAVAILABLE"
        for item in original_proof["prices"]
    )
    target = db.scalar(
        select(WinnerTargetStopOutcome).where(
            WinnerTargetStopOutcome.prediction_id == prediction.id,
            WinnerTargetStopOutcome.outcome_definition_id == definition.id,
            WinnerTargetStopOutcome.is_current_revision.is_(True),
        )
    )
    validate_outcome_body(target)
    assert outcome.close_return_pct == 8
    assert target.primary_winner
    assert prediction.feature_vector_hash == original_features

    from concurrent.futures import ThreadPoolExecutor

    current_id = outcome.id
    engine = db.get_bind()
    db.commit()

    def concurrent_retry():
        with Session(engine) as worker:
            row = worker.get(WinnerForwardOutcome, current_id)
            retry_result = OutcomeMaturationService().process_forward_outcome(worker, row, now=now)
            worker.commit()
            return retry_result.revised, retry_result.matured

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(lambda _: concurrent_retry(), range(2))) == [(0, 0), (0, 0)]
    retry = service.process_forward_outcome(db, outcome, now=now)
    db.commit()
    assert retry.matured == 0 and retry.revised == 0
    db.add(UploadRun(id=968, filename="revision-bypass.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="SUPPORTING_SEMANTIC_TRANSACTION_REQUIRED"):
        OutcomeRevisionService().upsert_forward_revision(
            db,
            outcome,
            {"source_bar_lineage_hash": "forged", "close_return_pct": 99},
            now=now,
        )
    db.commit()
    assert db.get(UploadRun, 968) is None
    assert outcome.close_return_pct == 8

    from copy import deepcopy

    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.winner_probability.outcome_authority import outcome_body

    forged = deepcopy(outcome.metadata_json)
    forged["native_outcome_proof"]["body_fingerprint"] = "caller-forged"
    outcome.metadata_json["native_outcome_proof"] = forged["native_outcome_proof"]
    outcome.metadata_json["native_outcome_proof"]["body_fingerprint"] = Canonical.fingerprint(
        outcome_body(outcome)
    )
    outcome.metadata_json["native_outcome_proof"]["configuration"]["semantic_hash"] = (
        "caller-forged"
    )
    db.add(UploadRun(id=965, filename="inplace-outcome-proof.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="RETAINED_PREDECESSOR_MISMATCH"):
        service.process_forward_outcome(db, outcome, now=now)
    db.commit()
    assert db.get(UploadRun, 965) is None
    validate_outcome_body(outcome)

    from dataclasses import replace

    from app.models.tables import PriceBarRevision
    from app.services.bar_cache_service import _price_bar_values
    from app.services.winner_probability.target_stop_service import TargetStopService

    revised_bar = db.scalar(
        select(PriceBar).where(
            PriceBar.ticker == prediction.ticker,
            PriceBar.bar_date == outcome.due_session,
            PriceBar.what_to_show == "ADJUSTED_LAST",
        )
    )
    previous_values = _price_bar_values(revised_bar)
    previous_hash = revised_bar.data_hash
    revised_bar.close = 109
    revised_bar.revision_count = 1
    revised_bar.revised_at = now
    revised_bar.data_hash = price_bar_data_hash(revised_bar)
    price_revision = PriceBarRevision(
        price_bar_id=revised_bar.id,
        ticker=revised_bar.ticker,
        bar_date=revised_bar.bar_date,
        timeframe=revised_bar.timeframe,
        what_to_show=revised_bar.what_to_show,
        revision_number=1,
        previous_data_hash=previous_hash,
        new_data_hash=revised_bar.data_hash,
        previous_values_json=previous_values,
        new_values_json=_price_bar_values(revised_bar),
        source=revised_bar.source,
        observed_at=now,
    )
    db.add(price_revision)
    db.commit()
    later = now + timedelta(hours=1)

    class AlteredTargetCalculator(TargetStopService):
        def evaluate(self, **kwargs):
            return replace(super().evaluate(**kwargs), primary_winner=False)

    db.add(UploadRun(id=967, filename="partial-outcome-revision.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="TARGET_STOP_NATIVE_RESULT_MISMATCH"):
        OutcomeMaturationService(
            target_stop_service=AlteredTargetCalculator()
        ).process_forward_outcome(
            db,
            outcome,
            now=later,
        )
    db.commit()
    assert db.get(UploadRun, 967) is None
    assert outcome.is_current_revision and outcome.close_return_pct == 8
    assert (
        db.scalar(
            select(func.count())
            .select_from(WinnerForwardOutcome)
            .where(
                WinnerForwardOutcome.prediction_id == prediction.id,
                WinnerForwardOutcome.entry_model == outcome.entry_model,
                WinnerForwardOutcome.horizon_sessions == outcome.horizon_sessions,
            )
        )
        == 1
    )
    revision_result = service.process_forward_outcome(db, outcome, now=later)
    db.commit()
    assert revision_result.revised == 1 and revision_result.target_stop_matured == 1
    current = db.scalar(
        select(WinnerForwardOutcome).where(
            WinnerForwardOutcome.prediction_id == prediction.id,
            WinnerForwardOutcome.entry_model == outcome.entry_model,
            WinnerForwardOutcome.horizon_sessions == outcome.horizon_sessions,
            WinnerForwardOutcome.is_current_revision.is_(True),
        )
    )
    assert current.id != outcome.id and current.revision == 2
    assert current.close_return_pct == 9 and outcome.close_return_pct == 8
    assert not outcome.is_current_revision
    proof = validate_outcome_body(current)
    assert any(item["price_bar_revision_id"] == price_revision.id for item in proof["prices"])
    assert any(item["revision_identity_status"] == "EXACT" for item in proof["prices"])
    assert validate_outcome_body(outcome)["prices"] == original_proof["prices"]
    current_target = db.scalar(
        select(WinnerTargetStopOutcome).where(
            WinnerTargetStopOutcome.prediction_id == prediction.id,
            WinnerTargetStopOutcome.outcome_definition_id == definition.id,
            WinnerTargetStopOutcome.is_current_revision.is_(True),
        )
    )
    assert current_target.forward_outcome_id == current.id and current_target.revision == 2
    validate_outcome_body(current_target)
    assert prediction.feature_vector_hash == original_features


def _native_generation_operation(db, prediction, definition, config):
    from datetime import timedelta

    from app.models.tables import WinnerForwardOutcome
    from app.services.winner_probability.cohort_authority import (
        validate_completion,
        validate_generation,
    )
    from app.services.winner_probability.cohort_generation_service import (
        CohortGenerationService,
        EvidenceWatermarkService,
        contract_for,
    )
    from app.services.winner_probability.cohort_materialization_service import (
        CohortMaterializationService,
    )

    source = db.scalar(
        select(WinnerForwardOutcome).where(
            WinnerForwardOutcome.prediction_id == prediction.id,
            WinnerForwardOutcome.entry_model == definition.entry_model,
            WinnerForwardOutcome.horizon_sessions == definition.horizon_sessions,
            WinnerForwardOutcome.is_current_revision.is_(True),
        )
    )
    at = source.matured_at + timedelta(seconds=1)
    watermark = EvidenceWatermarkService().advance_to_current_material_evidence(
        db,
        outcome_definition=definition,
        config=config,
        observed_at=at,
    )
    db.commit()
    service = CohortGenerationService()
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.models.tables import WinnerCohortGeneration, WinnerCohortRefreshState

    state_id, definition_id = watermark.state.id, definition.id
    engine = db.get_bind()
    db.commit()
    barrier = Barrier(2)

    def create_generation():
        with Session(engine) as child:
            state = child.get(WinnerCohortRefreshState, state_id)
            source_definition = child.get(WinnerOutcomeDefinition, definition_id)
            barrier.wait(timeout=30)
            created = CohortGenerationService().capture_or_resume(
                child,
                state=state,
                contract=contract_for(source_definition, config),
                requested_at=at + timedelta(seconds=1),
                config=config,
            )
            child.commit()
            return created.id

    with ThreadPoolExecutor(max_workers=2) as workers:
        created_ids = list(workers.map(lambda _: create_generation(), range(2)))
    assert created_ids[0] == created_ids[1]
    generation = db.get(WinnerCohortGeneration, created_ids[0])
    db.commit()
    validate_generation(db, generation, config)
    incomplete = service.publish(
        db,
        generation=generation,
        config=config,
        lease_guard=lambda: None,
        published_at=at + timedelta(seconds=2),
        predecessor_id=None,
    )
    assert incomplete.status == "REJECTED_INCOMPLETE"
    db.commit()
    from app.models.tables import WinnerCohortStatistic

    generation_id = generation.id
    db.commit()

    def reject_after_first_statistic():
        count = db.scalar(
            select(func.count())
            .select_from(WinnerCohortStatistic)
            .where(WinnerCohortStatistic.generation_id == generation_id)
        )
        if count:
            raise ValueError("T14C_EXPLICIT_PARTIAL_COHORT_REJECTION")

    db.add(UploadRun(id=954, filename="partial-native-cohort.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="EXPLICIT_PARTIAL_COHORT_REJECTION"):
        CohortMaterializationService().materialize_slice(
            db,
            generation=generation,
            outcome_definition=definition,
            config=config,
            lease_guard=reject_after_first_statistic,
            should_cancel=lambda: False,
            publish_when_ready=False,
            operation_at=at + timedelta(seconds=3),
        )
    db.commit()
    assert db.get(UploadRun, 954) is None
    assert generation.status == "BUILDING" and generation.root_manifest_hash is None
    assert (
        db.scalar(
            select(func.count())
            .select_from(WinnerCohortStatistic)
            .where(WinnerCohortStatistic.generation_id == generation_id)
        )
        == 0
    )
    generation_id = generation.id
    db.commit()
    barrier = Barrier(2)

    def complete_generation():
        with Session(engine) as child:
            exact = child.get(WinnerCohortGeneration, generation_id)
            source_definition = child.get(WinnerOutcomeDefinition, definition_id)
            barrier.wait(timeout=30)
            result = CohortMaterializationService().materialize_slice(
                child,
                generation=exact,
                outcome_definition=source_definition,
                config=config,
                lease_guard=lambda: None,
                should_cancel=lambda: False,
                publish_when_ready=False,
                operation_at=at + timedelta(seconds=3),
            )
            child.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as workers:
        completions = list(workers.map(lambda _: complete_generation(), range(2)))
    assert sum(not result.no_op for result in completions) == 1
    result = completions[0]
    db.commit()
    assert result.status == "READY"
    validate_completion(db, generation, config)
    assert generation.evidence_row_count == 1
    _native_winner_reclaim(db, prediction, definition, config, at + timedelta(seconds=4))
    db.commit()
    barrier = Barrier(2)

    def publish_generation():
        with Session(engine) as child:
            exact = child.get(WinnerCohortGeneration, generation_id)
            barrier.wait(timeout=30)
            try:
                published = CohortGenerationService().publish(
                    child,
                    generation=exact,
                    config=config,
                    lease_guard=lambda: None,
                    published_at=at + timedelta(seconds=4),
                    predecessor_id=None,
                )
                child.commit()
                return published.status
            except ValueError as error:
                assert any(
                    message in str(error)
                    for message in (
                        "PUBLICATION_PREDECESSOR_MISMATCH",
                        "RETAINED_SOURCE_BODY_MISMATCH",
                    )
                )
                child.rollback()
                return "EXACT_PREDECESSOR_CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as workers:
        publications = list(workers.map(lambda _: publish_generation(), range(2)))
    assert sorted(publications) == ["EXACT_PREDECESSOR_CONFLICT", "PUBLISHED"]
    db.expire_all()
    duplicate = service.publish(
        db,
        generation=generation,
        config=config,
        lease_guard=lambda: None,
        published_at=at + timedelta(seconds=5),
        predecessor_id=generation.id,
    )
    db.commit()
    assert duplicate.status == "ALREADY_ACTIVE"
    db.add(UploadRun(id=964, filename="publication-predecessor.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="PUBLICATION_PREDECESSOR_MISMATCH"):
        service.publish(
            db,
            generation=generation,
            config=config,
            lease_guard=lambda: None,
            published_at=at + timedelta(seconds=6),
            predecessor_id=None,
        )
    db.commit()
    assert db.get(UploadRun, 964) is None
    _native_reviewed_publication(db, prediction, definition, config, generation, at)


def _native_winner_reclaim(db, prediction, definition, config, at):
    from datetime import timedelta

    from test_winner_jobs_reliability_postgresql import _claim_registered_job

    from app.models.tables import PipelineRun
    from app.services.background_job_service import JobLeaseLost, enqueue_job, recover_stale_jobs
    from app.services.domain_write_fence import fence_domain_commits
    from app.services.winner_probability.cohort_generation_service import EvidenceWatermarkService

    pipeline_id = db.scalar(
        select(PipelineRun.id).where(PipelineRun.upload_run_id == prediction.run_id)
    )
    queued = enqueue_job(db, "WINNER_COHORT_REFRESH", {"pipeline_run_id": pipeline_id})
    db.commit()
    first = _claim_registered_job(db, "t14c-native-winner-A")
    assert first.id == queued.id
    token_a = first.execution_token
    first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert recover_stale_jobs(db, stale_after_seconds=1) == 1
    db.commit()
    second = _claim_registered_job(db, "t14c-native-winner-B")
    assert second.id == queued.id and second.execution_token != token_a
    token_b = second.execution_token
    db.commit()
    definition_id = definition.id
    args = dict(config=config, observed_at=at)
    db.add(UploadRun(id=959, filename="stale-winner-token.csv", status="COMPLETED"))
    with fence_domain_commits(job_id=queued.id, execution_token=token_a):
        with pytest.raises(JobLeaseLost):
            EvidenceWatermarkService().advance_to_current_material_evidence(
                db, outcome_definition=db.get(WinnerOutcomeDefinition, definition_id), **args
            )
    db.commit()
    assert db.get(UploadRun, 959) is None
    with fence_domain_commits(job_id=queued.id, execution_token=token_b):
        EvidenceWatermarkService().advance_to_current_material_evidence(
            db, outcome_definition=db.get(WinnerOutcomeDefinition, definition_id), **args
        )
        db.commit()
    from app.services.background_job_service import mark_job_completed

    mark_job_completed(db, second, result={"native_reclaim": True}, execution_token=token_b)
    db.commit()


def _native_reviewed_publication(db, prediction, definition, config, previous, at):
    import hashlib
    from datetime import timedelta

    from app.services.winner_probability.cohort_generation_service import (
        CohortGenerationService,
        EvidenceWatermarkService,
        contract_for,
    )
    from app.services.winner_probability.cohort_materialization_service import (
        CohortMaterializationService,
    )
    from app.services.winner_probability.estimate_publication_service import (
        WinnerEstimatePublicationService,
        serving_id_snapshot,
        transition_manifest_hash,
    )
    from app.services.winner_probability.probability_estimator import ProbabilityEstimator

    estimator = ProbabilityEstimator()
    original = estimator.create_latest_rescore_from_generation(
        db, prediction=prediction, outcome_definition=definition, generation=previous, config=config
    ).estimate
    db.commit()
    changed = replace(
        config, cohort=replace(config.cohort, prior_strength=config.cohort.prior_strength + 1)
    )
    from app.services.winner_probability.config import winner_probability_config_hash

    changed = replace(changed, config_hash=winner_probability_config_hash(changed))
    watermark = EvidenceWatermarkService().advance_to_current_material_evidence(
        db, outcome_definition=definition, config=changed, observed_at=at + timedelta(seconds=10)
    )
    db.commit()
    generation = CohortGenerationService().capture_or_resume(
        db,
        state=watermark.state,
        contract=contract_for(definition, changed),
        config=changed,
        requested_at=at + timedelta(seconds=11),
    )
    db.commit()
    CohortMaterializationService().materialize_slice(
        db,
        generation=generation,
        outcome_definition=definition,
        config=changed,
        lease_guard=lambda: None,
        should_cancel=lambda: False,
        publish_when_ready=False,
        operation_at=at + timedelta(seconds=12),
    )
    db.commit()
    candidate_review = hashlib.sha256(b"T14C explicit native candidate review").hexdigest()
    candidate = estimator.create_candidate_rescore_from_generation(
        db,
        prediction=prediction,
        outcome_definition=definition,
        generation=generation,
        config=changed,
        source_version="T14C_NATIVE_REVIEWED_CANDIDATE",
        supersedes_estimate_id=original.id,
        reviewed_manifest_hash=candidate_review,
    ).estimate
    db.commit()
    before = serving_id_snapshot(db)
    after_ids = sorted((set(before["ids"]) - {original.id}) | {candidate.id})
    digest = hashlib.sha256()
    for value in after_ids:
        digest.update(f"{value}\n".encode())
    manifest = {
        "generation": {
            "id": generation.id,
            "generation_key": generation.generation_key,
            "root_manifest_hash": generation.root_manifest_hash,
        },
        "previous_generation": {"id": previous.id, "generation_key": previous.generation_key},
        "candidate_manifest_hash": candidate_review,
        "candidate_count": 1,
        "quarantined_prediction_count": 0,
        "records": [
            {
                "original_estimate_id": original.id,
                "candidate_estimate_id": candidate.id,
                "decision_reconstruction_category": None,
            }
        ],
        "serving_before": {key: before[key] for key in ("count", "sha256")},
        "serving_after": {"count": len(after_ids), "sha256": digest.hexdigest()},
    }
    manifest["artifact_hash"] = transition_manifest_hash(manifest)
    args = dict(
        manifest=manifest,
        reviewed_manifest_hash=manifest["artifact_hash"],
        candidate_manifest_hash=candidate_review,
        actor="native-test",
        request_key="native-reviewed-1",
        approve_write=True,
        published_at=at + timedelta(seconds=13),
        config=changed,
    )
    db.commit()
    from copy import deepcopy

    from app.services.winner_probability.estimate_publication_service import (
        PublicationInvariantViolation,
    )

    for invalid in (
        {**args, "published_at": None},
        {**args, "config": replace(changed, engine=replace(changed.engine, enabled=False))},
    ):
        db.add(UploadRun(id=953, filename="publication-authority-attack.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="OPERATION_TIME_REQUIRED|OPERATION_DISABLED"):
            WinnerEstimatePublicationService().publish(db, **invalid)
        db.commit()
        assert db.get(UploadRun, 953) is None

    for header, field, wrong in (
        ("generation", "root_manifest_hash", "0" * 64),
        ("generation", "generation_key", "WRONG_NATIVE_GENERATION_KEY"),
        ("previous_generation", "id", generation.id),
    ):
        forged = deepcopy(manifest)
        forged[header][field] = wrong
        forged["artifact_hash"] = transition_manifest_hash(forged)
        invalid = {**args, "manifest": forged, "reviewed_manifest_hash": forged["artifact_hash"]}
        db.add(UploadRun(id=957, filename="publication-manifest-attack.csv", status="COMPLETED"))
        with pytest.raises((ValueError, PublicationInvariantViolation)):
            WinnerEstimatePublicationService().publish(db, **invalid)
        db.commit()
        assert db.get(UploadRun, 957) is None
        assert original.lifecycle_status == "PUBLISHED"
        assert candidate.lifecycle_status == "CANDIDATE"

    candidate.model_version_id = db.scalar(select(WinnerModelVersion.id).limit(1))
    db.add(UploadRun(id=956, filename="publication-model-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
        WinnerEstimatePublicationService().publish(db, **args)
    db.commit()
    assert db.get(UploadRun, 956) is None and candidate.model_version_id is None

    candidate.lifecycle_status = "PUBLISHED"
    db.add(UploadRun(id=955, filename="direct-candidate-activation.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="SEMANTIC_TRANSACTION_REQUIRED"):
        WinnerEstimatePublicationService().publish(db, **args)
    db.commit()
    assert db.get(UploadRun, 955) is None and candidate.lifecycle_status == "CANDIDATE"

    def reject_after_switch(stage):
        if stage == "generation_switch":
            raise ValueError("T14C_EXPLICIT_PARTIAL_PUBLICATION_REJECTION")

    db.add(UploadRun(id=963, filename="partial-publication.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="EXPLICIT_PARTIAL_PUBLICATION_REJECTION"):
        WinnerEstimatePublicationService().publish(db, **args, stage_hook=reject_after_switch)
    db.commit()
    assert db.get(UploadRun, 963) is None
    assert original.lifecycle_status == "PUBLISHED" and candidate.lifecycle_status == "CANDIDATE"
    assert previous.status == "PUBLISHED" and generation.status == "READY"
    result = WinnerEstimatePublicationService().publish(db, **args)
    db.commit()
    assert result["published_candidates"] == 1
    assert serving_id_snapshot(db)["ids"] == tuple(after_ids)
    assert original.lifecycle_status == "SUPERSEDED" and candidate.lifecycle_status == "PUBLISHED"
    duplicate = WinnerEstimatePublicationService().publish(db, **args)
    db.commit()
    assert duplicate == result


def _native_shadow_governance(db, prediction, definition, config):
    """Retained pre-Phase-5 facts are explicit fixture inputs, never certified captures.

    The training/diagnostic/governance writers themselves are real and unpatched.
    This proves current-rules training from frozen history, not historical CI recovery.
    """
    from dataclasses import asdict
    from datetime import timedelta

    from app.models.tables import WinnerForwardOutcome, WinnerTargetStopOutcome
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.core_calculation_evidence import calculation_evidence_payload
    from app.services.domain_mutation import MutationEvidenceReference
    from app.services.market_clock_service import MarketClockService
    from app.services.winner_probability.calibration_service import CalibrationService
    from app.services.winner_probability.drift_service import DriftService
    from app.services.winner_probability.evidence_manifest_service import EvidenceManifestService
    from app.services.winner_probability.evidence_service import EvidenceOutcome
    from app.services.winner_probability.model_training import (
        ShadowModelTrainingService,
        ShadowTrainingExample,
    )
    from app.services.winner_probability.mutation_authority import (
        shadow_calibration_examples,
        validate_diagnostic_artifact,
    )
    from app.services.winner_probability.similarity_service import SimilarityService

    evidence = []
    start = datetime(2025, 6, 2, 20, tzinfo=UTC)
    for index in range(401):
        at = start + timedelta(hours=12 * index)
        won = index % 2 == 1
        source = WinnerPredictionSnapshot(
            run_id=7,
            ticker=f"HIST{index}",
            prediction_as_of_date=at.date(),
            source_data_cutoff_at=at,
            decision_at=at,
            captured_at=at,
            planned_entry_session=at.date(),
            entry_schedule_status="RESOLVED",
            entry_data_status="AVAILABLE",
            eligibility_status="ELIGIBLE",
            feature_schema_version=config.feature_schema.version,
            feature_vector_hash=f"explicit-historical-fixture-{index}",
            config_hash=config.config_hash,
            calculation_version=config.engine.calculation_version,
            feature_json={"technical_score": 100 if won else 0, "setup_family": "breakout"},
            source_ids_json={},
            warning_flags_json=[],
            lineage_json={"authority_classification": "LEGACY_NONCERTIFIED_TEST_FIXTURE"},
        )
        db.add(source)
        db.flush()
        forward = WinnerForwardOutcome(
            prediction_id=source.id,
            entry_model=definition.entry_model,
            horizon_sessions=definition.horizon_sessions,
            entry_session=at.date(),
            due_session=(at + timedelta(days=10)).date(),
            status="MATURED",
            revision=1,
            is_current_revision=True,
            close_return_pct=3 if won else -3,
            mfe_pct=4,
            mae_pct=-4,
            source_bar_lineage_hash=f"historical-{index}",
            source_revision_cutoff_at=at + timedelta(days=10),
            matured_at=at + timedelta(days=10),
            metadata_json={"authority_classification": "LEGACY_NONCERTIFIED_TEST_FIXTURE"},
        )
        db.add(forward)
        db.flush()
        target = WinnerTargetStopOutcome(
            prediction_id=source.id,
            outcome_definition_id=definition.id,
            forward_outcome_id=forward.id,
            entry_model=definition.entry_model,
            horizon_sessions=definition.horizon_sessions,
            status="MATURED",
            revision=1,
            is_current_revision=True,
            target_pct=definition.target_pct,
            stop_pct=definition.stop_pct,
            target_hit=won,
            stop_hit=not won,
            first_event="TARGET_FIRST" if won else "STOP_FIRST",
            primary_winner=won,
            optimistic_winner=won,
            conservative_winner=won,
            source_bar_lineage_hash=f"historical-{index}",
            evaluated_at=at + timedelta(days=10),
            metadata_json={"authority_classification": "LEGACY_NONCERTIFIED_TEST_FIXTURE"},
        )
        db.add(target)
        db.flush()
        evidence.append(EvidenceOutcome(source, forward, target))
    db.commit()
    evidence = tuple(evidence)
    population = EvidenceManifestService().create_or_get_manifest(db, evidence=evidence).manifest
    effective = resolve_winner_configuration(config, family="generation")
    clock = MarketClockService().cutoff_for(datetime.now(UTC), reason="NATIVE_SHADOW_TRAINING")
    trainer = ShadowModelTrainingService()
    examples = tuple(
        ShadowTrainingExample(
            row.prediction.feature_json,
            row.won,
            row.prediction.source_data_cutoff_at,
            row.prediction.episode_id,
            row.inclusion_weight,
        )
        for row in evidence
    )
    parameters = {"fold_count": 20, "regularization_strength": 0.01}
    report = trainer.train_shadow_report(
        examples,
        feature_names=("technical_score",),
        outcome_definition_id=definition.id,
        training_cutoff_at=clock.cutoff_at,
        config=config,
        **parameters,
    )
    contract = {
        "artifact": "TRAINING",
        "report": asdict(report),
        "training_cutoff_at": clock.cutoff_at,
        "training_parameters": parameters,
    }
    training = trainer.persist_training_run(
        db,
        report=report,
        outcome_definition_id=definition.id,
        training_cutoff_at=clock.cutoff_at,
        effective_configuration=effective,
        training_parameters=parameters,
        mutation_context=_diagnostic_context(
            definition,
            population,
            effective,
            clock,
            "ShadowModelTrainingService.persist_training_run",
            contract,
        ),
    )
    db.commit()
    validate_diagnostic_artifact(training, "fold_plan_json")
    args = {
        "model_key": "native-shadow",
        "algorithm": report.algorithm,
        "outcome_definition_id": definition.id,
        "entry_model": definition.entry_model,
        "training_cutoff_at": clock.cutoff_at,
        "artifact_hash": report.candidate_artifact_hash,
        "artifact_format": "linear_json",
        "artifact_schema_version": "winner-model-artifact-v1",
        "feature_schema_version": config.feature_schema.version,
        "calculation_version": config.engine.calculation_version,
        "config_hash": config.config_hash,
    }
    prototype = WinnerModelVersion(
        **args,
        hyperparameters_json={},
        metrics_json=report.metrics,
        preprocessing_json=report.preprocessing,
        calibration_json={"method": "none", "version": "1"},
        dependency_versions_json={},
    )
    creation = _model_context(prototype, config, "register_model", "create shadow")
    creation = replace(
        creation,
        evidence=creation.evidence
        + (
            MutationEvidenceReference(
                "training",
                training.__tablename__,
                training.id,
                Canonical.fingerprint(calculation_evidence_payload(training)),
            ),
        ),
    )
    registry = ModelRegistry()
    db.add(UploadRun(id=973, filename="training-pin-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="TRAINING_PIN_MISMATCH"):
        registry.register_model(
            db,
            **args,
            actor="native-test",
            reason="create shadow",
            config=config,
            artifact_payload=report.artifact_payload,
            training_run_id=training.id,
            metrics=report.metrics,
            preprocessing=report.preprocessing,
            calibration=prototype.calibration_json,
            mutation_context=replace(
                creation, evidence=tuple(pin for pin in creation.evidence if pin.role != "training")
            ),
        )
    db.commit()
    assert db.get(UploadRun, 973) is None
    model = registry.register_model(
        db,
        **args,
        actor="native-test",
        reason="create shadow",
        config=config,
        artifact_payload=report.artifact_payload,
        training_run_id=training.id,
        metrics=report.metrics,
        preprocessing=report.preprocessing,
        calibration=prototype.calibration_json,
        mutation_context=creation,
    )
    db.commit()
    clock = MarketClockService().cutoff_for(datetime.now(UTC), reason="NATIVE_SHADOW_DIAGNOSTICS")
    examples = shadow_calibration_examples(
        db,
        population,
        outcome_id=definition.id,
        model_id=model.id,
        as_of=clock.cutoff_at,
    )
    calibration = CalibrationService()
    calibration_report = calibration.calculate(examples)
    contract = {
        "artifact": "CALIBRATION",
        "report": asdict(calibration_report),
        "estimate_kind": "SHADOW_WALK_FORWARD",
        "segment": {},
        "estimate_ids": (),
    }
    bins = calibration.persist_bins(
        db,
        report=calibration_report,
        outcome_definition_id=definition.id,
        estimate_kind="SHADOW_WALK_FORWARD",
        model_version_id=model.id,
        effective_configuration=effective,
        mutation_context=_diagnostic_context(
            model,
            population,
            effective,
            clock,
            "CalibrationService.persist_bins",
            contract,
        ),
    )
    held_out = [row for fold in report.artifact_payload["fold_metrics"] for row in fold["held_out"]]
    # Exact, disjoint halves from the later walk-forward folds, after warm-up.
    indices = tuple(row["index"] for row in held_out if row["index"] > 100)
    baseline, recent = indices[:150], indices[150:]
    baseline_examples = shadow_calibration_examples(
        db,
        population,
        outcome_id=definition.id,
        model_id=model.id,
        as_of=clock.cutoff_at,
        indices=baseline,
    )
    recent_examples = shadow_calibration_examples(
        db,
        population,
        outcome_id=definition.id,
        model_id=model.id,
        as_of=clock.cutoff_at,
        indices=recent,
    )
    drift = DriftService()
    results = drift.calculate(
        baseline=baseline_examples,
        recent=recent_examples,
        comparison_window="native-shadow",
        config=config,
    )
    contract = {
        "artifact": "DRIFT",
        "results": [asdict(row) for row in results],
        "as_of_date": clock.latest_completed_session,
        "baseline_estimate_ids": (),
        "recent_estimate_ids": (),
        "baseline_held_out_indices": baseline,
        "recent_held_out_indices": recent,
        "estimate_kind": "SHADOW_WALK_FORWARD",
    }
    metrics = drift.persist_metrics(
        db,
        results=results,
        outcome_definition_id=definition.id,
        model_version_id=model.id,
        as_of_date=clock.latest_completed_session,
        estimate_kind="SHADOW_WALK_FORWARD",
        baseline_held_out_indices=baseline,
        recent_held_out_indices=recent,
        effective_configuration=effective,
        mutation_context=_diagnostic_context(
            model,
            population,
            effective,
            clock,
            "DriftService.persist_metrics",
            contract,
        ),
    )
    db.commit()
    promotion = _model_context(model, config, "promote_model", "promote shadow")
    promotion = replace(
        promotion,
        evidence=promotion.evidence
        + tuple(
            MutationEvidenceReference(
                role,
                row.__tablename__,
                row.id,
                Canonical.fingerprint(calculation_evidence_payload(row)),
            )
            for role, row in (("calibration", bins[0]), ("drift", metrics[0]))
        ),
    )
    c2 = replace(
        config,
        drift=replace(
            config.drift,
            thresholds={
                **config.drift.thresholds,
                "ece_delta": 0.08,
            },
        ),
    )
    c2_context = replace(
        promotion,
        configuration=resolve_winner_configuration(c2, family="generation").snapshot.identity,
    )
    db.add(UploadRun(id=972, filename="diagnostic-config-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="DIAGNOSTIC_CONFIGURATION_MISMATCH"):
        registry.promote_model(
            db,
            model_id=model.id,
            actor="native-test",
            reason="promote shadow",
            config=c2,
            mutation_context=c2_context,
        )
    db.commit()
    assert db.get(UploadRun, 972) is None
    # A nested JSON alteration is not tracked by SQLAlchemy; read actual retained
    # columns before consuming a proof rather than trusting the identity map.
    from app.services.winner_probability.mutation_authority import diagnostic_artifact_body

    metrics[0].segment_json["native_report"][1]["breached"] = True
    metrics[0].segment_json["native_diagnostic_proof"]["body_fingerprint"] = Canonical.fingerprint(
        diagnostic_artifact_body(metrics[0], "segment_json")
    )
    forged = replace(
        promotion,
        evidence=tuple(
            MutationEvidenceReference(
                pin.role,
                pin.table,
                pin.artifact_id,
                Canonical.fingerprint(calculation_evidence_payload(metrics[0])),
            )
            if pin.role == "drift"
            else pin
            for pin in promotion.evidence
        ),
    )
    db.add(UploadRun(id=971, filename="diagnostic-body-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="DIAGNOSTIC_RETAINED_BODY_MISMATCH"):
        registry.promote_model(
            db,
            model_id=model.id,
            actor="native-test",
            reason="promote shadow",
            config=config,
            mutation_context=forged,
        )
    db.commit()
    assert db.get(UploadRun, 971) is None
    registry.promote_model(
        db,
        model_id=model.id,
        actor="native-test",
        reason="promote shadow",
        config=config,
        mutation_context=promotion,
    )
    db.commit()
    assert model.status == "ACTIVE"
    historical_estimate = db.scalar(
        select(WinnerProbabilityEstimate).where(
            WinnerProbabilityEstimate.prediction_id == prediction.id,
            WinnerProbabilityEstimate.estimate_kind == "DECISION_TIME",
        )
    )
    assert historical_estimate.model_version_id is None
    similarity = SimilarityService()
    clock = MarketClockService().cutoff_for(datetime.now(UTC), reason="NATIVE_SIMILARITY")
    neighbors = similarity.rank_neighbors(
        prediction=prediction,
        evidence=evidence,
        feature_names=("setup_family",),
        as_of=clock.cutoff_at,
        config=config,
    )
    assert len(neighbors) == 10
    contract = {
        "artifact": "SIMILARITY",
        "neighbors": [asdict(row) for row in neighbors],
        "source_cutoff_at": clock.cutoff_at,
        "cache_version": "similarity-v1",
        "feature_names": ("setup_family",),
        "feature_weights": None,
        "limit": 10,
        "one_per_episode": True,
    }
    links = similarity.persist_neighbors(
        db,
        prediction=prediction,
        outcome_definition=definition,
        neighbors=neighbors,
        source_cutoff_at=clock.cutoff_at,
        cache_version="similarity-v1",
        feature_names=("setup_family",),
        effective_configuration=effective,
        mutation_context=_diagnostic_context(
            prediction,
            population,
            effective,
            clock,
            "SimilarityService.persist_neighbors",
            contract,
        ),
    )
    db.commit()
    assert len(links) == 10
    validate_diagnostic_artifact(links[0], "contribution_json")
    # Removing a proof from untracked JSON must not permit deleting retained evidence.
    retirement = _model_context(model, config, "retire_model", "retire")
    links[0].contribution_json.pop("native_diagnostic_proof")
    db.delete(links[0])
    db.add(UploadRun(id=970, filename="diagnostic-delete-attack.csv", status="COMPLETED"))
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
        registry.retire_model(
            db,
            model_id=model.id,
            actor="native-test",
            reason="retire",
            config=config,
            mutation_context=retirement,
        )
    db.commit()
    assert db.get(UploadRun, 970) is None
    validate_diagnostic_artifact(links[0], "contribution_json")


def test_native_outcome_seal_matches_postgresql_numeric_rounding(contextual_engine):
    from decimal import Decimal

    from native_winner_support import native_pending_prediction

    from app.models.tables import WinnerTargetStopOutcome
    from app.services.bar_cache_service import cache_bars
    from app.services.ib_data_fetcher import HistoricalBar
    from app.services.winner_probability.market_data_obligation_service import (
        MarketDataObligationService,
        required_outcome_sessions,
    )
    from app.services.winner_probability.outcome_authority import validate_outcome_body
    from app.services.winner_probability.outcome_orchestration_service import (
        H5NextOpenOrchestrationService,
    )

    with Session(contextual_engine) as db:
        prediction, forward = native_pending_prediction(db)
        # The fifth close is 74.2934325: PostgreSQL retains 74.293433 at
        # NUMERIC scale six. Exercise the real cache, obligation and H5 writer.
        for ticker, base in (("ACME", 69.71), ("SPY", 504.2513), ("XLK", 257.35217)):
            cache_bars(
                db,
                [
                    HistoricalBar(
                        ticker=ticker,
                        bar_date=day,
                        timeframe="1 day",
                        what_to_show="ADJUSTED_LAST",
                        adjustment_type=None,
                        source="IBKR",
                        open=base * (1 + 0.01 * (index + 1)),
                        close=base * (1 + 0.01 * (index + 1)) * 1.015,
                        high=base * (1 + 0.01 * (index + 1)) * 1.04,
                        low=base * (1 + 0.01 * (index + 1)) * 0.995,
                        volume=100000,
                    )
                    for index, day in enumerate(required_outcome_sessions(forward.entry_session, 5))
                ],
            )
        db.commit()
        now = datetime(2027, 1, 15, 22, tzinfo=UTC)
        MarketDataObligationService().evaluate(db, now=now)
        db.commit()
        result = H5NextOpenOrchestrationService().drain_due(db, now=now)
        assert result.matured_h5 == 1, result
        db.commit()
        db.refresh(forward)
        assert forward.exit_price == Decimal("74.293433")
        validate_outcome_body(forward)
        for target in db.scalars(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.forward_outcome_id == forward.id
            )
        ):
            db.refresh(target)
            validate_outcome_body(target)
