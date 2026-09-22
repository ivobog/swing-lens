from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import BigInteger, create_engine, event, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleTransitionEvidence,
    SignalAlertDecisionEvidence,
    SignalAlertRuleEvidence,
    UploadRun,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.contextual_calculation_identity import (
    build_contextual_result_identity,
    consumer_context_identity,
)
from app.services.contextual_effective_configuration import (
    resolve_regime_configuration,
    resolve_sector_configuration,
)
from app.services.core_effective_configuration import (
    resolve_combined_configuration,
    resolve_ranking_configuration,
    resolve_technical_configuration,
)
from app.services.decision_effective_configuration import (
    resolve_alert_configuration,
    resolve_setup_configuration,
)
from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.original_context_reconstruction import (
    AuthorityDimension,
    ReconstructionMode,
    ReconstructionStatus,
)
from app.services.producer_readiness import READINESS_PAYLOAD_KEY, normalize_producer_readiness
from app.services.ranking_profile_config import get_ranking_profile
from app.services.setup_lifecycle.original_context_reconstruction import (
    ReconstructionTarget,
    ReconstructionTargetKind,
    SetupOperationMode,
    classify_operation_mode,
    resolve_alert_original_context,
    resolve_lifecycle_evaluation_original_context,
    resolve_lifecycle_transition_original_context,
    resolve_original_contexts,
    resolve_setup_original_context,
)
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@compiles(JSONB, "sqlite")
def _compile_jsonb(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@pytest.fixture(scope="module")
def configurations():
    from app.services.combined_decision import _load_scoring_config

    setup = resolve_setup_configuration()
    rule = SimpleNamespace(
        id=1,
        rule_id="READY_ALERT",
        enabled=True,
        severity="INFO",
        scope="lifecycle",
        setup_family="BREAKOUT",
        cooldown_sessions=2,
        minimum_confidence=50,
        config_version="R1",
        condition_json={"event_type": "STATE_TRANSITION"},
        market_restrictions_json={},
    )
    return {
        "TECHNICAL": resolve_technical_configuration(
            pine=load_pine_defaults(),
            v4=load_technical_scoring_v4_config(),
            v5=load_technical_scoring_v5_config(),
            settings=Settings(_env_file=None),
        ),
        "COMBINED": resolve_combined_configuration(),
        "RANKING": resolve_ranking_configuration(
            get_ranking_profile("momentum_swing"), _load_scoring_config()
        ),
        "REGIME": resolve_regime_configuration(),
        "SECTOR": resolve_sector_configuration(),
        "SETUP": setup,
        "LIFECYCLE": setup,
        "ALERT": resolve_alert_configuration(rules=(rule,)),
    }


@pytest.fixture
def evidence_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        CoreCalculationEvidence.__table__,
        CoreCalculationEvidenceSource.__table__,
        SetupLifecycleEvaluationEvidence.__table__,
        SetupLifecycleTransitionEvidence.__table__,
        SignalAlertRuleEvidence.__table__,
        SignalAlertDecisionEvidence.__table__,
    ):
        table.create(engine)
    with Session(engine) as db:
        yield db
        db.rollback()
    engine.dispose()


def _cutoff(day: int) -> MarketCalculationCutoff:
    session = date(2026, 9, day)
    cutoff_at = datetime(2026, 9, day, 20, tzinfo=UTC)
    return MarketCalculationCutoff(
        cutoff_at=cutoff_at,
        exchange_timezone="America/New_York",
        latest_completed_session=session,
        daily_bar_ready_at=cutoff_at,
        calendar_version="XNYS-2026a",
        bar_readiness_version="daily-close-v1",
        cutoff_reason="T16B_TEST",
    )


