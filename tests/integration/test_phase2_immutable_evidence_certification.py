from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    CombinedResult,
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    FundamentalScore,
    RankingResult,
    SectorRotationSnapshot,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleTransitionEvidence,
    SignalAlertDecisionEvidence,
    SignalAlertRule,
    SignalAlertRuleEvidence,
    TechnicalScore,
    UploadRun,
    WinnerPredictionSnapshot,
)
from app.services.calculation_identity import (
    CalculationIdentity,
    IdentityDimension,
    VersionIdentity,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    get_certified_evidence_for_row,
    persist_core_evidence,
)
from app.services.historical_read_service import (
    HistoricalReadError,
    ReadMode,
    read_core_artifact,
)
from app.services.setup_lifecycle.decision_evidence import (
    persist_alert_decision_evidence,
    persist_lifecycle_evaluation_evidence,
    persist_lifecycle_transition_evidence,
)
from app.services.setup_lifecycle.dtos import ActionabilityDecision, LifecycleDecision
from app.services.setup_lifecycle.enums import (
    Actionability,
    ConfidenceLabel,
    LifecycleState,
    SetupFamily,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]

PHASE2_BASE = "0074_ceri_evidence_quarantine"
PHASE2_REVISIONS = (
    "0075_core_immutable_evidence",
    "0076_regime_sector_evidence",
    "0077_ceri_decision_evidence",
    "0078_ibmi_constituent_evidence",
    "0079_setup_lifecycle_alert_ev",
)
SESSION = date(2026, 9, 15)


def test_phase2_postgresql_migration_chain_round_trip_and_schema_contract(
    disposable_postgres_database: str,
) -> None:
    config = _config(disposable_postgres_database)
    script = ScriptDirectory.from_config(config)
    assert tuple(script.get_heads()) == (PHASE2_REVISIONS[-1],)
    phase2_chain = tuple(
        revision.revision
        for revision in script.walk_revisions(PHASE2_BASE, PHASE2_REVISIONS[-1])
    )
    assert phase2_chain == (*reversed(PHASE2_REVISIONS), PHASE2_BASE)

    command.upgrade(config, PHASE2_BASE)
    engine = create_engine(disposable_postgres_database)
    schema = inspect(engine)
    assert "core_calculation_evidence" not in schema.get_table_names()

    command.upgrade(config, "head")
    schema = inspect(engine)
    expected_tables = {
        "core_calculation_evidence",
        "core_calculation_evidence_sources",
        "core_calculation_current_projections",
        "setup_lifecycle_evaluation_evidence",
        "setup_lifecycle_transition_evidence",
        "signal_alert_rule_evidence",
        "signal_alert_decision_evidence",
    }
    assert expected_tables <= set(schema.get_table_names())
    assert _alembic_revision(engine) == PHASE2_REVISIONS[-1]

    for table_name, column_name in {
        "fundamental_scores": "evidence_id",
        "technical_scores": "evidence_id",
        "combined_results": "evidence_id",
        "ranking_results": "evidence_id",
        "market_regime_snapshots": "evidence_id",
        "sector_rotation_snapshots": "evidence_id",
        "ib_intelligence_features": "evidence_id",
        "ceri_score_snapshots": "evidence_id",
        "setup_signal_snapshots": "evidence_id",
        "setup_lifecycle_episodes": "latest_evaluation_evidence_id",
        "signal_alert_events": "decision_evidence_id",
    }.items():
        columns = {column["name"]: column for column in schema.get_columns(table_name)}
        assert columns[column_name]["nullable"] is True

    _assert_restrict_fk(
        schema,
        "core_calculation_evidence_sources",
        "source_evidence_id",
        "core_calculation_evidence",
    )
    _assert_restrict_fk(
        schema,
        "setup_lifecycle_transition_evidence",
        "evaluation_evidence_id",
        "setup_lifecycle_evaluation_evidence",
    )
    _assert_restrict_fk(
        schema,
        "signal_alert_decision_evidence",
        "rule_evidence_id",
        "signal_alert_rule_evidence",
    )
    indexes = {index["name"] for index in schema.get_indexes("core_calculation_evidence")}
    assert {"idx_core_evidence_identity", "idx_core_evidence_scope"} <= indexes
    unique_constraints = {
        constraint["name"]
        for constraint in schema.get_unique_constraints("core_calculation_evidence")
    }
    assert "uq_core_calculation_evidence_key" in unique_constraints

    command.downgrade(config, PHASE2_BASE)
    schema = inspect(engine)
    assert expected_tables.isdisjoint(schema.get_table_names())
    assert _alembic_revision(engine) == PHASE2_BASE

    command.upgrade(config, "head")
    assert _alembic_revision(engine) == PHASE2_REVISIONS[-1]
    engine.dispose()


