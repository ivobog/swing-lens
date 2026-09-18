"""Winner artifact-specific authority; no Setup/CERI/Lifecycle dependency."""

from sqlalchemy import select

from app.models.tables import (
    CoreCalculationEvidence,
    RawCompanyRow,
    TransitionDecisionHandoffManifest,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_mutation_authority import (
    _validate_evidence,
    core_mutation_context,
    identity_cutoff,
    mutation_eligibility,
    validate_core_mutation_authority,
    validate_financial_source_context,
    validate_source_address,
    validate_source_values,
)
from app.services.decision_effective_configuration import resolve_winner_configuration
from app.services.domain_mutation import MutationDomain


def diagnostic_artifact_body(row, container):
    from decimal import Decimal

    from sqlalchemy import Numeric

    body = {}
    for column in row.__table__.columns:
        if column.key in {"id", "calculated_at", "created_at", "started_at", "completed_at"}:
            continue
        value = getattr(row, column.key)
        if column.key == container:
            value = {
                key: item for key, item in (value or {}).items() if key != "native_diagnostic_proof"
            }
        if value is not None and isinstance(column.type, Numeric):
            value = Decimal(str(value)).quantize(Decimal(1).scaleb(-column.type.scale))
        body[column.key] = value
    return body


def seal_diagnostic_artifact(row, container, *, artifact_payload=None):
    if row.id is not None:
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_CREATION_ONLY")
    value = getattr(row, container) or {}
    proof = {
        "contract": "winner-native-diagnostic-v1",
        "body_fingerprint": Canonical.fingerprint(diagnostic_artifact_body(row, container)),
    }
    if artifact_payload is not None:
        proof["artifact_payload"] = artifact_payload
    setattr(row, container, {**value, "native_diagnostic_proof": proof})


def validate_diagnostic_artifact(row, container):
    from sqlalchemy.orm import object_session

    from app.services.core_calculation_evidence import calculation_evidence_payload

    db = object_session(row)
    if db is None or row.id is None:
        raise ValueError("MUTATION_WINNER_RETAINED_DIAGNOSTIC_REQUIRED")
    with db.no_autoflush:
        retained = (
            db.execute(
                select(*row.__table__.columns).where(row.__table__.c.id == row.id).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
    if retained is None or Canonical.fingerprint(
        {
            key: value
            for key, value in retained.items()
            if key not in {"id", "evidence_id", "created_at", "updated_at"}
        }
    ) != Canonical.fingerprint(calculation_evidence_payload(row)):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_RETAINED_BODY_MISMATCH")
    proof = (getattr(row, container) or {}).get("native_diagnostic_proof")
    if not isinstance(proof, dict) or proof.get("contract") != "winner-native-diagnostic-v1":
        raise ValueError("MUTATION_WINNER_CERTIFIED_DIAGNOSTIC_REQUIRED")
    if proof.get("body_fingerprint") != Canonical.fingerprint(
        diagnostic_artifact_body(row, container)
    ):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_BODY_MISMATCH")
    return proof


def prediction_capture_authority(
    db, *, acquisition, features, identity, config, decision_at, mutation_context
):
    from app.services.winner_probability.calculation_identity import (
        _ticker_lineage,
        _validate_raw,
        validate_winner_handoff,
    )
    from app.services.winner_probability.feature_extractor import (
        WinnerFeatureExtractor,
        _feature_semantic_hash,
    )

    run = acquisition.run_context
    ticker = acquisition.ticker_context
    cutoff = identity_cutoff(db, identity)
    if decision_at is None or decision_at.tzinfo is None or decision_at < cutoff.cutoff_at:
        raise ValueError("MUTATION_WINNER_EXPLICIT_DECISION_TIME_REQUIRED")
    handoff = run.decision_handoff_manifest
    retained = db.get(TransitionDecisionHandoffManifest, handoff.id)
    if retained is None or retained.manifest_fingerprint != handoff.manifest_fingerprint:
        raise ValueError("MUTATION_WINNER_RETAINED_HANDOFF_MISSING")
    validate_winner_handoff(retained, run_id=identity.ownership.run_id.value, market_cutoff=cutoff)
    raw = db.scalar(select(RawCompanyRow).where(RawCompanyRow.id == ticker.raw_row.id))
    if (
        raw is None
        or raw.run_id != identity.ownership.run_id.value
        or raw.ticker != features.ticker
        or Canonical.fingerprint(raw.raw_json) != Canonical.fingerprint(ticker.raw_row.raw_json)
    ):
        raise ValueError("MUTATION_WINNER_RAW_SOURCE_MISMATCH")
    _validate_raw(
        raw,
        expected_artifact=_ticker_lineage(retained, raw.ticker).get("raw_row"),
        run_id=raw.run_id,
        ticker=raw.ticker,
    )
    sources = {
        "technical": ticker.technical_score,
        "combined": ticker.combined_result,
        "ranking": ticker.ranking_results[0] if ticker.ranking_results else None,
        "fundamental": ticker.fundamental_score,
        "regime": run.market_regime_snapshot,
        "sector": run.sector_rotation_snapshot,
    }
    sources = {role: source for role, source in sources.items() if source is not None}
    permissions = []
    frozen = acquisition.consumer_eligibility.canonical_payload()
    for role, source in sources.items():
        evidence = db.get(CoreCalculationEvidence, source.evidence_id)
        _validate_evidence(evidence)
        if evidence.artifact_kind != role.upper():
            raise ValueError("MUTATION_WINNER_SOURCE_KIND_MISMATCH: " + role)
        validate_source_address(identity, role, source, evidence)
        validate_source_values(source, evidence, db=db)
        if role in {"fundamental", "technical", "combined", "ranking"}:
            validate_financial_source_context(identity, evidence)
        permissions.append(mutation_eligibility(role, evidence, frozen[role]))
    manifests = {
        "raw_source": {
            "id": raw.id,
            "run_id": raw.run_id,
            "ticker": raw.ticker,
            "raw_json": raw.raw_json,
            "handoff_id": retained.id,
            "handoff_fingerprint": retained.manifest_fingerprint,
        }
    }
    configuration = resolve_winner_configuration(config)
    configuration.require_family("decision.winner.prediction")
    if not configuration.winner_config().engine.enabled:
        raise ValueError("MUTATION_WINNER_PREDICTION_DISABLED")
    expected = core_mutation_context(
        db,
        domain=MutationDomain.WINNER_PREDICTION,
        identity=identity,
        configuration=configuration.snapshot,
        sources=sources,
        manifests=manifests,
        eligibility=tuple(permissions),
        entrypoint="WinnerPredictionCaptureService._capture_ticker",
        writer="_capture_ticker",
    )
    validate_core_mutation_authority(
        db,
        mutation_context if mutation_context is not None else expected,
        domain=MutationDomain.WINNER_PREDICTION,
        identity=identity,
        configuration=configuration.snapshot,
        sources=sources,
        manifests=manifests,
        writer="_capture_ticker",
    )
    if features.prediction_as_of_date != cutoff.latest_completed_session:
        raise ValueError("MUTATION_WINNER_FEATURE_SESSION_MISMATCH")
    extractor = WinnerFeatureExtractor()
    native = extractor.finalize_decision_timing(
        extractor.extract(run, ticker, config, decision_at=decision_at), decision_at=decision_at
    )
    if _feature_semantic_hash(native.feature_json) != _feature_semantic_hash(features.feature_json):
        raise ValueError("MUTATION_WINNER_FROZEN_VECTOR_SOURCE_MISMATCH")
    return expected


def validate_diagnostic_authority(
    db,
    context,
    *,
    writer,
    subject_id,
    outcome_id,
    contract,
    configuration,
    subject_type=None,
):
    """Resolve retained diagnostic subject and population, never a caller report alone."""
    from app.models.tables import (
        WinnerEvidenceManifest,
        WinnerModelVersion,
        WinnerOutcomeDefinition,
        WinnerPredictionSnapshot,
    )
    from app.services.domain_mutation import (
        DomainMutationContext,
        MutationWriterDescriptor,
        fence_mutation_transaction,
    )
    from app.services.domain_write_fence import current_domain_write_ownership
    from app.services.winner_probability.evidence_manifest_service import _hash_payload

    if not isinstance(context, DomainMutationContext):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_CONTEXT_REQUIRED")
    if context.temporal is None:
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_TEMPORAL_AUTHORITY_REQUIRED")
    domain = MutationDomain.WINNER_DIAGNOSTICS
    if context.domain is not domain or context.writer != MutationWriterDescriptor(
        writer, "phase5-winner-diagnostic-v1", domain
    ):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_WRITER_MISMATCH")
    ownership = current_domain_write_ownership()
    if ownership is not None and context.execution != ownership:
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_OWNERSHIP_MISMATCH")
    fence_mutation_transaction(db, context)
    from app.services.core_mutation_authority import _validate_configuration

    if configuration is None or context.configuration != configuration.snapshot.identity:
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_CONFIGURATION_REQUIRED")
    configuration.require_family("decision.winner.generation")
    _validate_configuration(db, context, configuration.snapshot)
    pins = {pin.role: pin for pin in context.evidence}
    subject_pin = pins["diagnostic_subject"]
    model_type = subject_type or (
        WinnerModelVersion if subject_id is not None else WinnerOutcomeDefinition
    )
    if model_type not in {WinnerModelVersion, WinnerOutcomeDefinition, WinnerPredictionSnapshot}:
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_SUBJECT_TYPE_REJECTED")
    expected_id = subject_id if subject_id is not None else outcome_id
    subject = db.get(model_type, expected_id)
    if (
        subject is None
        or subject_pin.table != model_type.__tablename__
        or (subject_pin.artifact_id != expected_id)
    ):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_SUBJECT_MISMATCH")
    from app.services.core_calculation_evidence import calculation_evidence_payload

    payload = calculation_evidence_payload(
        subject,
        excluded_columns={"status", "activated_at", "retired_at"}
        if model_type is WinnerModelVersion
        else set(),
    )
    if subject_pin.fingerprint != Canonical.fingerprint(payload):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_SUBJECT_FINGERPRINT_MISMATCH")
    if model_type is WinnerModelVersion and subject.outcome_definition_id != outcome_id:
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_OUTCOME_MISMATCH")
    population_pin = pins["input_manifest"]
    population = db.get(WinnerEvidenceManifest, population_pin.artifact_id)
    if (
        population is None
        or population_pin.table != WinnerEvidenceManifest.__tablename__
        or (
            population.manifest_hash != population_pin.fingerprint
            or _hash_payload(population.payload_json) != population.manifest_hash
            or population.member_count != len(population.payload_json.get("members", []))
        )
    ):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_POPULATION_MISMATCH")
    expected_contract = Canonical.fingerprint(contract)
    contract_pin = pins["diagnostic_contract"]
    if (
        contract_pin.table != "native_manifest"
        or contract_pin.artifact_id != expected_contract
        or (contract_pin.fingerprint != expected_contract)
    ):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_CONTRACT_MISMATCH")
    return population


def diagnostic_population(db, population, outcome_id, *, as_of):
    """Rehydrate the exact prediction/outcome revisions retained by the manifest."""
    from decimal import Decimal

    from app.models.tables import (
        OutcomeStatus,
        WinnerForwardOutcome,
        WinnerPredictionSnapshot,
        WinnerTargetStopOutcome,
    )
    from app.services.winner_probability.evidence_service import EvidenceOutcome

    rows = []
    seen = set()
    for member in population.payload_json["members"]:
        prediction = db.get(WinnerPredictionSnapshot, member["prediction_id"])
        forward = db.get(WinnerForwardOutcome, member["outcome_id"])
        target = db.get(WinnerTargetStopOutcome, member["target_stop_outcome_id"])
        if (
            prediction is None
            or forward is None
            or target is None
            or (
                forward.prediction_id != prediction.id
                or target.prediction_id != prediction.id
                or forward.revision != member["outcome_revision"]
                or target.revision != member["target_stop_revision"]
                or target.outcome_definition_id != outcome_id
                or forward.status != OutcomeStatus.MATURED
                or target.status != OutcomeStatus.MATURED
                or target.primary_winner != member["primary_winner"]
                or member.get("outcome_replay_id") is not None
                or prediction.episode_id != member.get("episode_id")
                or target.forward_outcome_id != forward.id
                or prediction.source_data_cutoff_at >= as_of
                or forward.matured_at is None
                or forward.matured_at >= as_of
                or target.evaluated_at is None
                or target.evaluated_at >= as_of
            )
        ):
            raise ValueError("MUTATION_WINNER_DIAGNOSTIC_MEMBER_MISMATCH")
        key = (prediction.id, forward.id, target.id)
        if key in seen:
            raise ValueError("MUTATION_WINNER_DIAGNOSTIC_DUPLICATE_MEMBER")
        seen.add(key)
        rows.append(
            EvidenceOutcome(
                prediction=prediction,
                forward_outcome=forward,
                target_stop_outcome=target,
                inclusion_weight=Decimal(member["inclusion_weight"]),
            )
        )
    return tuple(rows)


def calibration_examples(
    db, population, *, outcome_id, model_id, estimate_kind, estimate_ids, as_of
):
    from app.models.tables import WinnerProbabilityEstimate
    from app.services.winner_probability.calibration_service import CalibrationExample

    if estimate_kind == "SHADOW_WALK_FORWARD":
        if estimate_ids:
            raise ValueError("MUTATION_WINNER_SHADOW_IS_NOT_SERVING_ESTIMATE")
        return shadow_calibration_examples(
            db, population, outcome_id=outcome_id, model_id=model_id, as_of=as_of
        )

    evidence = diagnostic_population(db, population, outcome_id, as_of=as_of)
    if len(estimate_ids) != len(set(estimate_ids)) or len(estimate_ids) != len(evidence):
        raise ValueError("MUTATION_WINNER_DIAGNOSTIC_EXACT_ESTIMATES_REQUIRED")
    estimates = {p.prediction.id: None for p in evidence}
    for id in estimate_ids:
        estimate = db.get(WinnerProbabilityEstimate, id)
        if (
            estimate is None
            or estimate.prediction_id not in estimates
            or (
                estimates[estimate.prediction_id] is not None
                or estimate.model_version_id != model_id
                or estimate.estimate_kind != estimate_kind
                or estimate.outcome_definition_id != outcome_id
                or estimate.point_probability is None
            )
        ):
            raise ValueError("MUTATION_WINNER_DIAGNOSTIC_ESTIMATE_MISMATCH")
        from app.services.decision_effective_configuration import configuration_from_payload
        from app.services.winner_probability.estimate_authority import validate_estimate

        retained = (estimate.metadata_json or {}).get("effective_configuration_at_creation")
        if retained is None:
            raise ValueError("MUTATION_WINNER_CERTIFIED_ESTIMATE_CONFIGURATION_REQUIRED")
        validate_estimate(estimate, configuration_from_payload(retained).winner_config())
        estimates[estimate.prediction_id] = estimate
    return tuple(
        CalibrationExample(
            probability=estimates[p.prediction.id].point_probability,
            observed=p.won,
            weight=p.inclusion_weight,
        )
        for p in evidence
    )


def diagnostic_input_bodies(evidence):
    from app.services.core_calculation_evidence import calculation_evidence_payload

    return [
        {
            "prediction_id": row.prediction.id,
            "prediction": Canonical.fingerprint(
                calculation_evidence_payload(
                    row.prediction,
                    excluded_columns={"entry_data_status", "superseded_at", "combined_result_id"},
                )
            ),
            "outcome": Canonical.fingerprint(
                calculation_evidence_payload(
                    row.forward_outcome, excluded_columns={"is_current_revision", "superseded_at"}
                )
            ),
            "target": Canonical.fingerprint(
                calculation_evidence_payload(
                    row.target_stop_outcome,
                    excluded_columns={"is_current_revision", "superseded_at"},
                )
            ),
        }
        for row in evidence
    ]


def shadow_calibration_examples(db, population, *, outcome_id, model_id, as_of, indices=None):
    """Governance-only held-out predictions; never persisted serving estimates."""
    from decimal import Decimal

    from app.models.tables import WinnerModelTrainingRun, WinnerModelVersion
    from app.services.winner_probability.calibration_service import CalibrationExample
    from app.services.winner_probability.model_authority import validate_model_source

    model = db.get(WinnerModelVersion, model_id)
    if model is None or model.outcome_definition_id != outcome_id:
        raise ValueError("MUTATION_WINNER_SHADOW_MODEL_REQUIRED")
    creation = validate_model_source(db, model)
    training = db.get(WinnerModelTrainingRun, creation.get("training_run_id"))
    if training is None or training.completed_at > as_of:
        raise ValueError("MUTATION_WINNER_SHADOW_TRAINING_NOT_KNOWN")
    proof = validate_diagnostic_artifact(training, "fold_plan_json")
    source_pins = training.fold_plan_json["mutation_authority"]["evidence"]
    source = next(pin for pin in source_pins if pin["role"] == "input_manifest")
    if source["id"] != population.id or source["fingerprint"] != population.manifest_hash:
        raise ValueError("MUTATION_WINNER_SHADOW_TRAINING_POPULATION_MISMATCH")
    evidence = diagnostic_population(db, population, outcome_id, as_of=training.training_cutoff_at)
    if diagnostic_input_bodies(evidence) != training.fold_plan_json["input_bodies"]:
        raise ValueError("MUTATION_WINNER_SHADOW_FROZEN_INPUT_BODY_MISMATCH")
    held_out = [
        row for fold in proof["artifact_payload"]["fold_metrics"] for row in fold["held_out"]
    ]
    if indices is not None:
        if len(indices) != len(set(indices)) or not set(indices).issubset(
            {row["index"] for row in held_out}
        ):
            raise ValueError("MUTATION_WINNER_SHADOW_HELD_OUT_SCOPE_MISMATCH")
        held_out = [row for row in held_out if row["index"] in indices]
    return tuple(
        CalibrationExample(
            probability=Decimal(str(row["probability"])),
            observed=row["observed"],
            weight=Decimal(row["weight"]),
        )
        for row in held_out
    )