def _identity(kind, configuration, *, day: int, ordinal: int):
    cutoff = _cutoff(day)
    base = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=1,
        pipeline_id=1,
        ticker="ACME",
    )
    namespace = {
        "TECHNICAL": "core.technical",
        "COMBINED": "core.combined",
        "RANKING": "core.ranking",
        "REGIME": "contextual.regime",
        "SECTOR": "contextual.sector",
        "SETUP": "decision.setup",
        "LIFECYCLE": "decision.setup",
        "ALERT": "decision.alerts.setup",
    }[kind]
    built = build_contextual_result_identity(
        base=base,
        namespace=namespace,
        config_hash=configuration.snapshot.semantic_hash,
        calculation_version=f"{kind.lower()}-v1",
        engine_version=f"{kind.lower()}-engine-v1",
        source_artifacts=(),
        source_payload={"ordinal": ordinal, "kind": kind},
    )
    return configuration.bind(built)


def _core(
    db,
    configurations,
    kind: str,
    *,
    day: int,
    ordinal: int,
    sources=None,
    include_configuration: bool = True,
):
    configuration = configurations[kind]
    identity = _identity(kind, configuration, day=day, ordinal=ordinal)
    source_ids = dict(sources or {})
    payload = {
        "ordinal": ordinal,
        "kind": kind,
        "technical_history_insufficient": kind == "TECHNICAL",
    }
    if include_configuration:
        payload[CONFIGURATION_PAYLOAD_KEY] = configuration.snapshot.as_dict()
    payload[READINESS_PAYLOAD_KEY] = normalize_producer_readiness(
        kind,
        payload,
        identity_fingerprint=str(identity.fingerprint()),
        calculation_versions={"engine_version": f"{kind.lower()}-engine-v1"},
        evaluated_at=_cutoff(day).cutoff_at,
        business_anchor=_cutoff(day).latest_completed_session,
    ).canonical_payload()
    payload = Canonical.canonicalize(payload)
    row = CoreCalculationEvidence(
        artifact_kind=kind,
        run_id=1,
        ticker="ACME",
        ranking_profile="momentum_swing" if kind == "RANKING" else None,
        calculation_identity_fingerprint=str(identity.fingerprint()),
        calculation_identity_json=identity.canonical_payload(),
        payload_fingerprint=Canonical.fingerprint(payload),
        payload_json=payload,
        source_evidence_ids_json=source_ids,
        evidence_key=Canonical.fingerprint(
            {
                "artifact_kind": kind,
                "calculation_identity_fingerprint": str(identity.fingerprint()),
                "payload_fingerprint": Canonical.fingerprint(payload),
                "source_evidence_ids": source_ids,
            }
        ),
        calculated_at=_cutoff(day).cutoff_at,
    )
    db.add(row)
    db.flush()
    for role, source_id in source_ids.items():
        db.add(
            CoreCalculationEvidenceSource(
                evidence_id=row.id,
                source_role=role,
                source_evidence_id=source_id,
            )
        )
    db.flush()
    return row


def _setup_graph(db, configurations, *, day: int = 15, ordinal: int = 1):
    sources = {
        role: _core(db, configurations, kind, day=day, ordinal=ordinal)
        for role, kind in (
            ("technical", "TECHNICAL"),
            ("combined", "COMBINED"),
            ("ranking_metadata", "RANKING"),
            ("regime", "REGIME"),
            ("sector", "SECTOR"),
        )
    }
    setup = _core(
        db,
        configurations,
        "SETUP",
        day=day,
        ordinal=ordinal,
        sources={role: row.id for role, row in sources.items()},
    )
    return setup, sources