def test_phase2_integrated_evidence_graph_survives_a_new_world(
    disposable_postgres_database: str,
) -> None:
    config = _config(disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)

    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="t11e-world.csv", status="COMPLETED"))
        db.commit()

        first = _world(db, "C1")
        first_payloads = {kind: deepcopy(row.payload_json) for kind, row in first.items()}
        first_sources = {
            kind: deepcopy(row.source_evidence_ids_json) for kind, row in first.items()
        }
        lifecycle1, transition1, alert1, episode, rule = _decision_world(
            db,
            setup=first[CoreEvidenceKind.SETUP],
            suffix="C1",
            effective_session=date(2026, 9, 14),
            state=LifecycleState.READY,
        )
        winner = _winner(first)
        db.add(winner)
        db.commit()
        winner_id = winner.id

        second = _world(db, "C2")
        lifecycle2, transition2, alert2, episode, rule = _decision_world(
            db,
            setup=second[CoreEvidenceKind.SETUP],
            suffix="C2",
            effective_session=SESSION,
            state=LifecycleState.CONFIRMED,
            episode=episode,
            rule=rule,
        )
        db.commit()

        assert lifecycle2.prior_evaluation_evidence_id == lifecycle1.id
        assert lifecycle2.prior_transition_evidence_id == transition1.id
        assert transition2.prior_transition_evidence_id == transition1.id
        assert alert1.rule_evidence_id != alert2.rule_evidence_id
        assert episode.latest_evaluation_evidence_id == lifecycle2.id
        assert episode.latest_transition_evidence_id == transition2.id
        lifecycle1_id = lifecycle1.id
        transition1_id = transition1.id
        alert1_id = alert1.id

        for kind in CoreEvidenceKind:
            evidence1 = first[kind]
            evidence2 = second[kind]
            assert evidence1.id != evidence2.id
            assert evidence1.payload_json == first_payloads[kind]
            assert evidence1.source_evidence_ids_json == first_sources[kind]
            assert evidence1.payload_fingerprint == CanonicalEvidenceSerializer.fingerprint(
                evidence1.payload_json
            )
            assert (
                evidence1.calculation_identity_fingerprint
                != evidence2.calculation_identity_fingerprint
            )

            historical = read_core_artifact(
                db,
                kind=kind,
                mode=ReadMode.EVIDENCE,
                evidence_id=evidence1.id,
            )
            current = read_core_artifact(
                db,
                kind=kind,
                mode=ReadMode.CURRENT,
                run_id=evidence2.run_id,
                ticker=evidence2.ticker,
                ranking_profile=evidence2.ranking_profile,
            )
            assert historical.id == evidence1.id
            assert current.id == evidence2.id

        assert first[CoreEvidenceKind.COMBINED].source_evidence_ids_json == {
            "fundamental": first[CoreEvidenceKind.FUNDAMENTAL].id,
            "technical": first[CoreEvidenceKind.TECHNICAL].id,
        }
        assert first[CoreEvidenceKind.RANKING].source_evidence_ids_json == {
            "fundamental": first[CoreEvidenceKind.FUNDAMENTAL].id,
            "ibmi_liquidity": first[CoreEvidenceKind.IBMI].id,
            "technical": first[CoreEvidenceKind.TECHNICAL].id,
        }
        assert first[CoreEvidenceKind.SECTOR].source_evidence_ids_json == {
            "ranking:ACME": first[CoreEvidenceKind.RANKING].id,
            "regime": first[CoreEvidenceKind.REGIME].id,
        }
        assert first[CoreEvidenceKind.CERI].source_evidence_ids_json == {
            "ibmi_volatility_short_pressure": first[CoreEvidenceKind.IBMI].id,
        }
        assert first[CoreEvidenceKind.SETUP].source_evidence_ids_json == {
            "combined": first[CoreEvidenceKind.COMBINED].id,
            "fundamental": first[CoreEvidenceKind.FUNDAMENTAL].id,
            "ranking_metadata": first[CoreEvidenceKind.RANKING].id,
            "regime": first[CoreEvidenceKind.REGIME].id,
            "sector": first[CoreEvidenceKind.SECTOR].id,
            "technical": first[CoreEvidenceKind.TECHNICAL].id,
        }

        with pytest.raises(
            HistoricalReadError, match="ORIGINAL_CONTEXT_RECONSTRUCTION_UNSUPPORTED"
        ):
            read_core_artifact(
                db, kind=CoreEvidenceKind.COMBINED, mode=ReadMode.ORIGINAL_CONTEXT
            )
        with pytest.raises(HistoricalReadError, match="HISTORICAL_MODE_REQUIRES_IDENTITY"):
            read_core_artifact(
                db,
                kind=CoreEvidenceKind.COMBINED,
                mode=ReadMode.CURRENT_RULES_RETROSPECTIVE,
            )
        with pytest.raises(EvidenceUnavailableError, match="EVIDENCE_UNAVAILABLE"):
            read_core_artifact(
                db,
                kind=CoreEvidenceKind.RANKING,
                mode=ReadMode.EVIDENCE,
                calculation_identity="missing-certified-history",
            )
        with pytest.raises(EvidenceUnavailableError, match="LEGACY_EVIDENCE_UNAVAILABLE"):
            get_certified_evidence_for_row(
                db,
                kind=CoreEvidenceKind.RANKING,
                current_row=SimpleNamespace(
                    id=999, run_id=1, ticker="ACME", evidence_id=None
                ),
            )

    with Session(engine) as reloaded:
        winner = reloaded.get(WinnerPredictionSnapshot, winner_id)
        assert winner is not None
        assert winner.feature_json == {"frozen_world": "C1", "combined_score": "8.2"}
        assert winner.source_ids_json == {
            "combined_evidence_id": first[CoreEvidenceKind.COMBINED].id,
            "ranking_evidence_id": first[CoreEvidenceKind.RANKING].id,
        }
        assert read_core_artifact(
            reloaded,
            kind=CoreEvidenceKind.COMBINED,
            mode=ReadMode.EVIDENCE,
            evidence_id=first[CoreEvidenceKind.COMBINED].id,
        ).payload_json == first_payloads[CoreEvidenceKind.COMBINED]
        lifecycle1 = reloaded.get(SetupLifecycleEvaluationEvidence, lifecycle1_id)
        transition1 = reloaded.get(SetupLifecycleTransitionEvidence, transition1_id)
        alert1 = reloaded.get(SignalAlertDecisionEvidence, alert1_id)
        assert lifecycle1 is not None and lifecycle1.output_state == "READY"
        assert transition1 is not None and transition1.to_state == "READY"
        assert alert1 is not None and alert1.payload_json["decision_payload"] == {
            "world": "C1"
        }
        rule1 = reloaded.get(SignalAlertRuleEvidence, alert1.rule_evidence_id)
        assert rule1 is not None and rule1.config_version == "C1"

        immutable = reloaded.get(
            CoreCalculationEvidence, first[CoreEvidenceKind.FUNDAMENTAL].id
        )
        assert immutable is not None
        immutable.payload_json = {"score": "mutated"}
        with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
            reloaded.flush()
        reloaded.rollback()

        with pytest.raises(IntegrityError):
            reloaded.execute(
                text("DELETE FROM core_calculation_evidence WHERE id = :evidence_id"),
                {"evidence_id": first[CoreEvidenceKind.FUNDAMENTAL].id},
            )
            reloaded.flush()
        reloaded.rollback()
        assert reloaded.get(
            CoreCalculationEvidence, first[CoreEvidenceKind.FUNDAMENTAL].id
        ) is not None

    command.downgrade(config, PHASE2_BASE)
    assert _alembic_revision(engine) == PHASE2_BASE
    command.upgrade(config, "head")
    assert _alembic_revision(engine) == PHASE2_REVISIONS[-1]
    engine.dispose()


