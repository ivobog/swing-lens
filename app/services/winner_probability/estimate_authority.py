"""Explicit prediction, contract, configuration and time at estimate writers."""

from dataclasses import replace
from decimal import Decimal

from sqlalchemy import Numeric

from app.models.tables import (
    WinnerEvidenceManifest,
    WinnerOutcomeDefinition,
    WinnerProbabilityEstimate,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.contextual_calculation_identity import (
    build_contextual_result_identity,
    consumer_context_identity,
)
from app.services.core_mutation_authority import (
    core_mutation_context,
    core_writer_member,
    validate_core_mutation_authority,
)
from app.services.decision_effective_configuration import (
    configuration_from_payload,
    resolve_winner_configuration,
)
from app.services.decision_mutation_authority import lock_decision_scope
from app.services.domain_mutation import MutationDomain
from app.services.market_clock_service import MarketClockService
from app.services.winner_probability.prediction_authority import validate_prediction_source

PROOF_KEY = "native_estimate_proof"
PROJECTION_COLUMNS = {
    "id",
    "created_at",
    "metadata_json",
    "lifecycle_status",
    "published_at",
    "superseded_at",
    "supersedes_estimate_id",
}


def estimate_request_authority(
    db,
    *,
    prediction,
    outcome_definition,
    config,
    cutoff_at,
    estimate_kind,
    model_version_id=None,
    generation=None,
    mutation_context=None,
):
    identity = validate_prediction_source(db, prediction)
    if (
        cutoff_at is None
        or cutoff_at.tzinfo is None
        or cutoff_at < prediction.source_data_cutoff_at
    ):
        raise ValueError("MUTATION_WINNER_ESTIMATE_EXPLICIT_CUTOFF_REQUIRED")
    effective = resolve_winner_configuration(config, family="cohort")
    effective.require_family("decision.winner.cohort")
    config = effective.winner_config()
    if not config.engine.enabled:
        raise ValueError("MUTATION_WINNER_ESTIMATE_DISABLED")
    if estimate_kind in {"DECISION_TIME", "AS_OF_REPLAY"}:
        if cutoff_at != prediction.source_data_cutoff_at:
            raise ValueError("MUTATION_WINNER_ESTIMATE_ORIGINAL_CUTOFF_MISMATCH")
        retained = (prediction.lineage_json or {}).get("estimate_effective_configuration")
        if (
            retained is None
            or configuration_from_payload(retained).snapshot.semantic_hash
            != effective.snapshot.semantic_hash
        ):
            raise ValueError("MUTATION_WINNER_ESTIMATE_CAPTURE_CONFIGURATION_MISMATCH")
    retained_outcome = configuration_from_payload(
        prediction.lineage_json["outcome_effective_configuration"]
    ).winner_config()
    definition = db.get(WinnerOutcomeDefinition, outcome_definition.id)
    raw = next(
        (
            raw
            for raw in retained_outcome.outcome_definitions
            if raw.id == outcome_definition.definition_id
        ),
        None,
    )
    expected = (
        {}
        if raw is None
        else {
            "entry_model": raw.entry_model,
            "horizon_sessions": raw.horizon_sessions,
            "target_pct": Decimal(str(raw.target_pct)) if raw.target_pct is not None else None,
            "stop_pct": Decimal(str(raw.stop_pct)) if raw.stop_pct is not None else None,
            "same_bar_conflict_policy": raw.same_bar_conflict_policy,
            "calculation_version": retained_outcome.engine.calculation_version,
        }
    )
    if (
        definition is None
        or raw is None
        or any(getattr(definition, key) != value for key, value in expected.items())
    ):
        raise ValueError("MUTATION_WINNER_ESTIMATE_OUTCOME_CONTRACT_MISMATCH")
    model = {"algorithm": "native-cohort-beta-binomial", "version": "cohort_baseline_v1"}
    if model_version_id is not None:
        from app.models.tables import WinnerModelVersion
        from app.services.winner_probability.model_artifact_service import ModelArtifactService

        version = db.get(WinnerModelVersion, model_version_id)
        if version is None or version.outcome_definition_id != definition.id:
            raise ValueError("MUTATION_WINNER_ESTIMATE_MODEL_MISMATCH")
        from app.services.winner_probability.model_authority import validate_model_source

        validate_model_source(db, version)
        if version.algorithm != "cohort" or version.status != "ACTIVE":
            raise ValueError("MUTATION_WINNER_ESTIMATE_NATIVE_SERVING_MODEL_REQUIRED")
        ModelArtifactService().validate_model_version(version, config=config)
        model = {
            "id": version.id,
            "artifact_hash": version.artifact_hash,
            "model_key": version.model_key,
        }
    manifests = {
        "prediction": {
            "id": prediction.id,
            "identity": str(identity.fingerprint()),
            "capture": prediction.lineage_json["native_capture_proof"],
        },
        "model": model,
        "evidence_manifest": {
            "selection": "NATIVE_COHORT_EVIDENCE_AT_EXPLICIT_CUTOFF",
            "cutoff_at": cutoff_at,
            "outcome_id": definition.id,
        },
        "outcome_contract": {"id": definition.id, **expected},
    }
    if generation is not None:
        manifests["generation"] = {
            "id": generation.id,
            "key": generation.generation_key,
            "watermark_hash": generation.watermark_hash,
        }
    clock = MarketClockService().cutoff_for(cutoff_at, reason="WINNER_ESTIMATE_EXPLICIT_REQUEST")
    base = identity
    if estimate_kind == "LATEST_RESCORE":
        from app.services.calculation_identity import IdentityDimension

        base = consumer_context_identity(
            market_cutoff=clock,
            run_id=prediction.run_id,
            pipeline_id=None,
            ticker=prediction.ticker,
        )
        base = replace(
            base, ownership=replace(base.ownership, pipeline_id=IdentityDimension.not_applicable())
        )
    estimate_identity = effective.bind(
        build_contextual_result_identity(
            base=base,
            namespace="winner-estimate",
            config_hash=effective.snapshot.semantic_hash,
            calculation_version=config.engine.calculation_version,
            engine_version=config.feature_schema.version,
            source_artifacts=(),
            source_payload={"kind": estimate_kind, **manifests},
        )
    )
    context = core_mutation_context(
        db,
        domain=MutationDomain.WINNER_ESTIMATE,
        identity=estimate_identity,
        configuration=effective.snapshot,
        sources={},
        manifests=manifests,
        entrypoint="ProbabilityEstimator",
        writer="persist_winner_estimate",
    )
    validate_core_mutation_authority(
        db,
        mutation_context or context,
        domain=MutationDomain.WINNER_ESTIMATE,
        identity=estimate_identity,
        configuration=effective.snapshot,
        sources={},
        manifests=manifests,
        writer="persist_winner_estimate",
    )
    lock_decision_scope(
        db,
        {
            "winner_estimate": prediction.id,
            "outcome": definition.id,
            "kind": estimate_kind,
            "cutoff": cutoff_at,
            "generation": generation.id if generation is not None else None,
        },
    )
    return context


def estimate_body(estimate):
    body = {}
    for column in WinnerProbabilityEstimate.__table__.columns:
        if column.key in PROJECTION_COLUMNS:
            continue
        value = getattr(estimate, column.key)
        if value is not None and isinstance(column.type, Numeric):
            value = Decimal(str(value)).quantize(Decimal(1).scaleb(-column.type.scale))
        body[column.key] = value
    body["configuration"] = (estimate.metadata_json or {}).get(
        "effective_configuration_at_creation"
    )
    return body


@core_writer_member(
    (
        "app.services.winner_probability.probability_estimator:ProbabilityEstimator._create_estimate",
        "app.services.winner_probability.probability_estimator:ProbabilityEstimator._create_candidate_decision_reconstruction",
        "app.services.winner_probability.probability_estimator:ProbabilityEstimator.create_latest_rescore_from_generation",
    )
)
def seal_estimate(db, estimate):
    if estimate.id is not None:
        raise ValueError("MUTATION_WINNER_ESTIMATE_CREATION_ONLY")
    manifest = db.get(WinnerEvidenceManifest, estimate.evidence_manifest_id)
    from app.services.winner_probability.evidence_manifest_service import _hash_payload

    if (
        manifest is None
        or _hash_payload(manifest.payload_json) != manifest.manifest_hash
        or manifest.member_count != len(manifest.payload_json.get("members", []))
        or manifest.manifest_hash != estimate.evidence_manifest_hash
    ):
        raise ValueError("MUTATION_WINNER_ESTIMATE_MANIFEST_MISMATCH")
    estimate.metadata_json = {
        **(estimate.metadata_json or {}),
        PROOF_KEY: {
            "contract": "winner-native-estimate-v1",
            "body_fingerprint": Canonical.fingerprint(estimate_body(estimate)),
            "manifest_hash": manifest.manifest_hash,
        },
    }


def validate_estimate(estimate, config):
    from sqlalchemy import select
    from sqlalchemy.orm import object_session

    from app.services.core_calculation_evidence import calculation_evidence_payload

    db = object_session(estimate)
    if db is None or estimate.id is None:
        raise ValueError("MUTATION_WINNER_RETAINED_ESTIMATE_REQUIRED")
    with db.no_autoflush:
        retained = (
            db.execute(
                select(*estimate.__table__.columns)
                .where(estimate.__table__.c.id == estimate.id)
                .with_for_update()
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
    ) != Canonical.fingerprint(calculation_evidence_payload(estimate)):
        raise ValueError("MUTATION_WINNER_ESTIMATE_RETAINED_BODY_MISMATCH")
    proof = (estimate.metadata_json or {}).get(PROOF_KEY)
    if not isinstance(proof, dict) or proof.get("contract") != "winner-native-estimate-v1":
        raise ValueError("MUTATION_WINNER_CERTIFIED_ESTIMATE_REQUIRED")
    if proof.get("body_fingerprint") != Canonical.fingerprint(estimate_body(estimate)):
        raise ValueError("MUTATION_WINNER_ESTIMATE_BODY_MISMATCH")
    effective = configuration_from_payload(
        estimate.metadata_json["effective_configuration_at_creation"]
    )
    if (
        effective.snapshot.semantic_hash
        != resolve_winner_configuration(config, family="cohort").snapshot.semantic_hash
    ):
        raise ValueError("MUTATION_WINNER_ESTIMATE_CONFIGURATION_MISMATCH")
