"""Recompute Alert conditions from exact retained native source artifacts."""

from app.models.tables import (
    CoreCalculationEvidence,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvent,
    SetupLifecycleTransitionEvidence,
    SetupSignalSnapshot,
    SignalChangeEvent,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.decision_mutation_authority import validate_retained_decision


def validate_alert_condition(
    db,
    *,
    rule,
    payload,
    ticker,
    timeframe,
    session,
    source_event_key,
    semantic_key,
    setup_id,
    evaluation_id,
    transition_id,
):
    from app.services.setup_lifecycle.alert_service import (
        _event_market_regime,
        _lifecycle_rule_matches,
        _lifecycle_semantic_key,
        _signal_rule_matches,
        _signal_semantic_key,
        _source_confidence,
    )
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository

    lifecycle_id = payload.get("lifecycle_event_id")
    change_id = payload.get("signal_change_event_id")
    episode_id = payload.get("episode_id")
    if lifecycle_id is not None and change_id is not None:
        raise ValueError("MUTATION_ALERT_SOURCE_EVENT_AMBIGUOUS")
    if lifecycle_id is not None:
        event = db.get(SetupLifecycleEvent, lifecycle_id)
        transition = (
            db.get(SetupLifecycleTransitionEvidence, transition_id) if transition_id else None
        )
        validate_retained_decision(
            db, transition, contract="setup-lifecycle-transition-evidence-v1"
        )
        if event is None or event.transition_evidence_id != transition.id:
            raise ValueError("MUTATION_ALERT_EXACT_SOURCE_EVENT_REQUIRED")
        aliases = {
            "effective_session": "effective_date",
            "reasons": "reason_codes_json",
            "evidence": "evidence_json",
        }
        for field, expected in transition.payload_json.items():
            attribute = aliases.get(field, field)
            if hasattr(event, attribute) and Canonical.dumps(
                {field: getattr(event, attribute)}
            ) != Canonical.dumps({field: expected}):
                raise ValueError("MUTATION_ALERT_SOURCE_EVENT_BODY_MISMATCH: " + field)
        if (
            transition.evaluation_evidence_id != evaluation_id
            or transition.setup_evidence_id != setup_id
            or event.episode_id != episode_id
            or not _lifecycle_rule_matches(rule, event)
        ):
            raise ValueError("MUTATION_ALERT_NATIVE_CONDITION_MISMATCH")
        snapshot = db.get(SetupSignalSnapshot, event.snapshot_id) if event.snapshot_id else None
        expected_payload = {
            "source": "lifecycle_event",
            "to_state": event.to_state,
            "to_phase": event.to_phase,
            "actionability_after": event.actionability_after,
            "market_regime": _event_market_regime(event, snapshot, db=db),
            "setup_family": event.setup_family,
            "source_evidence": event.evidence_json,
            "semantic_key": _lifecycle_semantic_key(rule, event),
        }
        confidence = event.confidence_score
        native_key = event.source_event_key
    elif change_id is not None:
        from app.services.decision_effective_configuration import configuration_from_payload
        from app.services.setup_lifecycle.change_authority import validate_signal_change

        event = db.get(SignalChangeEvent, change_id)
        proof = (event.evidence_json or {}).get("native_change_proof") if event else None
        if not isinstance(proof, dict):
            raise ValueError("MUTATION_ALERT_CERTIFIED_CHANGE_SOURCE_REQUIRED")
        reference = proof.get("effective_configuration_reference") or {}
        setup = db.get(CoreCalculationEvidence, reference.get("core_evidence_id"))
        validate_retained_decision(db, setup, contract="unused")
        configuration = configuration_from_payload(
            setup.payload_json["effective_configuration_at_creation"]
        )
        if (
            setup.id != setup_id
            or reference.get("semantic_hash") != configuration.snapshot.semantic_hash
            or reference.get("resolution_hash") != configuration.snapshot.resolution_hash
            or Canonical.dumps(proof)
            != Canonical.dumps(
                validate_signal_change(
                    db,
                    event,
                    configuration=configuration,
                    source_manifest=proof["source_manifest"],
                    source_validation=True,
                )
            )
            or not _signal_rule_matches(rule, event)
            or event.episode_id != episode_id
        ):
            raise ValueError("MUTATION_ALERT_NATIVE_CHANGE_CONDITION_MISMATCH")
        expected_payload = {
            "source": "signal_change_event",
            "signal_key": event.signal_key,
            "threshold_direction": event.threshold_direction,
            "direction": event.direction,
            "market_regime": (event.evidence_json or {}).get("market_regime"),
            "setup_family": (event.evidence_json or {}).get("setup_family"),
            "old_value": event.old_value_json,
            "new_value": event.new_value_json,
            "normalized_delta": str(event.normalized_delta)
            if event.normalized_delta is not None
            else None,
            "source_evidence": event.evidence_json,
            "semantic_key": _signal_semantic_key(rule, event),
        }
        confidence = _source_confidence(event)
        native_key = event.source_event_key
    elif payload.get("source") == "actionability_change":
        evaluation = (
            db.get(SetupLifecycleEvaluationEvidence, evaluation_id) if evaluation_id else None
        )
        validate_retained_decision(
            db,
            evaluation,
            contract="setup-lifecycle-evaluation-evidence-v1",
            payload_key="payload_fingerprint",
        )
        frozen = evaluation.payload_json
        prior = db.get(SetupLifecycleEvaluationEvidence, frozen.get("prior_evaluation_evidence_id"))
        validate_retained_decision(
            db,
            prior,
            contract="setup-lifecycle-evaluation-evidence-v1",
            payload_key="payload_fingerprint",
        )
        episode = db.get(SetupLifecycleEpisode, episode_id) if episode_id else None
        metadata = frozen["projection_after"]["metadata_json"]
        if (
            rule.rule_id != "GATE_BLOCKED"
            or rule.scope != "actionability_change"
            or episode is None
            or frozen.get("episode_id") != episode.id
            or frozen["decision"]["actionability"] != "BLOCKED"
            or prior.payload_json["projection_after"]["current_actionability"]
            not in {"ACTIONABLE", "WATCH_ONLY"}
            or "GATE_BLOCKED" not in metadata["actionability_reason_codes"]
            or prior.ticker != ticker.upper()
            or prior.timeframe != timeframe
            or prior.setup_family != evaluation.setup_family
            or prior.decision_session > session
            or prior.payload_json.get("episode_id") not in {None, episode.id}
        ):
            raise ValueError("MUTATION_ALERT_NATIVE_GATE_CONDITION_MISMATCH")
        blockers = frozen["decision"]["actionability_blockers"]
        expected_payload = {
            "source": "actionability_change",
            "semantic_key": f"GATE_BLOCKED:{episode.id}",
            "actionability_after": "BLOCKED",
            "blockers": blockers,
            "market_regime": metadata.get("market_regime"),
            "setup_family": evaluation.setup_family,
        }
        confidence = frozen["decision"]["confidence_score"]
        native_key = SetupLifecycleRepository.stable_key(
            "gate_blocked",
            str(evaluation.evaluation_run_id or ""),
            str(episode.id),
            ticker.upper(),
            timeframe,
            session.isoformat(),
            ",".join(blockers),
        )
        event = evaluation
    else:
        raise ValueError("MUTATION_ALERT_EXACT_SOURCE_EVENT_REQUIRED")
    native_session = getattr(event, "effective_date", getattr(event, "decision_session", None))
    if (
        event.ticker != ticker.upper()
        or event.timeframe != timeframe
        or native_session != session
        or native_key != source_event_key
        or semantic_key != expected_payload["semantic_key"]
        or payload.get("source_confidence") != confidence
        or any(
            Canonical.dumps(payload.get(key)) != Canonical.dumps(value)
            for key, value in expected_payload.items()
        )
        or payload.get("evaluation_run_id") != event.evaluation_run_id
    ):
        raise ValueError("MUTATION_ALERT_SOURCE_EVENT_BINDING_MISMATCH")
    return expected_payload


def validate_alert_event_projection(db, alert, *, allow_legacy=False):
    from app.models.tables import SignalAlertDecisionEvidence, SignalAlertRuleEvidence
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository

    if alert.decision_evidence_id is None and allow_legacy:
        return
    if alert.id is None and (
        alert.status != "UNREAD"
        or alert.acknowledged_at is not None
        or alert.dismissed_at is not None
    ):
        raise ValueError("MUTATION_ALERT_INITIAL_NOTIFICATION_STATUS_MISMATCH")
    decision = db.get(SignalAlertDecisionEvidence, alert.decision_evidence_id)
    validate_retained_decision(db, decision, contract="signal-alert-decision-evidence-v1")
    rule = db.get(SignalAlertRuleEvidence, decision.rule_evidence_id)
    validate_retained_decision(db, rule, contract="signal-alert-rule-evidence-v1")
    source = decision.payload_json["decision_payload"]
    expected_key = SetupLifecycleRepository.alert_event_key(
        rule_id=rule.rule_id,
        source_event_key=decision.source_event_key,
        ticker=decision.ticker,
        episode_id=source["episode_id"],
        effective_date=decision.effective_session,
        evaluation_run_id=source["evaluation_run_id"],
    )
    expected_evidence = {
        key: value
        for key, value in source.items()
        if key
        not in {"lifecycle_event_id", "signal_change_event_id", "episode_id", "evaluation_run_id"}
    }
    expected_evidence["rule_id"] = rule.rule_id
    expected = {
        "alert_rule_id": rule.rule_row_id,
        "lifecycle_event_id": source["lifecycle_event_id"],
        "signal_change_event_id": source["signal_change_event_id"],
        "evaluation_run_id": source["evaluation_run_id"],
        "ticker": decision.ticker,
        "timeframe": decision.timeframe,
        "effective_date": decision.effective_session,
        "event_key": expected_key,
        "source_event_key": decision.source_event_key,
        "severity": rule.payload_json["severity"],
        "reason_codes_json": decision.reasons_json,
        "evidence_json": expected_evidence,
    }
    if decision.decision != "GENERATED" or any(
        Canonical.dumps({key: getattr(alert, key)}) != Canonical.dumps({key: value})
        for key, value in expected.items()
    ):
        raise ValueError("MUTATION_ALERT_EVENT_PROJECTION_MISMATCH")