def _world(db: Session, suffix: str) -> dict[CoreEvidenceKind, CoreCalculationEvidence]:
    evidence: dict[CoreEvidenceKind, CoreCalculationEvidence] = {}

    def persist(
        kind: CoreEvidenceKind,
        *,
        sources: dict[str, CoreCalculationEvidence] | None = None,
        run_id: int | None = 1,
        ticker: str | None = "ACME",
        ranking_profile: str | None = None,
        payload: dict | None = None,
    ) -> CoreCalculationEvidence:
        source_rows = {
            role: SimpleNamespace(evidence_id=row.id)
            for role, row in (sources or {}).items()
        }
        row = SimpleNamespace(
            run_id=run_id,
            ticker=ticker,
            ranking_profile=ranking_profile,
            evidence_id=None,
        )
        result = persist_core_evidence(
            db,
            kind=kind,
            current_row=row,
            sources=source_rows,
            payload=payload or {"value": "8.2", "world": suffix},
            scope_ticker=ticker,
            scope_profile=ranking_profile,
            calculation_identity=_identity(f"{kind.value.lower()}-{suffix}"),
        )
        assert result is not None
        evidence[kind] = result
        return result

    fundamental = persist(CoreEvidenceKind.FUNDAMENTAL)
    technical = persist(
        CoreEvidenceKind.TECHNICAL,
        payload={"value": "8.2", "world": suffix, "price_bar_revision": suffix},
    )
    ibmi = persist(
        CoreEvidenceKind.IBMI,
        payload={
            "value": "8.2",
            "world": suffix,
            "provider_observation_ids": [f"ibmi-provider-{suffix}"],
            "price_bar_revision": suffix,
        },
    )
    combined = persist(
        CoreEvidenceKind.COMBINED,
        sources={"fundamental": fundamental, "technical": technical},
    )
    ranking = persist(
        CoreEvidenceKind.RANKING,
        sources={
            "fundamental": fundamental,
            "technical": technical,
            "ibmi_liquidity": ibmi,
        },
        ranking_profile="quality_momentum",
    )
    regime = persist(CoreEvidenceKind.REGIME, run_id=None, ticker=None)
    sector = persist(
        CoreEvidenceKind.SECTOR,
        sources={"ranking:ACME": ranking, "regime": regime},
        ticker=None,
        ranking_profile="universe_only",
    )
    persist(
        CoreEvidenceKind.CERI,
        sources={"ibmi_volatility_short_pressure": ibmi},
        payload={
            "value": "8.2",
            "world": suffix,
            "provider_evidence_ids": [f"ceri-provider-{suffix}"],
            "price_bar_revision": suffix,
        },
    )
    persist(
        CoreEvidenceKind.SETUP,
        sources={
            "fundamental": fundamental,
            "technical": technical,
            "combined": combined,
            "ranking_metadata": ranking,
            "regime": regime,
            "sector": sector,
        },
        payload={
            "value": "READY",
            "world": suffix,
            "price_bar_revision": suffix,
        },
    )
    return evidence


