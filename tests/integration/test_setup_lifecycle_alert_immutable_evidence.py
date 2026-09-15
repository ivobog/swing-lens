from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, create_engine, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupLifecycleTransitionEvidence,
    SetupSignalSnapshot,
    SignalAlertDecisionEvidence,
    SignalAlertEvent,
    SignalAlertRule,
    SignalAlertRuleEvidence,
    UploadRun,
)
from app.services.calculation_identity import (
    CalculationIdentity,
    IdentityDimension,
    VersionIdentity,
)
from app.services.combined_ranking_identity import embed_calculation_identity
from app.services.core_calculation_evidence import CoreEvidenceKind, get_current_evidence
from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
from app.services.setup_lifecycle.decision_evidence import (
    get_setup_evidence,
    persist_alert_decision_evidence,
    persist_lifecycle_evaluation_evidence,
    persist_lifecycle_transition_evidence,
    persist_setup_evidence,
    prior_generated_alert_decision,
)
from app.services.setup_lifecycle.dtos import ActionabilityDecision, LifecycleDecision
from app.services.setup_lifecycle.enums import (
    Actionability,
    ConfidenceLabel,
    LifecycleState,
    SetupFamily,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@compiles(JSONB, "sqlite")
def _compile_jsonb(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@pytest.fixture
def evidence_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        UploadRun.__table__,
        CoreCalculationEvidence.__table__,
        CoreCalculationEvidenceSource.__table__,
        CoreCalculationCurrentProjection.__table__,
        SetupLifecycleEvaluationRun.__table__,
        SetupLifecycleEvaluationEvidence.__table__,
        SetupLifecycleTransitionEvidence.__table__,
        SetupLifecycleEpisode.__table__,
        SetupLifecycleEvent.__table__,
        SignalAlertRule.__table__,
        SignalAlertRuleEvidence.__table__,
        SignalAlertDecisionEvidence.__table__,
        SignalAlertEvent.__table__,
    ):
        table.create(engine)
    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="t11c.csv", status="COMPLETED"))
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def test_0079_migration_compiles_complete_postgresql_evidence_graph() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260915_0079_setup_lifecycle_alert_evidence.py"
    )
    spec = spec_from_file_location("t11c_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    ddl = output.getvalue()
    assert "CREATE TABLE setup_lifecycle_evaluation_evidence" in ddl
    assert "CREATE TABLE setup_lifecycle_transition_evidence" in ddl
    assert "CREATE TABLE signal_alert_rule_evidence" in ddl
    assert "CREATE TABLE signal_alert_decision_evidence" in ddl
    assert "ON DELETE RESTRICT" in ddl


def test_0079_phase2_chain_round_trips_on_disposable_postgresql(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    schema = inspect(engine)
    assert {
        "setup_lifecycle_evaluation_evidence",
        "setup_lifecycle_transition_evidence",
        "signal_alert_rule_evidence",
        "signal_alert_decision_evidence",
    } <= set(schema.get_table_names())
    assert "evidence_id" in {
        column["name"] for column in schema.get_columns("setup_signal_snapshots")
    }
    assert "decision_evidence_id" in {
        column["name"] for column in schema.get_columns("signal_alert_events")
    }
    engine.dispose()


def test_setup_evidence_pins_upstream_and_projection_moves_without_rewriting(evidence_db) -> None:
    db = evidence_db
    sources = {
        kind: _core(db, kind=kind, ticker="ACME", suffix=kind)
        for kind in ("FUNDAMENTAL", "TECHNICAL", "COMBINED", "RANKING", "REGIME", "SECTOR")
    }
    identity1 = _identity("setup-v1")
    snapshot1 = _snapshot(
        identity1,
        source_evidence={kind.lower(): row.id for kind, row in sources.items()},
        source_hash="source-v1",
    )
    setup1 = persist_setup_evidence(db, snapshot1)
    assert setup1 is not None
    original_payload = deepcopy(setup1.payload_json)
    pinned = {
        row.source_role: row.source_evidence_id
        for row in db.scalars(
            select(CoreCalculationEvidenceSource).where(
                CoreCalculationEvidenceSource.evidence_id == setup1.id
            )
        )
    }
    assert pinned == {
        "fundamental": sources["FUNDAMENTAL"].id,
        "technical": sources["TECHNICAL"].id,
        "combined": sources["COMBINED"].id,
        "ranking_metadata": sources["RANKING"].id,
        "regime": sources["REGIME"].id,
        "sector": sources["SECTOR"].id,
    }

    identity2 = _identity("setup-v2")
    snapshot2 = _snapshot(
        identity2,
        source_evidence={kind.lower(): row.id for kind, row in sources.items()},
        source_hash="source-v2",
    )
    setup2 = persist_setup_evidence(db, snapshot2)
    assert setup2 is not None and setup2.id != setup1.id
    assert setup1.payload_json == original_payload
    assert get_setup_evidence(db, setup1.id).id == setup1.id
    assert (
        get_current_evidence(db, kind=CoreEvidenceKind.SETUP, run_id=1, ticker="ACME").id
        == setup2.id
    )

    setup1_id = setup1.id
    db.commit()
    setup1.payload_json = {"rewritten": True}
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
        db.flush()
    db.rollback()
    assert get_setup_evidence(db, setup1_id).payload_json == original_payload


def test_lifecycle_evaluations_and_transitions_form_immutable_projection_chain(evidence_db) -> None:
    db = evidence_db
    setup1 = _core(db, kind="SETUP", ticker="ACME", suffix="s1")
    setup2 = _core(db, kind="SETUP", ticker="ACME", suffix="s2")
    snapshot1 = _evidence_snapshot(setup1.id, date(2026, 9, 10))
    evaluation1 = persist_lifecycle_evaluation_evidence(
        db,
        snapshot=snapshot1,
        episode=None,
        decision=_decision(LifecycleState.DEVELOPING, previous=None),
        actionability=_actionability(),
        evaluation_run_id=None,
        transition_eligible=True,
    )
    assert evaluation1 is not None
    transition1 = persist_lifecycle_transition_evidence(
        db,
        event=_event(
            effective=date(2026, 9, 10),
            from_state=None,
            to_state=LifecycleState.DEVELOPING,
        ),
        evaluation=evaluation1,
        prior_transition_evidence_id=None,
    )
    assert transition1 is not None
    episode = _episode(evaluation1.id, transition1.id, date(2026, 9, 10))
    db.add(episode)
    db.flush()

    snapshot2 = _evidence_snapshot(setup2.id, date(2026, 9, 11))
    evaluation2 = persist_lifecycle_evaluation_evidence(
        db,
        snapshot=snapshot2,
        episode=episode,
        decision=_decision(LifecycleState.READY, previous=LifecycleState.DEVELOPING),
        actionability=_actionability(),
        evaluation_run_id=None,
        transition_eligible=True,
    )
    assert evaluation2 is not None
    transition2 = persist_lifecycle_transition_evidence(
        db,
        event=_event(
            effective=date(2026, 9, 11),
            from_state=LifecycleState.DEVELOPING,
            to_state=LifecycleState.READY,
        ),
        evaluation=evaluation2,
        prior_transition_evidence_id=transition1.id,
    )
    assert transition2 is not None
    episode.current_state = LifecycleState.READY.value
    episode.current_phase = LifecycleState.READY.value
    episode.current_as_of_date = date(2026, 9, 11)
    episode.latest_evaluation_evidence_id = evaluation2.id
    episode.latest_transition_evidence_id = transition2.id
    db.flush()

    assert evaluation2.prior_evaluation_evidence_id == evaluation1.id
    assert evaluation2.prior_transition_evidence_id == transition1.id
    assert transition2.prior_transition_evidence_id == transition1.id
    assert transition1.to_state == LifecycleState.DEVELOPING.value
    assert episode.latest_transition_evidence_id == transition2.id

    same_state = persist_lifecycle_evaluation_evidence(
        db,
        snapshot=_evidence_snapshot(setup1.id, date(2026, 9, 12)),
        episode=episode,
        decision=_decision(LifecycleState.READY, previous=LifecycleState.READY),
        actionability=_actionability(),
        evaluation_run_id=None,
        transition_eligible=False,
    )
    assert same_state is not None and same_state.id != evaluation2.id
    episode.latest_evaluation_evidence_id = same_state.id
    db.flush()
    retry = persist_lifecycle_evaluation_evidence(
        db,
        snapshot=_evidence_snapshot(setup1.id, date(2026, 9, 12)),
        episode=episode,
        decision=_decision(LifecycleState.READY, previous=LifecycleState.READY),
        actionability=_actionability(),
        evaluation_run_id=None,
        transition_eligible=False,
    )
    assert retry.id == same_state.id

    transition1_id = transition1.id
    db.commit()
    transition1.payload_json = {"rewrite": True}
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
        db.flush()
    db.rollback()
    assert db.get(SetupLifecycleTransitionEvidence, transition1_id).to_state == "DEVELOPING"


def test_older_repair_fails_closed_and_replay_evidence_is_separate(evidence_db) -> None:
    db = evidence_db
    setup = _core(db, kind="SETUP", ticker="ACME", suffix="repair")
    original = persist_lifecycle_evaluation_evidence(
        db,
        snapshot=_evidence_snapshot(setup.id, date(2026, 9, 12)),
        episode=None,
        decision=_decision(LifecycleState.READY, previous=None),
        actionability=_actionability(),
        evaluation_run_id=None,
        transition_eligible=True,
    )
    assert original is not None
    episode = _episode(original.id, None, date(2026, 9, 12))
    episode.current_state = LifecycleState.READY.value
    db.add(episode)
    db.flush()
    with pytest.raises(ValueError, match="newer active episode"):
        SetupLifecycleRepository().active_episode_for_update(
            db,
            ticker="ACME",
            timeframe="1d",
            setup_family="BREAKOUT",
            as_of_date=date(2026, 9, 11),
            lock=False,
        )

    replay_run = SetupLifecycleEvaluationRun(
        mode="REPLAY",
        status="RUNNING",
        engine_version="slse-test",
        config_version="cfg-v2",
        config_hash="cfg-v2-hash",
        dry_run=False,
    )
    db.add(replay_run)
    db.flush()
    replay = persist_lifecycle_evaluation_evidence(
        db,
        snapshot=_evidence_snapshot(setup.id, date(2026, 9, 12)),
        episode=None,
        decision=_decision(LifecycleState.DEVELOPING, previous=None),
        actionability=_actionability(),
        evaluation_run_id=replay_run.id,
        transition_eligible=False,
    )
    assert replay is not None and replay.id != original.id
    assert replay.execution_mode == "REPLAY"
    assert db.get(SetupLifecycleEvaluationEvidence, original.id).output_state == "READY"
    assert episode.latest_evaluation_evidence_id == original.id


def test_rule_change_cooldown_dedup_and_suppression_are_exact_and_bounded(evidence_db) -> None:
    db = evidence_db
    setup = _core(db, kind="SETUP", ticker="ACME", suffix="alert")
    evaluation = persist_lifecycle_evaluation_evidence(
        db,
        snapshot=_evidence_snapshot(setup.id, date(2026, 9, 1)),
        episode=None,
        decision=_decision(LifecycleState.READY, previous=LifecycleState.DEVELOPING),
        actionability=_actionability(),
        evaluation_run_id=None,
        transition_eligible=True,
    )
    assert evaluation is not None
    transition = persist_lifecycle_transition_evidence(
        db,
        event=_event(
            effective=date(2026, 9, 1),
            from_state=LifecycleState.DEVELOPING,
            to_state=LifecycleState.READY,
        ),
        evaluation=evaluation,
        prior_transition_evidence_id=None,
    )
    assert transition is not None
    lifecycle_event = _event(
        effective=date(2026, 9, 1),
        from_state=LifecycleState.DEVELOPING,
        to_state=LifecycleState.READY,
    )
    lifecycle_event.transition_evidence_id = transition.id
    db.add(lifecycle_event)
    rule = SignalAlertRule(
        rule_id="NEW_READY",
        enabled=True,
        severity="ACTIONABLE",
        scope="lifecycle_transition",
        setup_family="BREAKOUT",
        cooldown_sessions=2,
        minimum_confidence=0,
        config_version="R1",
        condition_json={"to_state": "READY"},
        market_restrictions_json={},
        metadata_json={},
    )
    db.add(rule)
    db.flush()
    service = SetupLifecycleAlertService(repository=SetupLifecycleRepository())

    a1 = service._persist_alert(
        db,
        rule=rule,
        ticker="ACME",
        timeframe="1d",
        effective_date=date(2026, 9, 1),
        source_event_key="a1-source",
        evaluation_run_id=None,
        lifecycle_event_id=lifecycle_event.id,
        episode_id=None,
        source_confidence=90,
        semantic_key="NEW_READY:1:READY:ACTIONABLE",
        reason_codes=("NEW_READY_ALERT",),
        evidence={"source": "lifecycle_event"},
    )
    assert a1.created == 1
    a1_event = db.get(SignalAlertEvent, a1.event_ids[0])
    a1_decision = db.get(SignalAlertDecisionEvidence, a1_event.decision_evidence_id)
    a1_rule = db.get(SignalAlertRuleEvidence, a1_decision.rule_evidence_id)
    assert a1_rule.payload_json["config_version"] == "R1"

    rule.config_version = "R2"
    rule.cooldown_sessions = 5
    db.flush()
    future = persist_alert_decision_evidence(
        db,
        rule=rule,
        ticker="ACME",
        timeframe="1d",
        effective_session=date(2026, 9, 20),
        source_event_key="future-source",
        semantic_key="NEW_READY:1:READY:ACTIONABLE",
        decision="GENERATED",
        reasons=("NEW_READY_ALERT",),
        payload={"future": True},
        setup_evidence_id=setup.id,
        lifecycle_evaluation_evidence_id=evaluation.id,
        lifecycle_transition_evidence_id=transition.id,
    )
    bounded = prior_generated_alert_decision(
        db,
        rule_id="NEW_READY",
        ticker="ACME",
        timeframe="1d",
        semantic_key="NEW_READY:1:READY:ACTIONABLE",
        effective_session=date(2026, 9, 10),
    )
    assert bounded.id == a1_decision.id
    assert bounded.id != future.id

    historical = service._persist_alert(
        db,
        rule=rule,
        ticker="ACME",
        timeframe="1d",
        effective_date=date(2026, 9, 10),
        source_event_key="historical-source",
        evaluation_run_id=None,
        lifecycle_event_id=lifecycle_event.id,
        episode_id=None,
        source_confidence=90,
        semantic_key="NEW_READY:1:READY:ACTIONABLE",
        reason_codes=("NEW_READY_ALERT",),
        evidence={"source": "lifecycle_event"},
    )
    assert historical.created == 1
    historical_event = db.get(SignalAlertEvent, historical.event_ids[0])
    historical_decision = db.get(SignalAlertDecisionEvidence, historical_event.decision_evidence_id)
    assert (
        db.get(SignalAlertRuleEvidence, historical_decision.rule_evidence_id).payload_json[
            "config_version"
        ]
        == "R2"
    )
    assert a1_decision.rule_evidence_id != historical_decision.rule_evidence_id

    duplicate = service._persist_alert(
        db,
        rule=rule,
        ticker="ACME",
        timeframe="1d",
        effective_date=date(2026, 9, 10),
        source_event_key="historical-source",
        evaluation_run_id=None,
        lifecycle_event_id=lifecycle_event.id,
        episode_id=None,
        source_confidence=90,
        semantic_key="NEW_READY:1:READY:ACTIONABLE",
        reason_codes=("NEW_READY_ALERT",),
        evidence={"source": "lifecycle_event"},
    )
    assert duplicate.suppressed == 1
    dedup = db.scalar(
        select(SignalAlertDecisionEvidence).where(
            SignalAlertDecisionEvidence.decision == "SUPPRESSED_DEDUP"
        )
    )
    assert dedup.dedup_predecessor_evidence_id == historical_decision.id

    cooldown = service._persist_alert(
        db,
        rule=rule,
        ticker="ACME",
        timeframe="1d",
        effective_date=date(2026, 9, 11),
        source_event_key="cooldown-source",
        evaluation_run_id=None,
        lifecycle_event_id=lifecycle_event.id,
        episode_id=None,
        source_confidence=90,
        semantic_key="NEW_READY:1:READY:ACTIONABLE",
        reason_codes=("NEW_READY_ALERT",),
        evidence={"source": "lifecycle_event"},
    )
    assert cooldown.suppressed == 1
    suppressed = db.scalar(
        select(SignalAlertDecisionEvidence)
        .where(SignalAlertDecisionEvidence.decision == "SUPPRESSED_COOLDOWN")
        .order_by(SignalAlertDecisionEvidence.id.desc())
    )
    assert suppressed.cooldown_predecessor_evidence_id == historical_decision.id


def _identity(version: str) -> CalculationIdentity:
    identity = CalculationIdentity.legacy_unknown(run_id=1, ticker="ACME")
    return replace(
        identity,
        algorithm=replace(
            identity.algorithm,
            calculation_version=IdentityDimension.known(VersionIdentity("setup-test", version)),
        ),
    )


def _snapshot(
    identity: CalculationIdentity,
    *,
    source_evidence: dict[str, int],
    source_hash: str,
) -> SetupSignalSnapshot:
    row_ids = {
        "fundamental": 101,
        "technical": 102,
        "combined": 103,
        "ranking": 104,
        "regime": 105,
        "sector": 106,
    }
    source_ids = {
        "fundamental_score_id": row_ids["fundamental"],
        "fundamental_evidence_id": source_evidence["fundamental"],
        "technical_score_id": row_ids["technical"],
        "technical_evidence_id": source_evidence["technical"],
        "combined_result_id": row_ids["combined"],
        "combined_evidence_id": source_evidence["combined"],
        "ranking_result_id": row_ids["ranking"],
        "ranking_evidence_id": source_evidence["ranking"],
        "market_regime_snapshot_id": row_ids["regime"],
        "regime_evidence_id": source_evidence["regime"],
        "sector_rotation_snapshot_id": row_ids["sector"],
        "sector_evidence_id": source_evidence["sector"],
    }
    lineage = embed_calculation_identity({}, identity, policy="SETUP_TEST")
    lineage.update(
        {
            "source_ids": source_ids,
            "pit_price_evidence": {
                "series_fingerprint": f"price-{source_hash}",
                "bars": [{"id": 1, "data_hash": source_hash}],
            },
        }
    )
    return SetupSignalSnapshot(
        run_id=1,
        ticker="ACME",
        timeframe="1d",
        data_as_of_date=date(2026, 9, 15),
        calculated_at=datetime(2026, 9, 15, 20, tzinfo=UTC),
        origin_type="LIVE_RUN",
        engine_version="slse-test",
        config_version="cfg-v1",
        config_hash="cfg-hash",
        source_data_hash=source_hash,
        schema_version="setup-test-v1",
        data_quality_label="HIGH",
        primary_setup_family="BREAKOUT",
        primary_phase="READY",
        lifecycle_state_candidate="READY",
        actionability_candidate="ACTIONABLE",
        confidence_score=90,
        confidence_label="HIGH",
        signals_json={"technical_score": {"value": "9"}},
        feature_flags_json={},
        warning_flags_json=[],
        missing_data_json={},
        source_lineage_json=lineage,
        diagnostic_high_cross_json={},
        canonical_decision_json={},
        debug_json={},
    )


def _core(db, *, kind: str, ticker: str | None, suffix: str) -> CoreCalculationEvidence:
    row = CoreCalculationEvidence(
        artifact_kind=kind,
        run_id=1,
        ticker=ticker,
        calculation_identity_fingerprint=f"identity-{suffix}",
        calculation_identity_json={"suffix": suffix},
        payload_fingerprint=f"payload-{suffix}",
        payload_json={"suffix": suffix},
        source_evidence_ids_json={},
        evidence_key=f"key-{suffix}",
        calculated_at=datetime(2026, 9, 15, 20, tzinfo=UTC),
    )
    db.add(row)
    db.flush()
    return row


def _evidence_snapshot(evidence_id: int, session: date) -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        evidence_id=evidence_id,
        ticker="ACME",
        timeframe="1d",
        data_as_of_date=session,
        calculation_cutoff_at=datetime(session.year, session.month, session.day, 20, tzinfo=UTC),
        calendar_version="XNYS-test",
        engine_version="slse-test",
        config_version="cfg-v1",
        config_hash="cfg-hash",
        warning_flags_json=[],
    )


def _decision(state: LifecycleState, *, previous: LifecycleState | None) -> LifecycleDecision:
    return LifecycleDecision(
        setup_family=SetupFamily.BREAKOUT,
        phase_code=state.value,
        previous_state=previous,
        proposed_state=state,
        actionability_candidate=Actionability.ACTIONABLE,
        confidence_score=90,
        confidence_label=ConfidenceLabel.HIGH,
        reason_codes=(f"TO_{state.value}",),
        evidence={"condition": state.value},
    )


def _actionability() -> ActionabilityDecision:
    return ActionabilityDecision(
        actionability=Actionability.ACTIONABLE,
        reason_codes=("ACTIONABLE",),
    )


def _event(
    *,
    effective: date,
    from_state: LifecycleState | None,
    to_state: LifecycleState,
) -> SetupLifecycleEvent:
    return SetupLifecycleEvent(
        ticker="ACME",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=effective,
        event_type="STATE_TRANSITION",
        from_state=from_state.value if from_state else None,
        to_state=to_state.value,
        from_phase=from_state.value if from_state else None,
        to_phase=to_state.value,
        immediate_transition=False,
        actionability_before="WATCH_ONLY" if from_state else None,
        actionability_after="ACTIONABLE",
        confidence_score=90,
        confidence_label="HIGH",
        severity="ACTIONABLE",
        source_event_key=f"event-{effective}-{to_state.value}",
        engine_version="slse-test",
        config_version="cfg-v1",
        config_hash="cfg-hash",
        reason_codes_json=(f"TO_{to_state.value}",),
        evidence_json={"condition": to_state.value},
        warning_flags_json=[],
    )


def _episode(
    evaluation_id: int,
    transition_id: int | None,
    current_session: date,
) -> SetupLifecycleEpisode:
    return SetupLifecycleEpisode(
        ticker="ACME",
        timeframe="1d",
        setup_family="BREAKOUT",
        status="ACTIVE",
        opened_on=current_session,
        current_as_of_date=current_session,
        last_observed_on=current_session,
        missing_observation_sessions=0,
        current_state="DEVELOPING",
        current_phase="DEVELOPING",
        state_entered_on=current_session,
        state_age_sessions=0,
        current_actionability="ACTIONABLE",
        confidence_score=90,
        confidence_label="HIGH",
        latest_evaluation_evidence_id=evaluation_id,
        latest_transition_evidence_id=transition_id,
        engine_version="slse-test",
        config_version="cfg-v1",
        config_hash="cfg-hash",
        metadata_json={},
    )
