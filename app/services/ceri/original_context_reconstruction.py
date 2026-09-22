"""Historical CERI alert rule/config context over the shared T16A contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriAlertEvent, CeriChangeEvent
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.original_context_reconstruction import (
    AuthorityAvailability,
    AuthorityDimension,
    AuthorityReference,
    AuthorityReferenceKind,
    AuthorityResolution,
    CodeIdentityStatus,
    OriginalContextReconstructionManifest,
    ReconstructionAuthorization,
    ReconstructionComparison,
    ReconstructionComparisonStatus,
    ReconstructionMode,
    ReconstructionResult,
    authorize_reconstruction,
)


@dataclass(frozen=True)
class CeriAlertRuleContextResolution:
    manifest: OriginalContextReconstructionManifest
    authorization: ReconstructionAuthorization
    reconstructed_output: dict[str, Any] | None
    result: ReconstructionResult


def _reference(reference_type: str, reference_id: str) -> AuthorityReference:
    return AuthorityReference(
        reference_type=reference_type,
        reference_id=reference_id,
        kind=AuthorityReferenceKind.CONTENT_ADDRESSED_HISTORICAL,
    )


def _exact(dimension: AuthorityDimension, *references: AuthorityReference) -> AuthorityResolution:
    return AuthorityResolution(
        dimension=dimension,
        availability=AuthorityAvailability.EXACT,
        material=True,
        references=references,
    )


def _unavailable(reason: str) -> tuple[AuthorityResolution, ...]:
    dimensions = (
        AuthorityDimension.CALCULATION_IDENTITY,
        AuthorityDimension.WORK_SCOPE_IDENTITY,
        AuthorityDimension.BUSINESS_CUTOFF,
        AuthorityDimension.EFFECTIVE_CONFIGURATION,
        AuthorityDimension.SOURCE_EVIDENCE,
        AuthorityDimension.RULE_POLICY_VERSION,
        AuthorityDimension.DECISION_INPUTS,
        AuthorityDimension.ALGORITHM_SCHEMA_VERSION,
    )
    return tuple(
        AuthorityResolution(
            dimension=dimension,
            availability=AuthorityAvailability.LEGACY_UNKNOWN,
            material=True,
            reason=reason,
            permanently_unavailable=True,
        )
        for dimension in dimensions
    )


def _bounded_code() -> AuthorityResolution:
    return AuthorityResolution(
        dimension=AuthorityDimension.CODE_DEPLOYMENT_IDENTITY,
        availability=AuthorityAvailability.BOUNDED,
        material=False,
        reason="native CERI evidence schema is retained; deployment commit is not",
    )


def _resolve_native(db: Session, event: CeriAlertEvent):
    from app.services.ceri.alert_authority import (
        alert_body,
        validate_change_source,
        validate_notification,
    )
    from app.services.decision_effective_configuration import configuration_from_payload

    validate_notification(db, event)
    payload = event.evidence_json or {}
    proof = payload.get("native_alert_proof")
    if not isinstance(proof, dict) or proof.get("classification") != "SUPPORTED_DISTINCT_SAFE":
        raise ValueError("native CERI alert proof is unavailable")
    change = db.get(CeriChangeEvent, event.source_change_event_id)
    if change is None:
        raise ValueError("native CERI alert source change is unavailable")
    validate_change_source(db, change)
    configuration = configuration_from_payload(payload.get("effective_configuration_at_creation"))
    configuration.require_family("decision.alerts.ceri")
    rule = proof.get("rule")
    operation = proof.get("operation_time")
    if not isinstance(rule, dict) or not isinstance(operation, dict):
        raise ValueError("native CERI rule or operation-time proof is unavailable")
    expected_rule = {
        "row_id": event.alert_rule_id,
        "rule_id": payload.get("alert_rule"),
        "severity": event.severity,
        "cooldown_sessions": payload.get("cooldown_sessions"),
        "config_version": payload.get("alert_rule_version"),
    }
    if any(rule.get(key) != value for key, value in expected_rule.items()):
        raise ValueError("native CERI historical rule proof mismatch")
    if (
        not rule.get("enabled")
        or operation.get("cutoff_at") is None
        or operation.get("session") is None
    ):
        raise ValueError("native CERI rule/time authority is incomplete")
    body_fingerprint = Canonical.fingerprint(alert_body(event))
    if proof.get("body_fingerprint") != body_fingerprint:
        raise ValueError("native CERI alert body fingerprint mismatch")
    change_proof = (change.delta_json or {}).get("native_change_proof")
    if not isinstance(change_proof, dict):
        raise ValueError("native CERI change proof is unavailable")
    change_reference = _reference(
        "ceri_change_evidence", f"{change.id}:{Canonical.fingerprint(change.delta_json)}"
    )
    alert_reference = _reference("ceri_alert_body", f"{event.id}:{body_fingerprint}")
    rule_reference = _reference(
        "ceri_alert_rule_at_creation", f"{event.id}:{Canonical.fingerprint(rule)}"
    )
    config_reference = _reference(
        "ceri_alert_configuration",
        f"{event.id}:{configuration.snapshot.semantic_hash}:{configuration.snapshot.resolution_hash}",
    )
    time_reference = _reference(
        "ceri_alert_operation_time", f"{event.id}:{Canonical.fingerprint(operation)}"
    )
    authority = (
        _exact(AuthorityDimension.CALCULATION_IDENTITY, alert_reference),
        _exact(AuthorityDimension.WORK_SCOPE_IDENTITY, alert_reference, change_reference),
        _exact(AuthorityDimension.BUSINESS_CUTOFF, time_reference),
        _exact(AuthorityDimension.EFFECTIVE_CONFIGURATION, config_reference),
        _exact(AuthorityDimension.SOURCE_EVIDENCE, change_reference),
        _exact(AuthorityDimension.RULE_POLICY_VERSION, rule_reference),
        _exact(
            AuthorityDimension.DECISION_INPUTS,
            alert_reference,
            change_reference,
            rule_reference,
            config_reference,
        ),
        _exact(AuthorityDimension.ALGORITHM_SCHEMA_VERSION, alert_reference),
        _bounded_code(),
    )
    output = Canonical.canonicalize(
        {
            "alert_body": alert_body(event),
            "historical_rule": rule,
            "historical_configuration": payload["effective_configuration_at_creation"],
            "source_change_id": change.id,
        }
    )
    return authority, output


def resolve_ceri_alert_rule_original_context(
    db: Session, alert_event_id: int
) -> CeriAlertRuleContextResolution:
    """Resolve a CERI alert's retained R/C/K context without reading today's rule row."""

    before = (frozenset(db.new), frozenset(db.dirty), frozenset(db.deleted))
    with db.no_autoflush:
        event = db.get(CeriAlertEvent, alert_event_id)
        try:
            if event is None:
                raise ValueError("CERI alert event is missing")
            authority, output = _resolve_native(db, event)
        except (KeyError, TypeError, ValueError) as exc:
            authority, output = _unavailable(str(exc)), None
    target_id = str(alert_event_id)
    manifest = OriginalContextReconstructionManifest(
        target_artifact_type="CERI_ALERT_RULE_CONTEXT",
        target_artifact_id=target_id,
        target_historical_decision_id=f"CERI_ALERT_RULE_CONTEXT:{target_id}",
        requested_mode=ReconstructionMode.ORIGINAL_CONTEXT,
        authority=authority,
        calculation_identity=(event.event_key if event is not None else None),
        scope_identity=(event.ticker if event is not None else None),
        code_identity_status=CodeIdentityStatus.BOUNDED_CODE_IDENTITY,
    )
    authorization = authorize_reconstruction(manifest)
    if not authorization.authorized:
        output = None
    comparison = ReconstructionComparison(
        manifest_fingerprint=manifest.fingerprint(),
        mode=ReconstructionMode.ORIGINAL_CONTEXT,
        comparison_target_id=(f"CERI_ALERT:{target_id}" if event is not None else None),
        status=(
            ReconstructionComparisonStatus.MATCHED_ORIGINAL
            if output is not None
            else ReconstructionComparisonStatus.INSUFFICIENT_AUTHORITY
        ),
    )
    result = ReconstructionResult(
        manifest_fingerprint=manifest.fingerprint(),
        mode=ReconstructionMode.ORIGINAL_CONTEXT,
        reconstruction_status=manifest.status,
        authority_completeness=manifest.completeness,
        result_fingerprint=Canonical.fingerprint(output) if output is not None else None,
        comparison=comparison,
    )
    after = (frozenset(db.new), frozenset(db.dirty), frozenset(db.deleted))
    if after != before:
        raise RuntimeError("CERI original-context resolution attempted to mutate the session")
    return CeriAlertRuleContextResolution(manifest, authorization, output, result)