def _identity(version: str) -> CalculationIdentity:
    identity = CalculationIdentity.legacy_unknown(run_id=1, ticker="ACME")
    return replace(
        identity,
        algorithm=replace(
            identity.algorithm,
            calculation_version=IdentityDimension.known(
                VersionIdentity("t11e-certification", version)
            ),
        ),
    )


def _winner(
    evidence: dict[CoreEvidenceKind, CoreCalculationEvidence],
) -> WinnerPredictionSnapshot:
    cutoff = datetime(2026, 9, 15, 20, tzinfo=UTC)
    return WinnerPredictionSnapshot(
        run_id=1,
        ticker="ACME",
        prediction_as_of_date=SESSION,
        source_data_cutoff_at=cutoff,
        decision_at=cutoff,
        captured_at=cutoff,
        entry_schedule_status="READY",
        entry_data_status="READY",
        eligibility_status="ELIGIBLE",
        ranking_profile="quality_momentum",
        fundamental_score=Decimal("8.2"),
        technical_score=Decimal("8.2"),
        combined_score=Decimal("8.2"),
        feature_schema_version="t11e-v1",
        feature_vector_hash="f" * 64,
        config_hash="c" * 64,
        calculation_version="t11e-v1",
        feature_json={"frozen_world": "C1", "combined_score": "8.2"},
        source_ids_json={
            "combined_evidence_id": evidence[CoreEvidenceKind.COMBINED].id,
            "ranking_evidence_id": evidence[CoreEvidenceKind.RANKING].id,
        },
        warning_flags_json=[],
        lineage_json={"evidence_world": "C1"},
    )