def _evaluation(
    db,
    configurations,
    setup,
    *,
    day: int,
    ordinal: int,
    prior_evaluation=None,
    prior_transition=None,
):
    configuration = configurations["LIFECYCLE"]
    identity = _identity("LIFECYCLE", configuration, day=day, ordinal=ordinal)
    payload = Canonical.canonicalize(
        {
            "setup_evidence_id": setup.id,
            "prior_evaluation_evidence_id": getattr(prior_evaluation, "id", None),
            "prior_transition_evidence_id": getattr(prior_transition, "id", None),
            "ticker": "ACME",
            "timeframe": "1d",
            "setup_family": "BREAKOUT",
            "decision_session": date(2026, 9, day),
            "calculation_cutoff_at": _cutoff(day).cutoff_at,
            "calendar_version": _cutoff(day).calendar_version,
            "calculation_identity_fingerprint": str(identity.fingerprint()),
            "calculation_identity": identity.canonical_payload(),
            "execution_mode": "DIRECT",
            "previous_state": getattr(prior_evaluation, "output_state", None),
            "output_state": "READY",
            "output_phase": "READY",
            "transition_eligible": True,
            "engine_version": "lifecycle-engine-v1",
            "config_version": "C1",
            "config_hash": configuration.snapshot.semantic_hash,
            "counters": {"ordinal": ordinal},
            "reasons": ["READY"],
            "warnings": [],
            CONFIGURATION_PAYLOAD_KEY: configuration.snapshot.as_dict(),
            READINESS_PAYLOAD_KEY: normalize_producer_readiness(
                "LIFECYCLE",
                {"confidence_score": 90},
                identity_fingerprint=str(identity.fingerprint()),
                calculation_versions={"engine_version": "lifecycle-engine-v1"},
                evaluated_at=_cutoff(day).cutoff_at,
                business_anchor=date(2026, 9, day),
            ).canonical_payload(),
        }
    )
    fingerprint = Canonical.fingerprint(payload)
    row = SetupLifecycleEvaluationEvidence(
        setup_evidence_id=setup.id,
        prior_evaluation_evidence_id=getattr(prior_evaluation, "id", None),
        prior_transition_evidence_id=getattr(prior_transition, "id", None),
        ticker="ACME",
        timeframe="1d",
        setup_family="BREAKOUT",
        decision_session=date(2026, 9, day),
        calculation_cutoff_at=_cutoff(day).cutoff_at,
        calendar_version=_cutoff(day).calendar_version,
        calculation_identity_fingerprint=str(identity.fingerprint()),
        execution_mode="DIRECT",
        previous_state=getattr(prior_evaluation, "output_state", None),
        output_state="READY",
        output_phase="READY",
        transition_eligible=True,
        engine_version="lifecycle-engine-v1",
        config_version="C1",
        config_hash=configuration.snapshot.semantic_hash,
        counters_json={"ordinal": ordinal},
        reasons_json=["READY"],
        warnings_json=[],
        payload_json=payload,
        payload_fingerprint=fingerprint,
        evidence_key=Canonical.fingerprint(
            {
                "contract": "setup-lifecycle-evaluation-evidence-v1",
                "payload_fingerprint": fingerprint,
            }
        ),
    )
    db.add(row)
    db.flush()
    return row


def _transition(db, evaluation, *, prior=None):
    payload = Canonical.canonicalize(
        {
            "evaluation_evidence_id": evaluation.id,
            "setup_evidence_id": evaluation.setup_evidence_id,
            "prior_transition_evidence_id": getattr(prior, "id", None),
            "ticker": "ACME",
            "timeframe": "1d",
            "setup_family": "BREAKOUT",
            "effective_session": evaluation.decision_session,
            "event_type": "STATE_TRANSITION",
            "from_state": getattr(prior, "to_state", None),
            "to_state": "READY",
            "from_phase": getattr(prior, "to_phase", None),
            "to_phase": "READY",
            "config_hash": evaluation.config_hash,
            "reasons": ["READY"],
        }
    )
    fingerprint = Canonical.fingerprint(payload)
    row = SetupLifecycleTransitionEvidence(
        evaluation_evidence_id=evaluation.id,
        setup_evidence_id=evaluation.setup_evidence_id,
        prior_transition_evidence_id=getattr(prior, "id", None),
        ticker="ACME",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_session=evaluation.decision_session,
        event_type="STATE_TRANSITION",
        from_state=getattr(prior, "to_state", None),
        to_state="READY",
        from_phase=getattr(prior, "to_phase", None),
        to_phase="READY",
        config_hash=evaluation.config_hash,
        reasons_json=["READY"],
        payload_json=payload,
        payload_fingerprint=fingerprint,
        evidence_key=Canonical.fingerprint(
            {"contract": "setup-lifecycle-transition-evidence-v1", "payload": fingerprint}
        ),
    )
    db.add(row)
    db.flush()
    return row


