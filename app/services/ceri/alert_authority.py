"""Native source and notification authority for supporting CERI alerts."""

from datetime import datetime

from app.models.ceri_tables import CeriChangeEvent, CeriCompany
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.change_authority import (
    _normalized_source,
    validate_normalized_change,
    validate_score_change,
)
from app.services.ceri.change_detection_service import CeriChangeDetectionService, change_dedup_key
from app.services.ceri.change_semantics import ComparisonState, change_dimensions
from app.services.ceri.enums import CeriChangeType
from app.services.decision_effective_configuration import configuration_from_payload
from app.services.market_clock_service import MarketClockService


def validate_change_source(db, change):
    retained, _ = _normalized_source(db, CeriChangeEvent, change.id)
    payload = retained.delta_json or {}
    proof = payload.get("native_change_proof")
    if not isinstance(proof, dict) or proof.get("artifact_role") != "SUPPORTING_CHANGE_LEDGER":
        raise ValueError("MUTATION_CERI_ALERT_CERTIFIED_CHANGE_SOURCE_REQUIRED")
    configuration = configuration_from_payload(payload.get("effective_configuration_at_creation"))
    service = CeriChangeDetectionService.__new__(CeriChangeDetectionService)
    service.effective_configuration = configuration
    service.config = configuration.ceri_decision_config()
    delta = {
        key: value
        for key, value in payload.items()
        if key not in {"native_change_proof", "effective_configuration_at_creation"}
    }
    change_type = CeriChangeType(change.change_type)
    if proof.get("source_kind") == "CERTIFIED_SCORE_COMPARISON":
        from app.models.ceri_tables import CeriScoreSnapshot

        current = db.get(CeriScoreSnapshot, change.to_snapshot_id)
        if current is None:
            raise ValueError("MUTATION_CERI_ALERT_SCORE_SOURCE_REQUIRED")
        session, config_hash, version = (
            current.as_of_session,
            current.config_hash,
            current.calculation_version,
        )
        native = validate_score_change(
            db,
            service,
            company_id=change.company_id,
            change_type=change_type,
            effective_session=session,
            delta=delta,
            config_hash=config_hash,
            calculation_version=version,
            from_snapshot_id=change.from_snapshot_id,
            to_snapshot_id=change.to_snapshot_id,
            comparison_state=ComparisonState(change.comparison_state),
            source_validation=True,
        )
    elif proof.get("source_kind") == "NORMALIZED_EVENT_DERIVATION":
        sources = proof["sources"]
        prior_id = sources.get("prior", {}).get("id")
        operation = proof["operation_time"]
        cutoff = MarketClockService().cutoff_for(
            datetime.fromisoformat(operation["cutoff_at"].replace("Z", "+00:00")),
            reason="RETAINED_CERI_CHANGE_OPERATION",
        )
        from datetime import date

        session = date.fromisoformat(proof["effective_session"])
        config_hash = "event_revision" if change.catalyst_revision_id else "guidance_event"
        version = "ceri-1.0.0"
        native = validate_normalized_change(
            db,
            service,
            company_id=change.company_id,
            change_type=change_type,
            effective_session=session,
            delta=delta,
            config_hash=config_hash,
            calculation_version=version,
            catalyst_revision_id=change.catalyst_revision_id,
            guidance_event_id=change.guidance_event_id,
            prior_catalyst_revision_id=prior_id if change.catalyst_revision_id else None,
            prior_guidance_event_id=prior_id if change.guidance_event_id else None,
            market_cutoff=cutoff,
            comparison_state=ComparisonState(change.comparison_state),
            source_validation=True,
        )
    else:
        raise ValueError("MUTATION_CERI_ALERT_CHANGE_SOURCE_KIND_MISMATCH")
    importance, signal_class = change_dimensions(change_type, delta)
    dedup = change_dedup_key(
        company_id=change.company_id,
        change_type=change.change_type,
        effective_session=session,
        from_snapshot_id=change.from_snapshot_id,
        to_snapshot_id=change.to_snapshot_id,
        catalyst_revision_id=change.catalyst_revision_id,
        guidance_event_id=change.guidance_event_id,
        config_hash=config_hash,
        calculation_version=version,
        effective_configuration_hash=configuration.snapshot.semantic_hash,
    )
    if (
        Canonical.dumps(proof) != Canonical.dumps(native)
        or change.dedup_key != dedup
        or change.severity != importance.value
        or change.importance != importance.value
        or change.signal_class != signal_class.value
    ):
        raise ValueError("MUTATION_CERI_ALERT_CHANGE_SOURCE_PROOF_MISMATCH")
    cutoff_at = native["operation_time"]["cutoff_at"]
    return MarketClockService().cutoff_for(cutoff_at, reason="EXACT_CERI_ALERT_SOURCE_TIME")