def _decision_world(
    db: Session,
    *,
    setup: CoreCalculationEvidence,
    suffix: str,
    effective_session: date,
    state: LifecycleState,
    episode: SetupLifecycleEpisode | None = None,
    rule: SignalAlertRule | None = None,
) -> tuple[
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleTransitionEvidence,
    SignalAlertDecisionEvidence,
    SetupLifecycleEpisode,
    SignalAlertRule,
]:
    prior_state = LifecycleState(episode.current_state) if episode is not None else None
    snapshot = SimpleNamespace(
        evidence_id=setup.id,
        ticker="ACME",
        timeframe="1d",
        data_as_of_date=effective_session,
        calculation_cutoff_at=datetime(
            effective_session.year,
            effective_session.month,
            effective_session.day,
            20,
            tzinfo=UTC,
        ),
        calendar_version="XNYS-t11e",
        engine_version="t11e-lifecycle",
        config_version=suffix,
        config_hash=f"lifecycle-rule-{suffix}",
        warning_flags_json=[],
    )
    decision = LifecycleDecision(
        setup_family=SetupFamily.BREAKOUT,
        phase_code=state.value,
        previous_state=prior_state,
        proposed_state=state,
        actionability_candidate=Actionability.ACTIONABLE,
        confidence_score=90,
        confidence_label=ConfidenceLabel.HIGH,
        reason_codes=(f"TO_{state.value}",),
        evidence={"world": suffix},
    )
    evaluation = persist_lifecycle_evaluation_evidence(
        db,
        snapshot=snapshot,
        episode=episode,
        decision=decision,
        actionability=ActionabilityDecision(
            actionability=Actionability.ACTIONABLE,
            reason_codes=("ACTIONABLE",),
        ),
        evaluation_run_id=None,
        transition_eligible=True,
    )
    assert evaluation is not None
    prior_transition_id = episode.latest_transition_evidence_id if episode is not None else None
    event = SimpleNamespace(
        ticker="ACME",
        timeframe="1d",
        setup_family="BREAKOUT",
        effective_date=effective_session,
        event_type="STATE_TRANSITION",
        from_state=prior_state.value if prior_state else None,
        to_state=state.value,
        from_phase=prior_state.value if prior_state else None,
        to_phase=state.value,
        state_age_before=episode.state_age_sessions if episode is not None else 0,
        actionability_before="ACTIONABLE" if episode is not None else None,
        actionability_after="ACTIONABLE",
        immediate_transition=False,
        config_hash=f"lifecycle-rule-{suffix}",
        reason_codes_json=[f"TO_{state.value}"],
        evidence_json={"world": suffix},
    )
    transition = persist_lifecycle_transition_evidence(
        db,
        event=event,
        evaluation=evaluation,
        prior_transition_evidence_id=prior_transition_id,
    )
    assert transition is not None
    if episode is None:
        episode = SetupLifecycleEpisode(
            ticker="ACME",
            timeframe="1d",
            setup_family="BREAKOUT",
            status="ACTIVE",
            opened_on=effective_session,
            current_as_of_date=effective_session,
            last_observed_on=effective_session,
            missing_observation_sessions=0,
            current_state=state.value,
            current_phase=state.value,
            state_entered_on=effective_session,
            state_age_sessions=0,
            current_actionability="ACTIONABLE",
            confidence_score=90,
            confidence_label="HIGH",
            latest_evaluation_evidence_id=evaluation.id,
            latest_transition_evidence_id=transition.id,
            engine_version="t11e-lifecycle",
            config_version=suffix,
            config_hash=f"lifecycle-rule-{suffix}",
            metadata_json={},
        )
        db.add(episode)
    else:
        episode.current_as_of_date = effective_session
        episode.last_observed_on = effective_session
        episode.current_state = state.value
        episode.current_phase = state.value
        episode.state_entered_on = effective_session
        episode.latest_evaluation_evidence_id = evaluation.id
        episode.latest_transition_evidence_id = transition.id
        episode.config_version = suffix
        episode.config_hash = f"lifecycle-rule-{suffix}"

    if rule is None:
        rule = SignalAlertRule(
            rule_id="T11E_STATE_CHANGE",
            enabled=True,
            severity="ACTIONABLE",
            scope="lifecycle_transition",
            setup_family="BREAKOUT",
            cooldown_sessions=2,
            minimum_confidence=0,
            config_version=suffix,
            condition_json={"to_state": state.value},
            market_restrictions_json={},
            metadata_json={},
        )
        db.add(rule)
        db.flush()
    else:
        rule.config_version = suffix
        rule.condition_json = {"to_state": state.value}
        db.flush()
    alert = persist_alert_decision_evidence(
        db,
        rule=rule,
        ticker="ACME",
        timeframe="1d",
        effective_session=effective_session,
        source_event_key=f"t11e-alert-{suffix}",
        semantic_key=f"T11E:{state.value}:ACTIONABLE",
        decision="GENERATED",
        reasons=(f"TO_{state.value}",),
        payload={"world": suffix},
        setup_evidence_id=setup.id,
        lifecycle_evaluation_evidence_id=evaluation.id,
        lifecycle_transition_evidence_id=transition.id,
    )
    db.flush()
    return evaluation, transition, alert, episode, rule


