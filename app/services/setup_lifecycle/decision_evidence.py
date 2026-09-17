from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.models.tables import (
    CoreCalculationEvidence,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupLifecycleTransitionEvidence,
    SetupSignalSnapshot,
    SignalAlertDecisionEvidence,
    SignalAlertRule,
    SignalAlertRuleEvidence,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    calculation_evidence_payload,
    persist_core_evidence,
)
from app.services.producer_readiness import (
    READINESS_PAYLOAD_KEY,
    ProducerReadinessEnvelope,
    legacy_readiness,
    normalize_producer_readiness,
    readiness_from_lifecycle_evaluation,
)

_SETUP_PROJECTION_FIELDS = {
    "evaluation_run_id",
    "evidence_id",
    "is_canonical",
    "canonical_reason",
    "canonicalized_at",
    "canonical_decision_json",
    "superseded_by_snapshot_id",
    "captured_at",
}


def get_lifecycle_readiness_for_episode(
    db,
    episode: SetupLifecycleEpisode,
) -> ProducerReadinessEnvelope:
    """Trading state does not certify quality; follow the exact evaluation pointer."""
    evidence_id = episode.latest_evaluation_evidence_id
    if evidence_id is None:
        return legacy_readiness("LIFECYCLE")
    evaluation = get_lifecycle_evaluation_evidence(db, int(evidence_id))
    return readiness_from_lifecycle_evaluation(evaluation)


def persist_setup_evidence(db, snapshot: SetupSignalSnapshot) -> CoreCalculationEvidence | None:
    """Freeze a certified Setup envelope; identity-incomplete rows remain legacy current."""

    identity = calculation_identity_from_debug(snapshot.source_lineage_json)
    if identity is None:
        return None
    lineage_ids = dict((snapshot.source_lineage_json or {}).get("source_ids") or {})
    sources: dict[str, Any] = {}
    source_pairs = (
        ("fundamental", "fundamental_score_id", "fundamental_evidence_id"),
        ("technical", "technical_score_id", "technical_evidence_id"),
        ("combined", "combined_result_id", "combined_evidence_id"),
        ("ranking_metadata", "ranking_result_id", "ranking_evidence_id"),
        ("regime", "market_regime_snapshot_id", "regime_evidence_id"),
        ("sector", "sector_rotation_snapshot_id", "sector_evidence_id"),
    )
    for role, row_key, evidence_key in source_pairs:
        row_id = lineage_ids.get(row_key)
        evidence_id = lineage_ids.get(evidence_key)
        if row_id is None:
            continue
        if evidence_id is None:
            return None
        source = db.get(CoreCalculationEvidence, int(evidence_id))
        if source is None:
            raise EvidenceUnavailableError(
                f"EVIDENCE_UNAVAILABLE: SETUP source role={role} evidence={evidence_id} missing"
            )
        sources[role] = SimpleNamespace(evidence_id=int(evidence_id))

    payload = calculation_evidence_payload(snapshot, excluded_columns=_SETUP_PROJECTION_FIELDS)
    frozen = getattr(snapshot, "_effective_configuration", None)
    if frozen is None and getattr(snapshot, "evidence_id", None) is not None:
        from app.services.core_effective_configuration import core_configuration_from_evidence

        prior = get_setup_evidence(db, snapshot.evidence_id)
        retained = core_configuration_from_evidence(prior)
        frozen = retained.snapshot if retained is not None else None
    return persist_core_evidence(
        db,
        kind=CoreEvidenceKind.SETUP,
        current_row=snapshot,
        sources=sources,
        payload=payload,
        calculation_identity=identity,
        effective_configuration=frozen,
    )


def get_setup_evidence(db, evidence_id: int) -> CoreCalculationEvidence:
    evidence = db.get(CoreCalculationEvidence, evidence_id)
    if evidence is None or evidence.artifact_kind != CoreEvidenceKind.SETUP.value:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: no immutable SETUP evidence id={evidence_id}"
        )
    return evidence


