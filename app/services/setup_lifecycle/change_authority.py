"""Exact native authority for supporting signal-change ledger rows."""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.models.tables import (
    CoreCalculationEvidence,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvaluationRun,
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
    SetupSignalSnapshotSelectionEvent,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.decision_mutation_authority import (
    decision_authority,
    validate_retained_decision,
    validate_setup_projection,
)

PROJECTION_FIELDS = (
    "primary_setup_family",
    "primary_phase",
    "lifecycle_state_candidate",
    "actionability_candidate",
    "confidence_score",
    "confidence_label",
)


def signal_change_body(event):
    values = {
        column.name: getattr(event, column.name)
        for column in event.__table__.columns
        if column.name not in {"id", "created_at"}
    }
    values["evidence_json"] = {
        key: value
        for key, value in (values["evidence_json"] or {}).items()
        if key != "native_change_proof"
    }
    for key in ("delta_numeric", "percentage_delta", "percentile_delta", "normalized_delta"):
        if values[key] is not None:
            values[key] = Decimal(str(values[key])).quantize(
                Decimal("0.00000001"), rounding=ROUND_HALF_UP
            )
    return Canonical.canonicalize(values)


def _evaluation_projection(evaluation):
    payload = evaluation.payload_json
    decision = payload.get("decision") or {}
    return {
        "primary_setup_family": evaluation.setup_family,
        "primary_phase": evaluation.output_phase,
        "lifecycle_state_candidate": evaluation.output_state,
        "actionability_candidate": decision.get("actionability"),
        "confidence_score": decision.get("confidence_score"),
        "confidence_label": decision.get("confidence_label"),
    }


def _projection_pin(db, snapshot, *, discover=False, supplied=None):
    validate_setup_projection(db, snapshot)
    setup = db.get(CoreCalculationEvidence, snapshot.evidence_id)
    captured = (setup.payload_json.get("debug_json") or {}).get("native_projection_at_capture")
    if not isinstance(captured, dict) or set(captured) != set(PROJECTION_FIELDS):
        raise ValueError("MUTATION_CHANGE_CAPTURE_PROJECTION_AUTHORITY_REQUIRED")
    actual = {key: getattr(snapshot, key) for key in PROJECTION_FIELDS}
    evaluation_id = None
    if Canonical.dumps(actual) != Canonical.dumps(captured):
        if discover:
            # Discovery finds an identical certified financial projection of this
            # exact Setup artifact. It never substitutes a latest predecessor.
            candidates = db.scalars(
                select(SetupLifecycleEvaluationEvidence)
                .where(
                    SetupLifecycleEvaluationEvidence.setup_evidence_id == setup.id,
                    SetupLifecycleEvaluationEvidence.decision_session == snapshot.data_as_of_date,
                )
                .order_by(SetupLifecycleEvaluationEvidence.id)
            )
            evaluation_id = next(
                (
                    row.id
                    for row in candidates
                    if row.payload_json.get("execution_semantics") == "CURRENT_CALCULATION"
                    and Canonical.dumps(_evaluation_projection(row)) == Canonical.dumps(actual)
                ),
                None,
            )
        else:
            evaluation_id = (supplied or {}).get("lifecycle_evaluation_evidence_id")
        evaluation = (
            db.get(SetupLifecycleEvaluationEvidence, evaluation_id) if evaluation_id else None
        )
        validate_retained_decision(
            db,
            evaluation,
            contract="setup-lifecycle-evaluation-evidence-v1",
            payload_key="payload_fingerprint",
        )
        if (
            evaluation.setup_evidence_id != setup.id
            or evaluation.ticker != snapshot.ticker
            or evaluation.timeframe != snapshot.timeframe
            or evaluation.decision_session != snapshot.data_as_of_date
            or evaluation.calculation_cutoff_at != snapshot.calculation_cutoff_at
            or evaluation.payload_json.get("execution_semantics") != "CURRENT_CALCULATION"
            or Canonical.dumps(_evaluation_projection(evaluation)) != Canonical.dumps(actual)
        ):
            raise ValueError("MUTATION_CHANGE_LIFECYCLE_PROJECTION_MISMATCH")
    if discover:
        selection = db.scalar(
            select(SetupSignalSnapshotCurrentSelection).where(
                SetupSignalSnapshotCurrentSelection.selected_snapshot_id == snapshot.id,
            )
        )
        audit = (
            db.scalar(
                select(SetupSignalSnapshotSelectionEvent).where(
                    SetupSignalSnapshotSelectionEvent.selected_snapshot_id == snapshot.id,
                    SetupSignalSnapshotSelectionEvent.selection_revision == selection.revision,
                )
            )
            if selection
            else None
        )
        audit_id = audit.id if audit else None
    else:
        audit_id = (supplied or {}).get("selection_event_id")
        audit = db.get(SetupSignalSnapshotSelectionEvent, audit_id) if audit_id else None
    if (
        audit is None
        or audit.selected_snapshot_id != snapshot.id
        or audit.ticker != snapshot.ticker
        or audit.timeframe != snapshot.timeframe
        or audit.data_as_of_date != snapshot.data_as_of_date
    ):
        raise ValueError("MUTATION_CHANGE_CANONICAL_SOURCE_AUTHORITY_REQUIRED")
    pin = {
        "setup_evidence_id": setup.id,
        "lifecycle_evaluation_evidence_id": evaluation_id,
        "selection_event_id": audit_id,
    }
    if not discover and pin != supplied:
        raise ValueError("MUTATION_CHANGE_PROJECTION_PIN_MISMATCH")
    return pin


def discover_change_sources(db, *, previous, current, history):
    sources = {row.id: row for row in (previous, current, *history)}
    return {
        "history_snapshot_ids": [row.id for row in history],
        "snapshot_projections": {
            str(key): _projection_pin(db, row, discover=True) for key, row in sources.items()
        },
    }


def validate_signal_change(db, event, *, configuration, source_manifest, source_validation=False):
    from app.services.calculation_identity import CalculationIdentity
    from app.services.domain_mutation import MutationDomain, MutationSemanticMode
    from app.services.setup_lifecycle.change_detector import SetupLifecycleChangeDetector

    if configuration is None or not isinstance(source_manifest, dict):
        raise ValueError("MUTATION_CHANGE_EXACT_AUTHORITY_REQUIRED")
    configuration.require_family("decision.setup")
    previous = db.get(SetupSignalSnapshot, event.previous_snapshot_id)
    current = db.get(SetupSignalSnapshot, event.current_snapshot_id)
    if previous is None or current is None:
        raise ValueError("MUTATION_CHANGE_SETUP_PREDECESSORS_REQUIRED")
    history = [db.get(SetupSignalSnapshot, key) for key in source_manifest["history_snapshot_ids"]]
    if any(row is None for row in history) or len({row.id for row in history}) != len(history):
        raise ValueError("MUTATION_CHANGE_HISTORY_AUTHORITY_MISMATCH")
    if (
        previous.data_as_of_date >= current.data_as_of_date
        or current.ticker != event.ticker
        or current.timeframe != event.timeframe
        or current.data_as_of_date != event.effective_date
        or current.config_hash != configuration.setup_config().config_hash
        or any(
            row.ticker != current.ticker
            or row.timeframe != current.timeframe
            or row.data_as_of_date >= current.data_as_of_date
            for row in (previous, *history)
        )
    ):
        raise ValueError("MUTATION_CHANGE_SETUP_SCOPE_OR_TIME_MISMATCH")
    sources = {row.id: row for row in (previous, current, *history)}
    if set(source_manifest["snapshot_projections"]) != {str(key) for key in sources}:
        raise ValueError("MUTATION_CHANGE_SOURCE_MANIFEST_MISMATCH")
    for key, row in sources.items():
        _projection_pin(db, row, supplied=source_manifest["snapshot_projections"][str(key)])
    setup = db.get(CoreCalculationEvidence, current.evidence_id)
    if (
        setup.payload_json["effective_configuration_at_creation"]["semantic_hash"]
        != configuration.snapshot.semantic_hash
    ):
        raise ValueError("MUTATION_CHANGE_CONFIGURATION_MISMATCH")
    if event.evaluation_run_id is not None:
        run = db.get(SetupLifecycleEvaluationRun, event.evaluation_run_id)
        if (
            run is None
            or run.mode not in {"LIVE", "REPAIR"}
            or run.source_run_id not in {None, current.run_id}
            or run.config_hash != current.config_hash
        ):
            raise ValueError("MUTATION_CHANGE_EVALUATION_RUN_SCOPE_MISMATCH")
    detector = SetupLifecycleChangeDetector(config=configuration.setup_config())
    changes = detector.detect_changes(previous=previous, current=current, history=tuple(history))
    change = next((row for row in changes if row.signal_key == event.signal_key), None)
    if change is None:
        raise ValueError("MUTATION_CHANGE_NATIVE_CONDITION_MISMATCH")
    expected = detector._to_event(
        change, previous=previous, current=current, evaluation_run_id=event.evaluation_run_id
    )
    if signal_change_body(expected) != signal_change_body(event):
        raise ValueError("MUTATION_CHANGE_NATIVE_OUTPUT_MISMATCH")
    identity = CalculationIdentity.from_canonical_payload(setup.calculation_identity_json)
    if not source_validation:
        decision_authority(
            db,
            domain=MutationDomain.CURRENT_PROJECTION,
            writer="add_signal_change_event",
            identity=identity,
            configuration=configuration,
            records={"target_evidence": setup},
            manifests={
                "projection_scope": {
                    "derived_signal_change": signal_change_body(event),
                    "sources": source_manifest,
                }
            },
            semantic_mode=MutationSemanticMode.CURRENT_PROJECTION_ADVANCE,
        )
    return {
        "artifact_role": "SUPPORTING_CHANGE_LEDGER",
        "classification": "SUPPORTED_DISTINCT_SAFE",
        "source_manifest": source_manifest,
        "effective_configuration_reference": {
            "core_evidence_id": setup.id,
            "semantic_hash": configuration.snapshot.semantic_hash,
            "resolution_hash": configuration.snapshot.resolution_hash,
        },
        "calculation_identity_fingerprint": str(identity.fingerprint()),
        "native_body_fingerprint": Canonical.fingerprint(signal_change_body(event)),
    }