def _config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    return config


def _alembic_revision(engine) -> str:
    with engine.connect() as connection:
        return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _assert_restrict_fk(
    schema,
    table_name: str,
    constrained_column: str,
    referred_table: str,
) -> None:
    matching = [
        fk
        for fk in schema.get_foreign_keys(table_name)
        if constrained_column in fk["constrained_columns"]
        and fk["referred_table"] == referred_table
    ]
    assert len(matching) == 1
    assert matching[0]["options"].get("ondelete") == "RESTRICT"


def test_phase2_model_metadata_has_no_duplicate_current_projection_rows() -> None:
    constraints = CoreCalculationCurrentProjection.__table__.indexes
    names = {index.name for index in constraints}
    assert {
        "uq_core_current_projection_ticker_scope",
        "uq_core_current_projection_context_run_scope",
        "uq_core_current_projection_global_scope",
    } <= names
    expected_evidence_indexes = {
        FundamentalScore: "idx_fundamental_scores_evidence",
        TechnicalScore: "idx_technical_scores_evidence",
        CombinedResult: "idx_combined_results_evidence",
        RankingResult: "idx_ranking_results_evidence",
        SectorRotationSnapshot: "idx_sector_rotation_snapshots_evidence",
    }
    for model, expected_index in expected_evidence_indexes.items():
        assert expected_index in {index.name for index in model.__table__.indexes}
    assert CoreCalculationEvidenceSource.__table__.constraints
    assert select(CoreCalculationEvidence) is not None