def persist_lifecycle_evaluation_evidence(
    db,
    *,
    snapshot: SetupSignalSnapshot,
    episode: SetupLifecycleEpisode | None,
    decision: Any,
    actionability: Any,
    evaluation_run_id: int | None,
    transition_eligible: bool,
    effective_configuration=None,
) -> SetupLifecycleEvaluationEvidence | None:
    setup = (
        get_setup_evidence(db, snapshot.evidence_id)
        if getattr(snapshot, "evidence_id", None) is not None
        else persist_setup_evidence(db, snapshot)
    )
    if setup is None:
        return None

    # Existing mutable episodes have no trustworthy predecessor address.  They
    # remain explicit legacy-current projections instead of becoming a forged
    # certified chain at the first post-migration observation.
    if episode is not None and getattr(episode, "latest_evaluation_evidence_id", None) is None:
        return None

    prior_evaluation_id = getattr(episode, "latest_evaluation_evidence_id", None)
    prior_transition_id = getattr(episode, "latest_transition_evidence_id", None)
    prior_evaluation = (
        db.get(SetupLifecycleEvaluationEvidence, prior_evaluation_id)
        if prior_evaluation_id is not None
        else None
    )
    if (
        prior_evaluation is not None
        and prior_evaluation.setup_evidence_id == setup.id
        and prior_evaluation.decision_session == snapshot.data_as_of_date
        and prior_evaluation.output_state == decision.proposed_state.value
        and prior_evaluation.output_phase == decision.phase_code
        and prior_evaluation.config_hash == snapshot.config_hash
        and episode.current_state == decision.proposed_state.value
        and episode.current_phase == decision.phase_code
        and (
            effective_configuration is None
            or (prior_evaluation.payload_json.get("effective_configuration_at_creation") or {}).get(
                "semantic_hash"
            )
            == effective_configuration.snapshot.semantic_hash
        )
    ):
        return prior_evaluation
    if (
        prior_evaluation is not None
        and prior_evaluation.decision_session > snapshot.data_as_of_date
    ):
        raise ValueError("historical lifecycle evaluation cannot consume future prior evidence")
    if prior_evaluation is not None and (
        prior_evaluation.ticker != snapshot.ticker
        or prior_evaluation.timeframe != snapshot.timeframe
        or prior_evaluation.setup_family != decision.setup_family.value
    ):
        raise ValueError("lifecycle prior evaluation evidence is scope-incompatible")
    prior_transition = (
        db.get(SetupLifecycleTransitionEvidence, prior_transition_id)
        if prior_transition_id is not None
        else None
    )
    if (
        prior_transition is not None
        and prior_transition.effective_session > snapshot.data_as_of_date
    ):
        raise ValueError("historical lifecycle transition cannot consume future prior evidence")
    if prior_transition is not None and (
        prior_transition.ticker != snapshot.ticker
        or prior_transition.timeframe != snapshot.timeframe
        or prior_transition.setup_family != decision.setup_family.value
    ):
        raise ValueError("lifecycle prior transition evidence is scope-incompatible")

    run = (
        db.get(SetupLifecycleEvaluationRun, evaluation_run_id)
        if evaluation_run_id is not None
        else None
    )
    counters = {
        "state_age_sessions": getattr(episode, "state_age_sessions", 0) if episode else 0,
        "missing_observation_sessions": (
            getattr(episode, "missing_observation_sessions", 0) if episode else 0
        ),
        "last_observed_on": getattr(episode, "last_observed_on", None) if episode else None,
    }
    decision_payload = {
        "setup_evidence_id": setup.id,
        "prior_evaluation_evidence_id": prior_evaluation_id,
        "prior_transition_evidence_id": prior_transition_id,
        "ticker": snapshot.ticker,
        "timeframe": snapshot.timeframe,
        "setup_family": decision.setup_family.value,
        "decision_session": snapshot.data_as_of_date,
        "calculation_cutoff_at": snapshot.calculation_cutoff_at,
        "calendar_version": snapshot.calendar_version,
        "calculation_identity_fingerprint": setup.calculation_identity_fingerprint,
        "execution_mode": getattr(run, "mode", "DIRECT") if run is not None else "DIRECT",
        "previous_state": getattr(episode, "current_state", None),
        "output_state": decision.proposed_state.value,
        "output_phase": decision.phase_code,
        "transition_eligible": transition_eligible,
        "counters": counters,
        "decision": {
            "immediate_transition": decision.immediate_transition,
            "actionability_candidate": decision.actionability_candidate.value,
            "actionability": actionability.actionability.value,
            "confidence_score": decision.confidence_score,
            "confidence_label": decision.confidence_label.value,
            "terminal_reason": decision.terminal_reason,
            "evidence": decision.evidence,
            "actionability_blockers": list(actionability.blockers),
        },
        "engine_version": snapshot.engine_version,
        "config_version": snapshot.config_version,
        "config_hash": snapshot.config_hash,
        "reasons": list(decision.reason_codes),
        "warnings": list(snapshot.warning_flags_json or []),
    }
    if effective_configuration is not None:
        from app.services.contextual_calculation_identity import build_contextual_result_identity
        from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY

        base = _setup_base_identity(setup, snapshot.ticker)
        identity = effective_configuration.bind(
            build_contextual_result_identity(
                base=base,
                namespace="lifecycle-evaluation",
                config_hash=effective_configuration.snapshot.semantic_hash,
                calculation_version=snapshot.engine_version,
                engine_version=snapshot.engine_version,
                source_artifacts=(),
                source_payload={
                    "setup_evidence_id": setup.id,
                    "prior_evaluation_evidence_id": prior_evaluation_id,
                    "prior_transition_evidence_id": prior_transition_id,
                },
            )
        )
        decision_payload[CONFIGURATION_PAYLOAD_KEY] = effective_configuration.snapshot.as_dict()
        decision_payload["calculation_identity"] = identity.canonical_payload()
        decision_payload["calculation_identity_fingerprint"] = str(identity.fingerprint())
        decision_payload["execution_semantics"] = (
            "CURRENT_RULES_RETROSPECTIVE"
            if decision_payload["execution_mode"] == "REPLAY"
            else "CURRENT_CALCULATION"
        )
    decision_payload[READINESS_PAYLOAD_KEY] = normalize_producer_readiness(
        "LIFECYCLE",
        {
            "confidence_score": decision.confidence_score,
            "confidence_label": decision.confidence_label.value,
            "warnings": list(snapshot.warning_flags_json or []),
            "confidence_components": (decision.evidence or {}).get("confidence"),
        },
        identity_fingerprint=decision_payload["calculation_identity_fingerprint"],
        calculation_versions={"engine_version": snapshot.engine_version},
        evaluated_at=snapshot.calculation_cutoff_at,
        business_anchor=snapshot.data_as_of_date,
    ).canonical_payload()
    payload = CanonicalEvidenceSerializer.canonicalize(decision_payload)
    payload_fingerprint = CanonicalEvidenceSerializer.fingerprint(payload)
    evidence_key = CanonicalEvidenceSerializer.fingerprint(
        {
            "contract": "setup-lifecycle-evaluation-evidence-v1",
            "payload_fingerprint": payload_fingerprint,
        }
    )
    existing = db.scalar(
        select(SetupLifecycleEvaluationEvidence).where(
            SetupLifecycleEvaluationEvidence.evidence_key == evidence_key
        )
    )
    if existing is not None:
        return existing
    row = SetupLifecycleEvaluationEvidence(
        setup_evidence_id=setup.id,
        prior_evaluation_evidence_id=prior_evaluation_id,
        prior_transition_evidence_id=prior_transition_id,
        evaluation_run_id=evaluation_run_id,
        ticker=snapshot.ticker,
        timeframe=snapshot.timeframe,
        setup_family=decision.setup_family.value,
        decision_session=snapshot.data_as_of_date,
        calculation_cutoff_at=snapshot.calculation_cutoff_at,
        calendar_version=snapshot.calendar_version,
        calculation_identity_fingerprint=decision_payload["calculation_identity_fingerprint"],
        execution_mode=decision_payload["execution_mode"],
        previous_state=decision_payload["previous_state"],
        output_state=decision.proposed_state.value,
        output_phase=decision.phase_code,
        transition_eligible=transition_eligible,
        engine_version=snapshot.engine_version,
        config_version=snapshot.config_version,
        config_hash=snapshot.config_hash,
        counters_json=CanonicalEvidenceSerializer.canonicalize(counters),
        reasons_json=sorted(set(decision.reason_codes)),
        warnings_json=sorted(set(snapshot.warning_flags_json or [])),
        payload_json=payload,
        payload_fingerprint=payload_fingerprint,
        evidence_key=evidence_key,
    )
    db.add(row)
    db.flush()
    return row