def _alert(db, configurations, evaluation, transition, *, day: int, ordinal: int, prior=None):
    rule_payload = Canonical.canonicalize(
        {
            "rule_id": "READY_ALERT",
            "enabled": True,
            "severity": "INFO",
            "scope": "lifecycle",
            "setup_family": "BREAKOUT",
            "cooldown_sessions": 2,
            "minimum_confidence": 50,
            "config_version": "R1",
            "condition": {"event_type": "STATE_TRANSITION"},
            "market_restrictions": {},
            "metadata": {},
        }
    )
    rule_fingerprint = Canonical.fingerprint(rule_payload)
    rule_key = Canonical.fingerprint(
        {"contract": "signal-alert-rule-evidence-v1", "payload": rule_fingerprint}
    )
    rule = db.scalar(
        select(SignalAlertRuleEvidence).where(SignalAlertRuleEvidence.evidence_key == rule_key)
    )
    if rule is None:
        rule = SignalAlertRuleEvidence(
            rule_id="READY_ALERT",
            rule_row_id=None,
            config_version="R1",
            payload_json=rule_payload,
            payload_fingerprint=rule_fingerprint,
            evidence_key=rule_key,
        )
        db.add(rule)
        db.flush()
    configuration = configurations["ALERT"]
    identity = _identity("ALERT", configuration, day=day, ordinal=ordinal)
    payload = Canonical.canonicalize(
        {
            "rule_evidence_id": rule.id,
            "setup_evidence_id": evaluation.setup_evidence_id,
            "lifecycle_evaluation_evidence_id": evaluation.id,
            "lifecycle_transition_evidence_id": transition.id,
            "cooldown_predecessor_evidence_id": getattr(prior, "id", None),
            "dedup_predecessor_evidence_id": None,
            "ticker": "ACME",
            "timeframe": "1d",
            "effective_session": date(2026, 9, day),
            "calculation_cutoff_at": _cutoff(day).cutoff_at,
            "calendar_version": _cutoff(day).calendar_version,
            "source_event_key": f"transition-{transition.id}",
            "semantic_key": "READY_ALERT:ACME",
            "decision": "GENERATED",
            "reasons": ["READY_ALERT"],
            "decision_payload": {"ordinal": ordinal},
            "calculation_identity": identity.canonical_payload(),
            "calculation_identity_fingerprint": str(identity.fingerprint()),
            "execution_semantics": "CURRENT_CALCULATION",
            CONFIGURATION_PAYLOAD_KEY: configuration.snapshot.as_dict(),
        }
    )
    fingerprint = Canonical.fingerprint(payload)
    row = SignalAlertDecisionEvidence(
        rule_evidence_id=rule.id,
        setup_evidence_id=evaluation.setup_evidence_id,
        lifecycle_evaluation_evidence_id=evaluation.id,
        lifecycle_transition_evidence_id=transition.id,
        cooldown_predecessor_evidence_id=getattr(prior, "id", None),
        dedup_predecessor_evidence_id=None,
        ticker="ACME",
        timeframe="1d",
        effective_session=date(2026, 9, day),
        calculation_cutoff_at=_cutoff(day).cutoff_at,
        calendar_version=_cutoff(day).calendar_version,
        source_event_key=f"transition-{transition.id}",
        semantic_key="READY_ALERT:ACME",
        decision="GENERATED",
        reasons_json=["READY_ALERT"],
        payload_json=payload,
        payload_fingerprint=fingerprint,
        evidence_key=Canonical.fingerprint(
            {"contract": "signal-alert-decision-evidence-v1", "payload": fingerprint}
        ),
    )
    db.add(row)
    db.flush()
    return row


