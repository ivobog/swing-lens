"""Model governance names retained models and diagnostic gate rows explicitly."""

from sqlalchemy import select

from app.models.tables import (
    LifecycleEventType,
    WinnerCalibrationBin,
    WinnerDriftMetric,
    WinnerModelLifecycleEvent,
    WinnerModelVersion,
    WinnerOutcomeDefinition,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_calculation_evidence import calculation_evidence_payload
from app.services.core_mutation_authority import _validate_configuration
from app.services.decision_effective_configuration import (
    configuration_from_payload,
    resolve_winner_configuration,
)
from app.services.decision_mutation_authority import lock_decision_scope
from app.services.domain_mutation import (
    DomainMutationContext,
    MutationDomain,
    MutationEvidenceReference,
    MutationWriterDescriptor,
    fence_mutation_transaction,
)
from app.services.domain_write_fence import current_domain_write_ownership


def model_body(model):
    return calculation_evidence_payload(
        model, excluded_columns={"id", "status", "activated_at", "retired_at", "created_at"}
    )


def baseline_artifact(config, outcome_definition_id):
    return {
        "algorithm": "cohort",
        "outcome_definition_id": outcome_definition_id,
        "calculation_version": config.engine.calculation_version,
        "feature_schema_version": config.feature_schema.version,
        "cohort_configuration": resolve_winner_configuration(
            config, family="cohort"
        ).snapshot.semantic_hash,
    }


def validate_model_source(db, model):
    events = list(
        db.scalars(
            select(WinnerModelLifecycleEvent).where(
                WinnerModelLifecycleEvent.model_version_id == model.id,
                WinnerModelLifecycleEvent.event_type == LifecycleEventType.CREATED,
            )
        )
    )
    if len(events) != 1:
        raise ValueError("MUTATION_WINNER_CERTIFIED_MODEL_CREATION_REQUIRED")
    with db.no_autoflush:
        retained = db.scalar(
            select(WinnerModelLifecycleEvent.metadata_json)
            .where(WinnerModelLifecycleEvent.id == events[0].id)
            .with_for_update()
        )
    if Canonical.fingerprint(retained) != Canonical.fingerprint(events[0].metadata_json):
        raise ValueError("MUTATION_WINNER_MODEL_RETAINED_CREATION_MISMATCH")
    proof = (retained or {}).get("native_model_proof")
    if (
        not isinstance(proof, dict)
        or proof.get("contract") != "winner-model-creation-v1"
        or proof.get("body_fingerprint") != Canonical.fingerprint(model_body(model))
    ):
        raise ValueError("MUTATION_WINNER_MODEL_CREATION_BODY_MISMATCH")
    configuration_from_payload(proof["configuration"]).require_family("decision.winner.generation")
    return proof


def governance_authority(
    db,
    context,
    *,
    model,
    action,
    actor,
    reason,
    config,
    replacement_id=None,
    allow_without_fallback=False,
):
    if not isinstance(context, DomainMutationContext) or context.temporal is None:
        raise ValueError("MUTATION_WINNER_MODEL_CONTEXT_AND_TIME_REQUIRED")
    if config is None:
        raise ValueError("MUTATION_WINNER_MODEL_CONFIGURATION_REQUIRED")
    effective = resolve_winner_configuration(config, family="generation")
    effective.require_family("decision.winner.generation")
    if (
        context.domain is not MutationDomain.WINNER_MODEL
        or context.writer
        != MutationWriterDescriptor(
            "ModelRegistry." + action, "phase5-winner-model-v1", MutationDomain.WINNER_MODEL
        )
        or context.configuration != effective.snapshot.identity
    ):
        raise ValueError("MUTATION_WINNER_MODEL_WRITER_CONFIGURATION_MISMATCH")
    ownership = current_domain_write_ownership()
    if ownership is not None and context.execution != ownership:
        raise ValueError("MUTATION_WINNER_MODEL_OWNERSHIP_MISMATCH")
    if model.training_cutoff_at > context.temporal.cutoff_at:
        raise ValueError("MUTATION_WINNER_MODEL_TRAINING_AFTER_OPERATION")
    if model.id is not None and model.created_at > context.temporal.cutoff_at:
        raise ValueError("MUTATION_WINNER_MODEL_NOT_KNOWN_AT_OPERATION")
    action_body = {
        "action": action,
        "actor": actor,
        "reason": reason,
        "model_key": model.model_key,
        "model_id": model.id,
        "replacement_id": replacement_id,
        "allow_without_active_fallback": allow_without_fallback,
    }
    target = model_body(model)
    expected = {
        "model_version": MutationEvidenceReference(
            "model_version",
            "native_manifest",
            Canonical.fingerprint(target),
            Canonical.fingerprint(target),
        ),
        "governance_action": MutationEvidenceReference(
            "governance_action",
            "native_manifest",
            Canonical.fingerprint(action_body),
            Canonical.fingerprint(action_body),
        ),
    }
    pins = {pin.role: pin for pin in context.evidence}
    if any(pins.get(role) != pin for role, pin in expected.items()):
        raise ValueError("MUTATION_WINNER_MODEL_TARGET_ACTION_MISMATCH")
    fence_mutation_transaction(db, context)
    _validate_configuration(db, context, effective.snapshot)
    lock_decision_scope(db, {"winner_model_governance": model.outcome_definition_id})
    if action != "register_model":
        validate_model_source(db, model)
    if action == "promote_model":
        active = list(
            db.scalars(
                select(WinnerModelVersion)
                .where(
                    WinnerModelVersion.outcome_definition_id == model.outcome_definition_id,
                    WinnerModelVersion.status == "ACTIVE",
                )
                .order_by(WinnerModelVersion.id)
            )
        )
        if active:
            for predecessor in active:
                validate_model_source(db, predecessor)
            body = [{"id": row.id, "body": model_body(row), "status": row.status} for row in active]
            fingerprint = Canonical.fingerprint(body)
            expected_predecessor = MutationEvidenceReference(
                "active_predecessor",
                "native_manifest",
                fingerprint,
                fingerprint,
            )
            if pins.get("active_predecessor") != expected_predecessor:
                raise ValueError("MUTATION_WINNER_MODEL_ACTIVE_PREDECESSOR_MISMATCH")
    contract_config = config
    if action == "retire_model":
        contract_config = configuration_from_payload(
            validate_model_source(db, model)["configuration"]
        ).winner_config()
    definition = db.get(WinnerOutcomeDefinition, model.outcome_definition_id)
    native = next(
        (
            row
            for row in contract_config.outcome_definitions
            if definition is not None and row.id == definition.definition_id
        ),
        None,
    )
    if definition is None or native is None:
        raise ValueError("MUTATION_WINNER_MODEL_OUTCOME_CONTRACT_REQUIRED")
    from decimal import Decimal

    expected = {
        "entry_model": native.entry_model,
        "horizon_sessions": native.horizon_sessions,
        "target_pct": Decimal(str(native.target_pct)),
        "stop_pct": Decimal(str(native.stop_pct)),
        "same_bar_conflict_policy": native.same_bar_conflict_policy,
        "calculation_version": contract_config.engine.calculation_version,
    }
    if model.entry_model != native.entry_model or any(
        getattr(definition, key) != value for key, value in expected.items()
    ):
        raise ValueError("MUTATION_WINNER_MODEL_OUTCOME_CONTRACT_MISMATCH")
    for role, kind in (("calibration", WinnerCalibrationBin), ("drift", WinnerDriftMetric)):
        pin = pins.get(role)
        if pin is None:
            continue
        row = db.get(kind, pin.artifact_id)
        if (
            row is None
            or pin.table != kind.__tablename__
            or row.model_version_id != model.id
            or row.outcome_definition_id != model.outcome_definition_id
            or row.calculated_at > context.temporal.cutoff_at
            or pin.fingerprint != Canonical.fingerprint(calculation_evidence_payload(row))
        ):
            raise ValueError("MUTATION_WINNER_MODEL_DIAGNOSTIC_SCOPE_MISMATCH")
        from app.services.winner_probability.mutation_authority import validate_diagnostic_artifact

        validate_diagnostic_artifact(row, "segment_json")
        if row.segment_json["mutation_authority"]["configuration"] != (
            effective.snapshot.identity.as_dict()
        ):
            raise ValueError("MUTATION_WINNER_MODEL_DIAGNOSTIC_CONFIGURATION_MISMATCH")
    if action == "promote_model":
        if not effective.winner_config().engine.enabled:
            raise ValueError("MUTATION_WINNER_MODEL_PROMOTION_DISABLED")
        gates = effective.winner_config().model_governance.promotion_gates
        if gates["require_calibration_bins"] and "calibration" not in pins:
            raise ValueError("MUTATION_WINNER_MODEL_CALIBRATION_PIN_REQUIRED")
        if gates["require_fresh_drift_metrics"] and "drift" not in pins:
            raise ValueError("MUTATION_WINNER_MODEL_DRIFT_PIN_REQUIRED")
    return pins