def persist_lifecycle_transition_evidence(
    db,
    *,
    event: SetupLifecycleEvent,
    evaluation: SetupLifecycleEvaluationEvidence | None,
    prior_transition_evidence_id: int | None,
) -> SetupLifecycleTransitionEvidence | None:
    if evaluation is None:
        return None
    if prior_transition_evidence_id is not None:
        prior = db.get(SetupLifecycleTransitionEvidence, prior_transition_evidence_id)
        if prior is None:
            raise EvidenceUnavailableError("prior lifecycle transition evidence is missing")
        if prior.effective_session > event.effective_date:
            raise ValueError("lifecycle transition cannot consume future predecessor evidence")
    payload = CanonicalEvidenceSerializer.canonicalize(
        {
            "evaluation_evidence_id": evaluation.id,
            "setup_evidence_id": evaluation.setup_evidence_id,
            "prior_transition_evidence_id": prior_transition_evidence_id,
            "ticker": event.ticker,
            "timeframe": event.timeframe,
            "setup_family": event.setup_family,
            "effective_session": event.effective_date,
            "event_type": event.event_type,
            "from_state": event.from_state,
            "to_state": event.to_state,
            "from_phase": event.from_phase,
            "to_phase": event.to_phase,
            "state_age_before": event.state_age_before,
            "actionability_before": event.actionability_before,
            "actionability_after": event.actionability_after,
            "immediate_transition": event.immediate_transition,
            "config_hash": event.config_hash,
            "reasons": event.reason_codes_json,
            "evidence": event.evidence_json,
        }
    )
    payload_fingerprint = CanonicalEvidenceSerializer.fingerprint(payload)
    evidence_key = CanonicalEvidenceSerializer.fingerprint(
        {"contract": "setup-lifecycle-transition-evidence-v1", "payload": payload_fingerprint}
    )
    existing = db.scalar(
        select(SetupLifecycleTransitionEvidence).where(
            SetupLifecycleTransitionEvidence.evidence_key == evidence_key
        )
    )
    if existing is not None:
        return existing
    row = SetupLifecycleTransitionEvidence(
        evaluation_evidence_id=evaluation.id,
        setup_evidence_id=evaluation.setup_evidence_id,
        prior_transition_evidence_id=prior_transition_evidence_id,
        ticker=event.ticker,
        timeframe=event.timeframe,
        setup_family=event.setup_family,
        effective_session=event.effective_date,
        event_type=event.event_type,
        from_state=event.from_state,
        to_state=event.to_state,
        from_phase=event.from_phase,
        to_phase=event.to_phase,
        config_hash=event.config_hash,
        reasons_json=sorted(set(event.reason_codes_json or [])),
        payload_json=payload,
        payload_fingerprint=payload_fingerprint,
        evidence_key=evidence_key,
    )
    db.add(row)
    db.flush()
    return row