def test_exact_setup_uses_historical_technical_readiness_and_no_pricebars(
    evidence_db, configurations
):
    setup, sources = _setup_graph(evidence_db, configurations)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(evidence_db.bind, "before_cursor_execute", capture)
    before = (set(evidence_db.new), set(evidence_db.dirty), set(evidence_db.deleted))
    resolved = resolve_setup_original_context(evidence_db, setup.id)
    after = (set(evidence_db.new), set(evidence_db.dirty), set(evidence_db.deleted))
    event.remove(evidence_db.bind, "before_cursor_execute", capture)
    assert resolved.authorization.authorized, [item.reason for item in resolved.manifest.authority]
    assert resolved.manifest.status is ReconstructionStatus.EXACT
    assert sources["technical"].payload_json["technical_history_insufficient"] is True
    assert before == after
    assert not any("price_bars" in statement for statement in statements)


def test_setup_missing_technical_and_current_substitution_fail_closed(evidence_db, configurations):
    _complete, sources = _setup_graph(evidence_db, configurations, ordinal=1)
    old = _core(
        evidence_db,
        configurations,
        "SETUP",
        day=15,
        ordinal=2,
        sources={role: row.id for role, row in sources.items() if role != "technical"},
    )
    current = _core(
        evidence_db,
        configurations,
        "TECHNICAL",
        day=16,
        ordinal=200,
    )
    resolved = resolve_setup_original_context(evidence_db, old.id)
    assert not resolved.authorization.authorized
    assert resolved.result.result_fingerprint is None
    assert current.id != sources["technical"].id


def test_missing_historical_config_cannot_be_replaced_by_current_config(
    evidence_db, configurations
):
    _complete, sources = _setup_graph(evidence_db, configurations, ordinal=1)
    legacy = _core(
        evidence_db,
        configurations,
        "SETUP",
        day=15,
        ordinal=2,
        sources={role: row.id for role, row in sources.items()},
        include_configuration=False,
    )
    resolved = resolve_setup_original_context(evidence_db, legacy.id)
    assert resolved.manifest.status is ReconstructionStatus.PERMANENTLY_UNAVAILABLE
    assert not resolved.authorization.authorized


def test_s1_s2_s3_lifecycle_chain_and_alert_rule_cooldown_are_exact(evidence_db, configurations):
    setup, _ = _setup_graph(evidence_db, configurations)
    e1 = _evaluation(evidence_db, configurations, setup, day=15, ordinal=1)
    t1 = _transition(evidence_db, e1)
    e2 = _evaluation(
        evidence_db,
        configurations,
        setup,
        day=16,
        ordinal=2,
        prior_evaluation=e1,
        prior_transition=t1,
    )
    t2 = _transition(evidence_db, e2, prior=t1)
    e3 = _evaluation(
        evidence_db,
        configurations,
        setup,
        day=17,
        ordinal=3,
        prior_evaluation=e2,
        prior_transition=t2,
    )
    t3 = _transition(evidence_db, e3, prior=t2)
    a1 = _alert(evidence_db, configurations, e2, t2, day=16, ordinal=1)
    a2 = _alert(evidence_db, configurations, e3, t3, day=17, ordinal=2, prior=a1)
    evidence_db.flush()

    evaluation_result = resolve_lifecycle_evaluation_original_context(evidence_db, e2.id)
    transition_result = resolve_lifecycle_transition_original_context(evidence_db, t2.id)
    alert_result = resolve_alert_original_context(evidence_db, a2.id)
    assert all(
        result.authorization.authorized
        for result in (evaluation_result, transition_result, alert_result)
    ), [
        (result.target.kind.value, [item.reason for item in result.manifest.authority])
        for result in (evaluation_result, transition_result, alert_result)
    ]
    evaluation_predecessor = next(
        item
        for item in evaluation_result.manifest.authority
        if item.dimension is AuthorityDimension.PREDECESSOR_STATE
    )
    transition_predecessor = next(
        item
        for item in transition_result.manifest.authority
        if item.dimension is AuthorityDimension.PREDECESSOR_STATE
    )
    assert any(
        reference.reference_id.startswith(f"{e1.id}:")
        for reference in evaluation_predecessor.references
    )
    assert any(
        reference.reference_id.startswith(f"{t1.id}:")
        for reference in evaluation_predecessor.references
    )
    assert all(
        not reference.reference_id.startswith(f"{e3.id}:")
        and not reference.reference_id.startswith(f"{t3.id}:")
        for reference in evaluation_predecessor.references
    )
    assert any(
        reference.reference_id.startswith(f"{t1.id}:")
        for reference in transition_predecessor.references
    )
    predecessor = next(
        item
        for item in alert_result.manifest.authority
        if item.dimension is AuthorityDimension.PREDECESSOR_STATE
    )
    assert any(
        reference.reference_id.startswith(f"{a1.id}:") for reference in predecessor.references
    )


