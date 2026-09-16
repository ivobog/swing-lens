from copy import deepcopy
from datetime import date

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, selectinload
from test_combined_decision import _config
from test_combined_ranking_identity_adoption import _identity_aware_sources

from alembic import command
from app.models.tables import (
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupSignalSnapshot,
    SignalAlertEvent,
    TechnicalScore,
)
from app.services.combined_decision import _to_model, combine_row_decision
from app.services.core_calculation_evidence import CoreEvidenceKind, persist_core_evidence
from app.services.ranking_profile_config import get_ranking_profile
from app.services.ranking_profile_engine import rank_single_row
from app.services.ranking_profile_service import _to_ranking_model
from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
from app.services.setup_lifecycle.decision_evidence import (
    persist_lifecycle_evaluation_evidence,
    persist_setup_evidence,
)
from app.services.setup_lifecycle.dtos import ActionabilityDecision, LifecycleDecision
from app.services.setup_lifecycle.enums import (
    Actionability,
    ConfidenceLabel,
    LifecycleState,
    SetupFamily,
)
from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.setup_lifecycle.source_loader import TickerSourceContext
from app.services.technical_consumer_eligibility import (
    TECHNICAL_ELIGIBILITY_KEY,
    TechnicalConsumerPolicy,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_core_permissions_freeze_round_trip_retry_and_block_existing_ready_episode(
    disposable_postgres_database,
    monkeypatch,
):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    from setup_lifecycle.test_snapshot_builder import _bar

    from app.models.tables import UploadRun
    from app.services.combined_ranking_identity import calculation_identity_from_debug

    row, fundamental, technical, cutoff = _identity_aware_sources()
    technical.insufficient_data = False
    technical.data_quality_score = 9
    technical_id = calculation_identity_from_debug(technical.debug_json)
    profile = get_ranking_profile("momentum_swing")
    with Session(engine) as db:
        db.add(UploadRun(id=row.run_id, filename="t12b.csv", status="COMPLETED"))
        db.flush()
        from app.models.tables import MarketCalculationContext

        db.add(
            MarketCalculationContext(
                id=cutoff.context_id,
                upload_run_id=row.run_id,
                cutoff_at=cutoff.cutoff_at,
                exchange_timezone=cutoff.exchange_timezone,
                latest_completed_session=cutoff.latest_completed_session,
                daily_bar_ready_at=cutoff.daily_bar_ready_at,
                calendar_version=cutoff.calendar_version,
                bar_readiness_version=cutoff.bar_readiness_version,
                cutoff_reason=cutoff.cutoff_reason,
            )
        )
        db.flush()
        db.add_all([row, fundamental, technical])
        db.flush()
        persist_core_evidence(db, kind=CoreEvidenceKind.FUNDAMENTAL, current_row=fundamental)
        first_technical = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.TECHNICAL,
            current_row=technical,
        )
        db.commit()
        # The actual cohort read loads all exact readiness/value envelopes in two
        # SELECTs. Multiple consumer/profile evaluations issue no further reads.
        statements = []

        def count_select(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", count_select)
        technical = db.scalars(
            select(TechnicalScore).options(selectinload(TechnicalScore.calculation_evidence))
        ).one()
        # Warm unrelated source projections so the measured policy lane is isolated.
        _ = row.ticker, fundamental.fundamental_score
        statements.clear()
        for _ in range(7):
            rank_single_row(
                profile=profile,
                row=row,
                fundamental=fundamental,
                technical=technical,
                config=_config(),
                today=date(2026, 7, 7),
            )
        assert statements == []
        event.remove(engine, "before_cursor_execute", count_select)
        first_combined = combine_row_decision(row, fundamental, technical, config=_config())
        assert first_combined.has_technical
        combined_model = _to_model(row.run_id, 1, first_combined, technical_id)
        combined = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.COMBINED,
            current_row=combined_model,
            sources={"technical": technical, "fundamental": fundamental},
        )
        ranking_model = _to_ranking_model(
            row.run_id,
            rank_single_row(
                profile=profile,
                row=row,
                fundamental=fundamental,
                technical=technical,
                config=_config(),
            ),
            technical_id,
        )
        ranking = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.RANKING,
            current_row=ranking_model,
            sources={"technical": technical, "fundamental": fundamental},
        )
        context = TickerSourceContext(
            raw_row=row,
            fundamental_score=fundamental,
            technical_score=technical,
            price_bars=(_bar(cutoff.latest_completed_session, close=101),),
            market_cutoff=cutoff,
        )
        snapshot = SetupSignalSnapshot()
        SetupLifecycleRepository()._apply_snapshot_fields(
            snapshot,
            SetupLifecycleSnapshotBuilder().build(context).dto,
        )
        db.add(snapshot)
        db.flush()
        setup = persist_setup_evidence(db, snapshot)
        assert setup is not None
        historical = {e.id: deepcopy(e.payload_json) for e in (combined, ranking, setup)}
        old_decision = LifecycleDecision(
            setup_family=SetupFamily.BREAKOUT,
            phase_code="PIVOT_READY",
            previous_state=None,
            proposed_state=LifecycleState.READY,
            actionability_candidate=Actionability.ACTIONABLE,
            confidence_score=90,
            confidence_label=ConfidenceLabel.HIGH,
            reason_codes=("VALID_EARLIER_READY",),
            evidence={},
        )
        old_evaluation = persist_lifecycle_evaluation_evidence(
            db,
            snapshot=snapshot,
            episode=None,
            decision=old_decision,
            actionability=ActionabilityDecision(Actionability.ACTIONABLE, ("GATES_PASS",)),
            evaluation_run_id=None,
            transition_eligible=True,
        )
        episode = SetupLifecycleEpisode(
            ticker=row.ticker,
            timeframe="1d",
            setup_family="BREAKOUT",
            status="ACTIVE",
            engine_version=snapshot.engine_version,
            config_version=snapshot.config_version,
            config_hash=snapshot.config_hash,
            current_state="READY",
            current_phase="PIVOT_READY",
            current_actionability="ACTIONABLE",
            opened_on=snapshot.data_as_of_date,
            last_observed_on=snapshot.data_as_of_date,
            current_as_of_date=snapshot.data_as_of_date,
            state_entered_on=snapshot.data_as_of_date,
            current_snapshot_id=snapshot.id,
            confidence_score=90,
            confidence_label="HIGH",
            latest_evaluation_evidence_id=old_evaluation.id,
            state_age_sessions=0,
            missing_observation_sessions=0,
        )
        db.add(episode)
        db.flush()
        old_payload = deepcopy(old_evaluation.payload_json)
        technical.insufficient_data = True
        second_technical = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.TECHNICAL,
            current_row=technical,
        )
        # Expire the explicitly loaded relationship after the projection moves.
        db.expire(technical, ["calculation_evidence"])
        blocked_combined = combine_row_decision(row, fundamental, technical, config=_config())
        assert not blocked_combined.has_technical
        blocked_ranking = rank_single_row(
            profile=profile,
            row=row,
            fundamental=fundamental,
            technical=technical,
            config=_config(),
        )
        assert blocked_ranking.technical_profile_score is None
        newer = SetupSignalSnapshot()
        SetupLifecycleRepository()._apply_snapshot_fields(
            newer,
            SetupLifecycleSnapshotBuilder().build(context).dto,
        )
        db.add(newer)
        db.flush()
        newer_setup = persist_setup_evidence(db, newer)
        assert newer_setup.id != setup.id
        service = SetupLifecycleEpisodeService()
        result = service.apply_snapshot(db, newer, preloaded_episodes=(episode,))
        assert result.decision.proposed_state is LifecycleState.READY
        assert result.actionability.actionability is Actionability.BLOCKED
        assert result.lifecycle_event is None
        evaluation = db.get(SetupLifecycleEvaluationEvidence, episode.latest_evaluation_evidence_id)
        assert evaluation.id != old_evaluation.id
        assert evaluation.payload_json["decision"]["actionability"] == "BLOCKED"
        alerts = SetupLifecycleAlertService()
        alerts.seed_builtin_rules(db)
        alerts.evaluate_episode_result(db, result)
        assert not db.scalars(
            select(SignalAlertEvent).where(
                SignalAlertEvent.evidence_json["actionability_after"].astext == "ACTIONABLE"
            )
        ).all()
        assert old_evaluation.payload_json == old_payload
        # A mutable Setup projection cannot clear a frozen block.
        newer.source_lineage_json = deepcopy(snapshot.source_lineage_json)
        repeated = service.apply_snapshot(db, newer, preloaded_episodes=(episode,))
        assert repeated.actionability.actionability is Actionability.BLOCKED
        for evidence_id, payload in historical.items():
            assert db.get(CoreCalculationEvidence, evidence_id).payload_json == payload
        assert first_technical.id != second_technical.id
        for evidence in (combined, ranking, setup):
            payload = evidence.payload_json
            if evidence.artifact_kind == "SETUP":
                payload = payload["source_lineage_json"]
            else:
                payload = payload["debug_json"]
            assert (
                payload[TECHNICAL_ELIGIBILITY_KEY]["decision"]["producer_evidence_id"]
                == first_technical.id
            )
        retry = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.COMBINED,
            current_row=combined_model,
            sources={
                "technical": type("Pinned", (), {"evidence_id": first_technical.id})(),
                "fundamental": fundamental,
            },
        )
        assert retry.id == combined.id
        assert db.scalars(
            select(CoreCalculationEvidenceSource).where(
                CoreCalculationEvidenceSource.evidence_id == newer_setup.id,
                CoreCalculationEvidenceSource.source_evidence_id == second_technical.id,
            )
        ).one()
        # Changing the policy registry prospectively cannot reinterpret either
        # historical READY permissions or the latest frozen blocked Setup.
        with monkeypatch.context() as changed_policy:
            for module, consumer in (
                ("app.services.combined_decision", "COMBINED"),
                ("app.services.ranking_profile_engine", "RANKING"),
                ("app.services.setup_lifecycle.snapshot_builder", "SETUP"),
            ):
                changed_policy.setattr(
                    f"{module}.TECHNICAL_TO_{consumer}",
                    TechnicalConsumerPolicy(consumer, f"technical-to-{consumer.lower()}-v2-test"),
                )
            prospective = (
                combine_row_decision(row, fundamental, technical, config=_config()).debug_evidence,
                rank_single_row(
                    profile=profile, row=row, fundamental=fundamental,
                    technical=technical, config=_config(),
                ).debug,
                SetupLifecycleSnapshotBuilder().build(context).dto.source_lineage,
            )
            for payload in prospective:
                assert payload[TECHNICAL_ELIGIBILITY_KEY]["decision"]["policy_version"].endswith(
                    "-v2-test"
                )
            frozen_replay = service.apply_snapshot(db, newer, preloaded_episodes=(episode,))
            assert frozen_replay.actionability.actionability is Actionability.BLOCKED
            for evidence_id, payload in historical.items():
                assert db.get(CoreCalculationEvidence, evidence_id).payload_json == payload
            blocked_lineage = newer_setup.payload_json["source_lineage_json"]
            assert blocked_lineage[TECHNICAL_ELIGIBILITY_KEY]["decision"]["policy_version"] == (
                "technical-to-setup-v1"
            )
        ids = list(historical)
        db.commit()
    with Session(engine) as db:
        for evidence_id in ids:
            assert (
                db.get(CoreCalculationEvidence, evidence_id).payload_json == historical[evidence_id]
            )
    engine.dispose()