def persist_observation_gap_evaluation_evidence(
    db,
    *,
    episode: SetupLifecycleEpisode,
    observed_on: date,
    missing_observation_sessions: int,
    threshold: int,
    evaluation_run_id: int | None,
    effective_configuration=None,
) -> SetupLifecycleEvaluationEvidence | None:
    """Record a gap/no-gap evaluation from the exact current certified chain."""

    prior_evaluation_id = episode.latest_evaluation_evidence_id
    if prior_evaluation_id is None:
        return None
    prior = db.get(SetupLifecycleEvaluationEvidence, prior_evaluation_id)
    if prior is None:
        raise EvidenceUnavailableError("current lifecycle evaluation evidence is missing")
    if prior.decision_session > observed_on:
        raise ValueError("observation-gap evaluation cannot consume future evidence")
    if (
        prior.decision_session == observed_on
        and (prior.counters_json or {}).get("missing_observation_sessions")
        == missing_observation_sessions
        and prior.output_state in {episode.current_state, "EXPIRED"}
        and (
            effective_configuration is None
            or (prior.payload_json.get("effective_configuration_at_creation") or {}).get(
                "semantic_hash"
            )
            == effective_configuration.snapshot.semantic_hash
        )
    ):
        return prior
    run = (
        db.get(SetupLifecycleEvaluationRun, evaluation_run_id)
        if evaluation_run_id is not None
        else None
    )
    expired = missing_observation_sessions > threshold
    output_state = "EXPIRED" if expired else episode.current_state
    output_phase = "OBSERVATION_GAP_EXPIRED" if expired else episode.current_phase
    payload = CanonicalEvidenceSerializer.canonicalize(
        {
            "setup_evidence_id": prior.setup_evidence_id,
            "prior_evaluation_evidence_id": prior.id,
            "prior_transition_evidence_id": episode.latest_transition_evidence_id,
            "ticker": episode.ticker,
            "timeframe": episode.timeframe,
            "setup_family": episode.setup_family,
            "decision_session": observed_on,
            "calculation_cutoff_at": prior.calculation_cutoff_at,
            "calendar_version": prior.calendar_version,
            "calculation_identity_fingerprint": prior.calculation_identity_fingerprint,
            "execution_mode": getattr(run, "mode", "MAINTENANCE") if run else "MAINTENANCE",
            "previous_state": episode.current_state,
            "output_state": output_state,
            "output_phase": output_phase,
            "transition_eligible": expired,
            "counters": {
                "missing_observation_sessions": missing_observation_sessions,
                "observation_gap_threshold": threshold,
                "last_observed_on": episode.last_observed_on,
            },
            "engine_version": episode.engine_version,
            "config_version": episode.config_version,
            "config_hash": episode.config_hash,
            "reasons": ["OBSERVATION_GAP_EXPIRED" if expired else "OBSERVATION_GAP_COUNTED"],
            "warnings": [],
        }
    )
    if effective_configuration is not None:
        from app.services.contextual_calculation_identity import build_contextual_result_identity
        from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY

        payload[CONFIGURATION_PAYLOAD_KEY] = effective_configuration.snapshot.as_dict()
        payload["execution_semantics"] = "CURRENT_STATE_REPAIR"
        setup = db.get(CoreCalculationEvidence, prior.setup_evidence_id)
        if setup is None:
            raise EvidenceUnavailableError("gap repair setup evidence is missing")
        base = _setup_base_identity(setup, episode.ticker)
        identity = effective_configuration.bind(
            build_contextual_result_identity(
                base=base,
                namespace="lifecycle-gap-repair",
                config_hash=effective_configuration.snapshot.semantic_hash,
                calculation_version=episode.engine_version,
                engine_version=episode.engine_version,
                source_artifacts=(),
                source_payload={
                    "setup_evidence_id": setup.id,
                    "prior_evaluation_evidence_id": prior.id,
                    "prior_transition_evidence_id": episode.latest_transition_evidence_id,
                    "observed_on": observed_on,
                    "threshold": threshold,
                },
            )
        )
        payload["calculation_identity"] = identity.canonical_payload()
        payload["calculation_identity_fingerprint"] = str(identity.fingerprint())
    payload[READINESS_PAYLOAD_KEY] = normalize_producer_readiness(
        "LIFECYCLE",
        {
            "missing_observation_sessions": missing_observation_sessions,
            "observation_gap_threshold": threshold,
        },
        identity_fingerprint=payload["calculation_identity_fingerprint"],
        calculation_versions={"engine_version": episode.engine_version},
        evaluated_at=prior.calculation_cutoff_at,
        business_anchor=observed_on,
    ).canonical_payload()
    fingerprint = CanonicalEvidenceSerializer.fingerprint(payload)
    key = CanonicalEvidenceSerializer.fingerprint(
        {"contract": "setup-lifecycle-evaluation-evidence-v1", "payload_fingerprint": fingerprint}
    )
    existing = db.scalar(
        select(SetupLifecycleEvaluationEvidence).where(
            SetupLifecycleEvaluationEvidence.evidence_key == key
        )
    )
    if existing is not None:
        return existing
    row = SetupLifecycleEvaluationEvidence(
        setup_evidence_id=prior.setup_evidence_id,
        prior_evaluation_evidence_id=prior.id,
        prior_transition_evidence_id=episode.latest_transition_evidence_id,
        evaluation_run_id=evaluation_run_id,
        ticker=episode.ticker,
        timeframe=episode.timeframe,
        setup_family=episode.setup_family,
        decision_session=observed_on,
        calculation_cutoff_at=prior.calculation_cutoff_at,
        calendar_version=prior.calendar_version,
        calculation_identity_fingerprint=payload["calculation_identity_fingerprint"],
        execution_mode=payload["execution_mode"],
        previous_state=episode.current_state,
        output_state=output_state,
        output_phase=output_phase,
        transition_eligible=expired,
        engine_version=episode.engine_version,
        config_version=episode.config_version,
        config_hash=episode.config_hash,
        counters_json=payload["counters"],
        reasons_json=payload["reasons"],
        warnings_json=[],
        payload_json=payload,
        payload_fingerprint=fingerprint,
        evidence_key=key,
    )
    db.add(row)
    db.flush()
    return row