def test_missing_predecessor_and_future_alert_predecessor_are_unavailable(
    evidence_db, configurations
):
    setup, _ = _setup_graph(evidence_db, configurations)
    evaluation = _evaluation(evidence_db, configurations, setup, day=15, ordinal=1)
    transition = _transition(evidence_db, evaluation)
    later_evaluation = _evaluation(
        evidence_db,
        configurations,
        setup,
        day=16,
        ordinal=2,
        prior_evaluation=evaluation,
        prior_transition=transition,
    )
    alert = _alert(evidence_db, configurations, evaluation, transition, day=15, ordinal=1)
    broken_evaluation_payload = {
        **later_evaluation.payload_json,
        "prior_evaluation_evidence_id": 999999,
    }
    broken_evaluation_fingerprint = Canonical.fingerprint(broken_evaluation_payload)
    evidence_db.execute(
        update(SetupLifecycleEvaluationEvidence)
        .where(SetupLifecycleEvaluationEvidence.id == later_evaluation.id)
        .values(
            prior_evaluation_evidence_id=999999,
            payload_json=broken_evaluation_payload,
            payload_fingerprint=broken_evaluation_fingerprint,
            evidence_key=Canonical.fingerprint(
                {
                    "contract": "setup-lifecycle-evaluation-evidence-v1",
                    "payload_fingerprint": broken_evaluation_fingerprint,
                }
            ),
        )
    )
    corrupted = {**alert.payload_json, "cooldown_predecessor_evidence_id": 999999}
    corrupted_fingerprint = Canonical.fingerprint(corrupted)
    evidence_db.execute(
        update(SignalAlertDecisionEvidence)
        .where(SignalAlertDecisionEvidence.id == alert.id)
        .values(
            cooldown_predecessor_evidence_id=999999,
            payload_json=corrupted,
            payload_fingerprint=corrupted_fingerprint,
            evidence_key=Canonical.fingerprint(
                {
                    "contract": "signal-alert-decision-evidence-v1",
                    "payload": corrupted_fingerprint,
                }
            ),
        )
    )
    evidence_db.expire_all()
    lifecycle = resolve_lifecycle_evaluation_original_context(evidence_db, later_evaluation.id)
    resolved = resolve_alert_original_context(evidence_db, alert.id)
    assert lifecycle.manifest.status is ReconstructionStatus.PERMANENTLY_UNAVAILABLE
    assert lifecycle.reconstructed_output is None
    assert resolved.manifest.status is ReconstructionStatus.PERMANENTLY_UNAVAILABLE
    assert resolved.reconstructed_output is None


def test_rule_payload_tamper_cannot_fall_back_to_mutable_rule(evidence_db, configurations):
    setup, _ = _setup_graph(evidence_db, configurations)
    evaluation = _evaluation(evidence_db, configurations, setup, day=15, ordinal=1)
    transition = _transition(evidence_db, evaluation)
    alert = _alert(evidence_db, configurations, evaluation, transition, day=15, ordinal=1)
    rule = evidence_db.get(SignalAlertRuleEvidence, alert.rule_evidence_id)
    evidence_db.execute(
        update(SignalAlertRuleEvidence)
        .where(SignalAlertRuleEvidence.id == rule.id)
        .values(payload_json={**rule.payload_json, "cooldown_sessions": 99})
    )
    evidence_db.expire_all()
    resolved = resolve_alert_original_context(evidence_db, alert.id)
    assert not resolved.authorization.authorized
    assert resolved.result.comparison.status.value == "INSUFFICIENT_AUTHORITY"