def alert_body(event):
    values = {
        column.name: getattr(event, column.name)
        for column in event.__table__.columns
        if column.name
        not in {
            "id",
            "created_at",
            "status",
            "acknowledged_at",
            "dismissed_at",
            "validity_classification",
            "invalidated_reason",
            "invalidated_at",
        }
    }
    values["evidence_json"] = {
        key: value
        for key, value in (event.evidence_json or {}).items()
        if key != "native_alert_proof"
    }
    return values


def validate_alert_source(db, service, change, ticker):
    cutoff = validate_change_source(db, change)
    configuration = service.effective_configuration
    configuration.require_family("decision.alerts.ceri")
    company = db.get(CeriCompany, change.company_id)
    if company is None or company.ticker.upper() != ticker.upper():
        raise ValueError("MUTATION_CERI_ALERT_COMPANY_TICKER_MISMATCH")
    from app.services.ceri.alert_service import CeriAlertService
    from app.services.ceri.evidence_eligibility import EXCLUDED, effective_disposition_by_snapshot

    calculator = CeriAlertService.__new__(CeriAlertService)
    calculator.config = configuration.ceri_decision_config()
    requested_snapshot_ids = {
        int(value) for value in (change.from_snapshot_id, change.to_snapshot_id) if value
    }
    prefetched = getattr(service, "_prefetched_dispositions", None)
    dispositions = (
        {
            snapshot_id: prefetched[snapshot_id]
            for snapshot_id in requested_snapshot_ids
            if snapshot_id in prefetched
        }
        if prefetched is not None
        else effective_disposition_by_snapshot(db, requested_snapshot_ids)
    )
    if any(value == EXCLUDED for value in dispositions.values()):
        raise ValueError("MUTATION_CERI_ALERT_EXCLUDED_SOURCE")
    if not configuration.values["enabled"] or not calculator._eligible_change(db, change):
        raise ValueError("MUTATION_CERI_ALERT_NATIVE_CONDITION_INELIGIBLE")
    return cutoff


def validate_rule(db, service, rule, change):
    from app.models.ceri_tables import CeriAlertRule
    from app.services.source_mutation_authority import prefetched_source_rows

    configuration = service.effective_configuration
    configuration.require_family("decision.alerts.ceri")
    native_config = configuration.ceri_decision_config()
    change_type = CeriChangeType(change.change_type)
    native_rule = native_config.alerts.rules.get(change_type)
    if native_rule is None or not native_rule.enabled:
        raise ValueError("MUTATION_CERI_ALERT_FROZEN_RULE_INELIGIBLE")
    expected = next(
        (
            value
            for value in configuration.values["rules"]
            if value["rule_id"] == change.change_type
        ),
        None,
    )
    if expected is None:
        expected = {
            "rule_id": change.change_type,
            "enabled": native_rule.enabled,
            "severity": native_rule.severity,
            "cooldown_sessions": native_rule.cooldown_sessions,
            "config_version": native_config.engine.config_version,
        }
    actual = {key: getattr(rule, key) for key in expected}
    prefetched = prefetched_source_rows(db, CeriAlertRule, [rule.id])
    physical = prefetched[0] if prefetched is not None else db.get(CeriAlertRule, rule.id)
    if (
        not expected["enabled"]
        or Canonical.dumps(actual) != Canonical.dumps(expected)
        or physical is None
        or physical.rule_id != expected["rule_id"]
    ):
        raise ValueError("MUTATION_CERI_ALERT_FROZEN_RULE_MISMATCH")


def validate_notification(db, event, *, allow_legacy=False):
    payload = event.evidence_json or {}
    proof = payload.get("native_alert_proof")
    if proof is None and allow_legacy:
        return
    if not isinstance(proof, dict) or proof.get("body_fingerprint") != Canonical.fingerprint(
        alert_body(event)
    ):
        raise ValueError("MUTATION_CERI_ALERT_NOTIFICATION_BODY_MISMATCH")
    change = db.get(CeriChangeEvent, event.source_change_event_id)
    if (
        change is None
        or change.dedup_key != proof.get("change_key")
        or payload.get("change_type") != change.change_type
        or Canonical.dumps(payload.get("delta")) != Canonical.dumps(change.delta_json)
        or event.source_catalyst_revision_id != change.catalyst_revision_id
        or event.importance != (change.importance or change.severity)
        or event.signal_class != change.signal_class
    ):
        raise ValueError("MUTATION_CERI_ALERT_NOTIFICATION_SOURCE_MISMATCH")
    # Notification status preserves its creation decision. Later normalized
    # revision/current-state changes do not reinterpret that historical decision.
    configuration_from_payload(payload["effective_configuration_at_creation"]).require_family(
        "decision.alerts.ceri"
    )