def persist_alert_rule_evidence(db, rule: SignalAlertRule) -> SignalAlertRuleEvidence:
    payload = CanonicalEvidenceSerializer.canonicalize(
        {
            "rule_id": rule.rule_id,
            "enabled": rule.enabled,
            "severity": rule.severity,
            "scope": rule.scope,
            "setup_family": rule.setup_family,
            "cooldown_sessions": rule.cooldown_sessions,
            "minimum_confidence": rule.minimum_confidence,
            "config_version": rule.config_version,
            "condition": rule.condition_json,
            "market_restrictions": rule.market_restrictions_json,
            "metadata": rule.metadata_json,
        }
    )
    fingerprint = CanonicalEvidenceSerializer.fingerprint(payload)
    key = CanonicalEvidenceSerializer.fingerprint(
        {"contract": "signal-alert-rule-evidence-v1", "payload": fingerprint}
    )
    existing = db.scalar(
        select(SignalAlertRuleEvidence).where(SignalAlertRuleEvidence.evidence_key == key)
    )
    if existing is not None:
        return existing
    evidence = SignalAlertRuleEvidence(
        rule_id=rule.rule_id,
        rule_row_id=rule.id,
        config_version=rule.config_version,
        payload_json=payload,
        payload_fingerprint=fingerprint,
        evidence_key=key,
    )
    db.add(evidence)
    db.flush()
    return evidence