def test_batch_query_count_is_target_count_invariant(evidence_db, configurations):
    first, sources = _setup_graph(evidence_db, configurations)
    source_ids = {role: row.id for role, row in sources.items()}
    setups = [first]
    for ordinal in range(2, 201):
        payload = {**first.payload_json, "ordinal": ordinal}
        fingerprint = Canonical.fingerprint(payload)
        row = CoreCalculationEvidence(
            artifact_kind="SETUP",
            run_id=first.run_id,
            ticker=first.ticker,
            calculation_identity_fingerprint=first.calculation_identity_fingerprint,
            calculation_identity_json=first.calculation_identity_json,
            payload_fingerprint=fingerprint,
            payload_json=payload,
            source_evidence_ids_json=source_ids,
            evidence_key=Canonical.fingerprint(
                {
                    "artifact_kind": "SETUP",
                    "calculation_identity_fingerprint": first.calculation_identity_fingerprint,
                    "payload_fingerprint": fingerprint,
                    "source_evidence_ids": source_ids,
                }
            ),
            calculated_at=first.calculated_at,
        )
        evidence_db.add(row)
        setups.append(row)
    evidence_db.flush()
    evidence_db.add_all(
        [
            CoreCalculationEvidenceSource(
                evidence_id=row.id,
                source_role=role,
                source_evidence_id=source_id,
            )
            for row in setups[1:]
            for role, source_id in source_ids.items()
        ]
    )
    evidence_db.flush()

    counts = {}
    for size in (1, 50, 200):
        count = 0

        def capture(*_args):
            nonlocal count
            count += 1

        event.listen(evidence_db.bind, "before_cursor_execute", capture)
        results = resolve_original_contexts(
            evidence_db,
            tuple(
                ReconstructionTarget(ReconstructionTargetKind.SETUP, row.id)
                for row in setups[:size]
            ),
        )
        event.remove(evidence_db.bind, "before_cursor_execute", capture)
        assert len(results) == size
        assert all(result.authorization.authorized for result in results)
        counts[size] = count
    assert counts[1] == counts[50] == counts[200]
    assert counts[200] <= 4


def test_operation_modes_are_explicit_and_never_alias_reconstruction():
    assert classify_operation_mode(ReconstructionMode.ORIGINAL_CONTEXT) is (
        SetupOperationMode.ORIGINAL_CONTEXT_RECONSTRUCTION
    )
    assert classify_operation_mode("REPLAY") is SetupOperationMode.CURRENT_RULES_RETROSPECTIVE
    assert classify_operation_mode("MAINTENANCE") is SetupOperationMode.CURRENT_STATE_REPAIR
    assert (
        len(
            {
                SetupOperationMode.ORIGINAL_CONTEXT_RECONSTRUCTION.value,
                SetupOperationMode.CURRENT_RULES_RETROSPECTIVE.value,
                SetupOperationMode.CURRENT_STATE_REPAIR.value,
            }
        )
        == 3
    )


def test_postgresql_native_setup_lifecycle_alert_chain_is_exact_and_read_only(
    disposable_postgres_database: str, configurations
):
    alembic = Config("alembic.ini")
    alembic.attributes["database_url"] = disposable_postgres_database
    command.upgrade(alembic, "head")
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="t16b-native.csv", status="COMPLETED"))
        db.flush()
        setup, _ = _setup_graph(db, configurations)
        evaluation = _evaluation(db, configurations, setup, day=15, ordinal=1)
        transition = _transition(db, evaluation)
        alert = _alert(db, configurations, evaluation, transition, day=15, ordinal=1)
        db.commit()
        before = (frozenset(db.new), frozenset(db.dirty), frozenset(db.deleted))
        results = (
            resolve_setup_original_context(db, setup.id),
            resolve_lifecycle_evaluation_original_context(db, evaluation.id),
            resolve_lifecycle_transition_original_context(db, transition.id),
            resolve_alert_original_context(db, alert.id),
        )
        after = (frozenset(db.new), frozenset(db.dirty), frozenset(db.deleted))
        assert all(result.authorization.authorized for result in results)
        assert before == after
    engine.dispose()