def prior_generated_alert_decision(
    db,
    *,
    rule_id: str,
    ticker: str,
    timeframe: str,
    semantic_key: str,
    effective_session: date,
) -> SignalAlertDecisionEvidence | None:
    """Select only certified generated predecessors at or before decision time."""

    return db.scalar(
        select(SignalAlertDecisionEvidence)
        .join(
            SignalAlertRuleEvidence,
            SignalAlertRuleEvidence.id == SignalAlertDecisionEvidence.rule_evidence_id,
        )
        .where(SignalAlertRuleEvidence.rule_id == rule_id)
        .where(SignalAlertDecisionEvidence.ticker == ticker.strip().upper())
        .where(SignalAlertDecisionEvidence.timeframe == timeframe)
        .where(SignalAlertDecisionEvidence.semantic_key == semantic_key)
        .where(SignalAlertDecisionEvidence.decision == "GENERATED")
        .where(SignalAlertDecisionEvidence.effective_session <= effective_session)
        .order_by(
            SignalAlertDecisionEvidence.effective_session.desc(),
            SignalAlertDecisionEvidence.id.desc(),
        )
        .limit(1)
    )


def persist_alert_decision_evidence(
    db,
    *,
    rule: SignalAlertRule,
    ticker: str,
    timeframe: str,
    effective_session: date,
    source_event_key: str,
    semantic_key: str,
    decision: str,
    reasons: tuple[str, ...],
    payload: dict[str, Any],
    setup_evidence_id: int | None = None,
    lifecycle_evaluation_evidence_id: int | None = None,
    lifecycle_transition_evidence_id: int | None = None,
    cooldown_predecessor_evidence_id: int | None = None,
    dedup_predecessor_evidence_id: int | None = None,
    effective_configuration=None,
) -> SignalAlertDecisionEvidence:
    rule_evidence = persist_alert_rule_evidence(db, rule)
    cutoff = None
    calendar = None
    if lifecycle_evaluation_evidence_id is not None:
        evaluation = db.get(SetupLifecycleEvaluationEvidence, lifecycle_evaluation_evidence_id)
        if evaluation is not None:
            cutoff = evaluation.calculation_cutoff_at
            calendar = evaluation.calendar_version
            setup_evidence_id = setup_evidence_id or evaluation.setup_evidence_id
    evidence_payload = CanonicalEvidenceSerializer.canonicalize(
        {
            "rule_evidence_id": rule_evidence.id,
            "setup_evidence_id": setup_evidence_id,
            "lifecycle_evaluation_evidence_id": lifecycle_evaluation_evidence_id,
            "lifecycle_transition_evidence_id": lifecycle_transition_evidence_id,
            "cooldown_predecessor_evidence_id": cooldown_predecessor_evidence_id,
            "dedup_predecessor_evidence_id": dedup_predecessor_evidence_id,
            "ticker": ticker.strip().upper(),
            "timeframe": timeframe,
            "effective_session": effective_session,
            "calculation_cutoff_at": cutoff,
            "calendar_version": calendar,
            "source_event_key": source_event_key,
            "semantic_key": semantic_key,
            "decision": decision,
            "reasons": list(reasons),
            "decision_payload": payload,
        }
    )
    if effective_configuration is not None:
        from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY

        evidence_payload[CONFIGURATION_PAYLOAD_KEY] = effective_configuration.snapshot.as_dict()
    fingerprint = CanonicalEvidenceSerializer.fingerprint(evidence_payload)
    key = CanonicalEvidenceSerializer.fingerprint(
        {"contract": "signal-alert-decision-evidence-v1", "payload": fingerprint}
    )
    existing = db.scalar(
        select(SignalAlertDecisionEvidence).where(SignalAlertDecisionEvidence.evidence_key == key)
    )
    if existing is not None:
        return existing
    row = SignalAlertDecisionEvidence(
        rule_evidence_id=rule_evidence.id,
        setup_evidence_id=setup_evidence_id,
        lifecycle_evaluation_evidence_id=lifecycle_evaluation_evidence_id,
        lifecycle_transition_evidence_id=lifecycle_transition_evidence_id,
        cooldown_predecessor_evidence_id=cooldown_predecessor_evidence_id,
        dedup_predecessor_evidence_id=dedup_predecessor_evidence_id,
        ticker=ticker.strip().upper(),
        timeframe=timeframe,
        effective_session=effective_session,
        calculation_cutoff_at=cutoff,
        calendar_version=calendar,
        source_event_key=source_event_key,
        semantic_key=semantic_key,
        decision=decision,
        reasons_json=sorted(set(reasons)),
        payload_json=evidence_payload,
        payload_fingerprint=fingerprint,
        evidence_key=key,
    )
    db.add(row)
    db.flush()
    return row


def get_lifecycle_evaluation_evidence(db, evidence_id: int) -> SetupLifecycleEvaluationEvidence:
    row = db.get(SetupLifecycleEvaluationEvidence, evidence_id)
    if row is None:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: lifecycle evaluation evidence id={evidence_id}"
        )
    return row


def get_lifecycle_transition_evidence(db, evidence_id: int) -> SetupLifecycleTransitionEvidence:
    row = db.get(SetupLifecycleTransitionEvidence, evidence_id)
    if row is None:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: lifecycle transition evidence id={evidence_id}"
        )
    return row


def get_alert_decision_evidence(db, evidence_id: int) -> SignalAlertDecisionEvidence:
    row = db.get(SignalAlertDecisionEvidence, evidence_id)
    if row is None:
        raise EvidenceUnavailableError(
            f"EVIDENCE_UNAVAILABLE: alert decision evidence id={evidence_id}"
        )
    return row


def _setup_base_identity(setup, ticker):
    """New repair proof preserves unknown historical input dimensions."""
    from app.services.calculation_identity import CalculationIdentity

    payload = setup.calculation_identity_json or {}
    if "schema_version" in payload:
        # A claimed typed identity must validate; corruption is never legacy.
        return CalculationIdentity.from_canonical_payload(payload)
    return CalculationIdentity.legacy_unknown(run_id=setup.run_id, ticker=ticker)
