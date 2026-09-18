"""Native T14C writer authority attacks and transactional rejection."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from native_mutation_support import seed_native_core
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CoreCalculationEvidence,
    SetupLifecycleEvaluationEvidence,
    SetupSignalSnapshot,
    SignalAlertDecisionEvidence,
    SignalAlertRule,
    UploadRun,
)
from app.services.decision_effective_configuration import (
    resolve_alert_configuration,
    resolve_lifecycle_configuration,
    resolve_setup_configuration,
)
from app.services.decision_mutation_authority import validate_setup_projection
from app.services.setup_lifecycle.decision_evidence import (
    persist_alert_decision_evidence,
    persist_setup_evidence,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader

contextual_engine = contextual.contextual_engine

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def _native_liquidity_source(*, weak=False):
    import json
    from pathlib import Path

    from app.services.core_effective_configuration import resolve_fundamental_configuration

    aliases = resolve_fundamental_configuration().values["column_aliases"]
    values = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "e2e"
            / "single_run_certification"
            / "financial_inputs.json"
        ).read_text()
    )
    if weak:
        values.update(
            {key: "1000" for key in ("dollar_volume_10d", "dollar_volume_30d", "dollar_volume_60d")}
        )
        values.update(
            {
                "free_float": "0",
                "relative_volume_1d": "0",
                "volume_change_1d_pct": "400",
                "price_change_1d_pct": "100",
                "beta_1y": "1000",
                "beta_3y": "1000",
                "tradingview_atr_pct_14d": "100",
            }
        )
    return {aliases[key][0]: value for key, value in values.items() if key in aliases}


def test_native_setup_exact_authority_rejects_before_evidence(contextual_engine):
    config = resolve_setup_configuration()
    lifecycle_config = resolve_lifecycle_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, sources = seed_native_core(db, extra_configurations=(config, lifecycle_config))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(config.setup_config()).build(context.tickers[0]).dto
        repo = SetupLifecycleRepository(config.setup_config())
        snapshot = repo.upsert_snapshot(db, dto)
        evidence = persist_setup_evidence(db, snapshot)
        db.commit()
        assert evidence.artifact_kind == "SETUP"
        assert "ceri" not in evidence.source_evidence_ids_json
        assert persist_setup_evidence(db, snapshot).id == evidence.id
        db.commit()

        from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService

        lifecycle = SetupLifecycleEpisodeService(config=lifecycle_config.setup_config())

        original_score = snapshot.dual_score
        original_evaluations = db.scalar(
            select(func.count()).select_from(SetupLifecycleEvaluationEvidence)
        )
        snapshot.dual_score = 99
        db.add(UploadRun(id=992, filename="forged-setup-projection.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="MUTATION_SETUP_PROJECTION_PAYLOAD_MISMATCH"):
            lifecycle.apply_snapshot(db, snapshot)
        db.commit()
        assert snapshot.dual_score == original_score
        assert db.get(UploadRun, 992) is None
        assert (
            db.scalar(select(func.count()).select_from(SetupLifecycleEvaluationEvidence))
            == original_evaluations
        )

        result = lifecycle.apply_snapshot(db, snapshot)
        db.commit()
        assert result.lifecycle_evaluation_evidence is not None
        assert result.lifecycle_evaluation_evidence.setup_evidence_id == evidence.id
        # Lifecycle denormalization does not change Setup's immutable ledger.
        validate_setup_projection(db, snapshot)
        assert persist_setup_evidence(db, snapshot).id == evidence.id
        db.commit()


def test_native_handoff_uses_sealed_sources_and_writer_rechecks_after_declaration(
    contextual_engine,
):
    from app.services.combined_ranking_identity import calculation_identity_from_debug
    from app.services.core_calculation_evidence import (
        CoreEvidenceKind,
        declare_core_evidence_mutation,
        persist_core_evidence,
    )
    from app.services.transition_preflight_plan_service import _validate_handoff_temporal_lineage

    with Session(contextual_engine) as db:
        cutoff, configurations, sources = seed_native_core(db)
        combined = sources["combined"]
        technical = sources["technical"]
        debug = deepcopy(combined.debug_json)
        debug["source_ids"]["fundamental_score_id"] = None
        debug["source_ids"]["technical_score_id"] = None
        combined.debug_json = debug
        db.commit()
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        _validate_handoff_temporal_lineage(
            db, upload_run_id=7, market_cutoff=cutoff, built_rows=[(context.tickers[0], None)]
        )
        db.commit()

        evidence = db.get(CoreCalculationEvidence, combined.evidence_id)
        payload = deepcopy(evidence.payload_json)
        payload.pop("producer_readiness")
        payload.pop("effective_configuration_at_creation")
        producer_sources = {role: sources[role] for role in ("fundamental", "technical")}
        declaration = declare_core_evidence_mutation(
            db,
            kind=CoreEvidenceKind.COMBINED,
            current_row=combined,
            sources=producer_sources,
            payload=payload,
            effective_configuration=configurations[2].snapshot,
        )
        original_score = technical.dual_score
        original_pointer = combined.evidence_id
        db.add(UploadRun(id=991, filename="after-declaration.csv", status="COMPLETED"))
        technical.dual_score = 99
        with pytest.raises(ValueError, match="MUTATION_SOURCE_FINANCIAL_VALUE_MISMATCH"):
            persist_core_evidence(
                db,
                kind=CoreEvidenceKind.COMBINED,
                current_row=combined,
                sources=producer_sources,
                payload=payload,
                calculation_identity=calculation_identity_from_debug(combined.debug_json),
                effective_configuration=configurations[2].snapshot,
                mutation_context=declaration,
            )
        db.commit()
        assert db.get(UploadRun, 991) is None
        assert technical.dual_score == original_score
        assert combined.evidence_id == original_pointer


@pytest.mark.parametrize("batch", (False, True))
def test_native_setup_output_and_altered_retry_roll_back(contextual_engine, batch):
    config = resolve_setup_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(db, extra_configurations=(config,))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(config.setup_config()).build(context.tickers[0]).dto
        db.commit()
        repo = SetupLifecycleRepository(config.setup_config())

        def write(value):
            return (
                repo.upsert_snapshots(db, [value])[0] if batch else repo.upsert_snapshot(db, value)
            )

        db.add(UploadRun(id=997, filename="forged-native-output.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="MUTATION_SETUP_NATIVE_OUTPUT_MISMATCH"):
            write(replace(dto, promoted_fields={**dto.promoted_fields, "dual_score": 99}))
        db.commit()
        assert db.get(UploadRun, 997) is None
        assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 0
        snapshot = write(dto)
        db.commit()
        assert write(dto).id == snapshot.id
        db.commit()
        db.add(UploadRun(id=998, filename="altered-retry.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="MUTATION_SETUP_ALTERED_RETRY"):
            write(replace(dto, promoted_fields={**dto.promoted_fields, "dual_score": 99}))
        db.commit()
        assert db.get(UploadRun, 998) is None
        assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 1
        wrong_source = deepcopy(dto.source_lineage)
        wrong_source["source_ids"]["technical_score_id"] = 987654321
        attacks = (
            ("missing-identity", replace(dto, source_lineage={})),
            ("missing-configuration", replace(dto, effective_configuration=None)),
            ("wrong-target", replace(dto, ticker="FOREIGN")),
            ("wrong-session", replace(dto, data_as_of_date=date(2026, 9, 17))),
            ("wrong-run", replace(dto, run_id=987654321)),
            ("wrong-source", replace(dto, source_lineage=wrong_source)),
            (
                "forged-output",
                replace(
                    dto, promoted_fields={**dto.promoted_fields, "required_feature_coverage": 99}
                ),
            ),
        )
        for name, attacked in attacks:
            db.add(UploadRun(id=998, filename=name, status="COMPLETED"))
            with pytest.raises(ValueError):
                write(replace(attacked, source_data_hash=name))
            db.commit()
            assert db.get(UploadRun, 998) is None
            assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 1
            validate_setup_projection(db, snapshot)
            db.commit()


def test_native_lifecycle_output_projection_and_primary_scope(contextual_engine):
    from app.services.decision_mutation_authority import validate_episode_projection
    from app.services.setup_lifecycle.decision_evidence import persist_lifecycle_evaluation_evidence
    from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService

    config = resolve_setup_configuration()
    lifecycle_config = resolve_lifecycle_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(db, extra_configurations=(config, lifecycle_config))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(config.setup_config()).build(context.tickers[0]).dto
        snapshot = SetupLifecycleRepository(config.setup_config()).upsert_snapshot(db, dto)
        lifecycle = SetupLifecycleEpisodeService(config=lifecycle_config.setup_config())
        applied = lifecycle.apply_snapshot(db, snapshot)
        db.commit()
        episode = applied.episode
        assert episode is not None
        validate_episode_projection(db, episode)
        db.commit()
        for field, value in (
            ("confidence_score", 99),
            ("state_age_sessions", 99),
            ("metadata_json", {"setup_score": 999}),
        ):
            setattr(episode, field, value)
            db.add(UploadRun(id=996, filename="forged-primary-input.csv", status="COMPLETED"))
            with pytest.raises(ValueError, match="EPISODE_PROJECTION_MISMATCH"):
                lifecycle.refresh_primary_status(
                    db, ticker=episode.ticker, timeframe=episode.timeframe, market_cutoff=cutoff
                )
            db.commit()
            assert db.get(UploadRun, 996) is None
            validate_episode_projection(db, episode)
            db.commit()
        with pytest.raises(ValueError, match="PRIMARY_TEMPORAL_AUTHORITY_REQUIRED"):
            lifecycle.refresh_primary_status(db, ticker=episode.ticker, timeframe=episode.timeframe)
        db.commit()
        lifecycle.refresh_primary_status(
            db, ticker=episode.ticker, timeframe=episode.timeframe, market_cutoff=cutoff
        )
        db.commit()
        assert episode.is_primary and episode.primary_rank == 1
        source_config = lifecycle_config.setup_config()
        drifted = SetupLifecycleEpisodeService(
            config=replace(source_config, confidence=replace(source_config.confidence, high_min=1))
        )
        db.add(UploadRun(id=996, filename="primary-config-drift.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="PRIMARY_CONFIGURATION_MISMATCH"):
            drifted.refresh_primary_status(
                db, ticker=episode.ticker, timeframe=episode.timeframe, market_cutoff=cutoff
            )
        db.commit()
        assert db.get(UploadRun, 996) is None
        db.add(UploadRun(id=996, filename="forged-evaluation.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="NATIVE_DECISION_MISMATCH"):
            persist_lifecycle_evaluation_evidence(
                db,
                snapshot=snapshot,
                episode=None,
                decision=replace(applied.decision, confidence_score=99),
                actionability=applied.actionability,
                evaluation_run_id=None,
                transition_eligible=True,
                effective_configuration=lifecycle.effective_configuration,
            )
        db.commit()
        assert db.get(UploadRun, 996) is None


def test_native_lifecycle_concurrent_advance_and_decision_reclaim(contextual_engine):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime, timedelta
    from threading import Barrier

    from test_winner_jobs_reliability_postgresql import _claim_registered_job

    from app.models.tables import PipelineRun, SetupLifecycleEpisode
    from app.services.background_job_service import JobLeaseLost, enqueue_job, recover_stale_jobs
    from app.services.decision_mutation_authority import validate_episode_projection
    from app.services.domain_write_fence import fence_domain_commits
    from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService

    setup = resolve_setup_configuration()
    lifecycle = resolve_lifecycle_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(db, extra_configurations=(setup, lifecycle))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(setup.setup_config()).build(context.tickers[0]).dto
        snapshot = SetupLifecycleRepository(setup.setup_config()).upsert_snapshot(db, dto)
        db.commit()
        snapshot_id = snapshot.id
        db.commit()
        barrier = Barrier(2)

        def advance():
            with Session(contextual_engine) as child:
                source = child.get(SetupSignalSnapshot, snapshot_id)
                barrier.wait(timeout=30)
                service = SetupLifecycleEpisodeService(config=lifecycle.setup_config())
                result = service.apply_snapshot(child, source)
                child.commit()
                return result.episode.id

        with ThreadPoolExecutor(max_workers=2) as workers:
            episode_ids = list(workers.map(lambda _: advance(), range(2)))
        assert episode_ids[0] == episode_ids[1]
        assert db.scalar(select(func.count()).select_from(SetupLifecycleEpisode)) == 1
        validate_episode_projection(db, db.get(SetupLifecycleEpisode, episode_ids[0]))
        db.commit()
        pipeline_id = db.scalar(select(PipelineRun.id).where(PipelineRun.upload_run_id == 7))
        queued = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": pipeline_id, "run_id": 7})
        db.commit()
        first = _claim_registered_job(db, "t14c-native-decision-A")
        assert first.id == queued.id
        token_a = first.execution_token
        first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert recover_stale_jobs(db, stale_after_seconds=1) == 1
        db.commit()
        second = _claim_registered_job(db, "t14c-native-decision-B")
        assert second.id == queued.id and second.execution_token != token_a
        token_b = second.execution_token
        db.commit()
        db.add(UploadRun(id=958, filename="stale-decision-token.csv", status="COMPLETED"))
        with fence_domain_commits(job_id=queued.id, execution_token=token_a):
            with pytest.raises(JobLeaseLost):
                SetupLifecycleRepository(setup.setup_config()).upsert_snapshot(db, dto)
        db.commit()
        assert db.get(UploadRun, 958) is None
        with fence_domain_commits(job_id=queued.id, execution_token=token_b):
            retry = SetupLifecycleRepository(setup.setup_config()).upsert_snapshot(db, dto)
            db.commit()
        assert retry.id == snapshot_id
        validate_episode_projection(db, db.get(SetupLifecycleEpisode, episode_ids[0]))


def test_native_transition_target_and_retrospective_episode_isolation(contextual_engine):
    from app.services.setup_lifecycle.decision_evidence import (
        persist_lifecycle_evaluation_evidence,
        persist_lifecycle_transition_evidence,
    )
    from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService

    config = resolve_setup_configuration()
    lifecycle_config = resolve_lifecycle_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(db, extra_configurations=(config, lifecycle_config))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(config.setup_config()).build(context.tickers[0]).dto
        repo = SetupLifecycleRepository(lifecycle_config.setup_config())
        snapshot = repo.upsert_snapshot(db, dto)
        lifecycle = SetupLifecycleEpisodeService(config=lifecycle_config.setup_config())
        applied = lifecycle.apply_snapshot(db, snapshot)
        db.commit()
        event = applied.lifecycle_event
        episode = applied.episode
        assert event is not None and episode is not None
        evaluation = db.get(SetupLifecycleEvaluationEvidence, episode.latest_evaluation_evidence_id)
        for field, value in (
            ("episode_id", episode.id + 9999),
            ("snapshot_id", snapshot.id + 9999),
            ("confidence_score", 99),
            ("source_event_key", "forged-source-event"),
            ("severity", "forged-severity"),
        ):
            candidate = type(event)(
                **{column.name: getattr(event, column.name) for column in event.__table__.columns}
            )
            setattr(candidate, field, value)
            db.add(UploadRun(id=993, filename="transition-attack.csv", status="COMPLETED"))
            with pytest.raises(ValueError, match="MUTATION_LIFECYCLE_TRANSITION_"):
                persist_lifecycle_transition_evidence(
                    db, event=candidate, evaluation=evaluation, prior_transition_evidence_id=None
                )
            db.commit()
            assert db.get(UploadRun, 993) is None
        assert (
            persist_lifecycle_transition_evidence(
                db, event=event, evaluation=evaluation, prior_transition_evidence_id=None
            ).id
            == episode.latest_transition_evidence_id
        )
        db.commit()
        replay = repo.create_evaluation_run(
            db,
            mode="REPLAY",
            status="RUNNING",
            engine_version=repo.config.engine.version,
            config_version=repo.config.engine.config_version,
            config_hash=repo.config.config_hash,
            source_run_id=7,
        )
        retrospective = persist_lifecycle_evaluation_evidence(
            db,
            snapshot=snapshot,
            episode=None,
            decision=applied.decision,
            actionability=applied.actionability,
            evaluation_run_id=replay.id,
            transition_eligible=False,
            effective_configuration=lifecycle.effective_configuration,
        )
        db.commit()
        assert retrospective.payload_json["execution_semantics"] == "CURRENT_RULES_RETROSPECTIVE"
        previous_evaluation_id = episode.latest_evaluation_evidence_id
        db.add(UploadRun(id=993, filename="retrospective-projection.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="CURRENT_EPISODE_MODE_REJECTED"):
            lifecycle.apply_snapshot(db, snapshot, evaluation_run_id=replay.id)
        db.commit()
        assert db.get(UploadRun, 993) is None
        assert episode.latest_evaluation_evidence_id == previous_evaluation_id


def test_native_signal_change_writer_pins_sources_recomputes_and_rejects_retry(contextual_engine):
    from app.services.setup_lifecycle.canonicalization import SetupLifecycleCanonicalizer
    from app.services.setup_lifecycle.change_authority import discover_change_sources
    from app.services.setup_lifecycle.change_detector import SetupLifecycleChangeDetector

    config = resolve_setup_configuration()
    repo = SetupLifecycleRepository(config.setup_config())
    builder = SetupLifecycleSnapshotBuilder(config.setup_config())
    canonicalizer = SetupLifecycleCanonicalizer(repository=repo, config=config.setup_config())
    detector = SetupLifecycleChangeDetector(repository=repo, config=config.setup_config())
    with Session(contextual_engine) as db:
        snapshots = []
        for run_id, cutoff_at, raw_values in (
            (7, datetime(2026, 9, 15, 21, tzinfo=UTC), None),
            (8, datetime(2026, 9, 16, 21, tzinfo=UTC), {}),
        ):
            cutoff, _, _ = seed_native_core(
                db,
                run_id=run_id,
                cutoff_at=cutoff_at,
                extra_configurations=(config,),
                raw_values=raw_values,
            )
            context = SetupLifecycleSourceLoader().load_run_context(
                db, run_id, market_cutoff=cutoff
            )
            snapshots.append(repo.upsert_snapshot(db, builder.build(context.tickers[0]).dto))
            canonicalizer.canonicalize_run(db, run_id=run_id, snapshot_ids=(snapshots[-1].id,))
            db.commit()
        previous, current = snapshots
        result = detector.detect_and_persist(db, evaluation_run_id=None, snapshot_ids=(current.id,))
        db.commit()
        assert result.created_events > 0
        event = repo.get_signal_change_events_by_ids(db, result.event_ids)[0]
        manifest = discover_change_sources(
            db, previous=previous, current=current, history=(previous,)
        )
        from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
        from app.services.setup_lifecycle.change_authority import validate_signal_change

        rechecked = validate_signal_change(
            db, event, configuration=config, source_manifest=manifest
        )
        retained = event.evidence_json["native_change_proof"]
        assert {
            key: retained.get(key)
            for key in rechecked
            if Canonical.dumps(retained.get(key)) != Canonical.dumps(rechecked[key])
        } == {}, "retained native change proof must match identical retry"
        assert (
            repo.add_signal_change_event(
                db, event, effective_configuration=config, source_manifest=manifest
            ).id
            == event.id
        )
        db.commit()
        candidate = type(event)(
            **{column.name: getattr(event, column.name) for column in event.__table__.columns}
        )
        candidate.new_value_json = {"value": "forged"}
        db.add(UploadRun(id=991, filename="altered-change.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="CHANGE_NATIVE_OUTPUT_MISMATCH"):
            repo.add_signal_change_event(
                db, candidate, effective_configuration=config, source_manifest=manifest
            )
        db.commit()
        assert db.get(UploadRun, 991) is None
        assert (
            detector.detect_and_persist(
                db, evaluation_run_id=None, snapshot_ids=(current.id,)
            ).created_events
            == 0
        )
        db.commit()
        db.add(UploadRun(id=991, filename="missing-change-authority.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="CHANGE_EXACT_AUTHORITY_REQUIRED"):
            repo.add_signal_change_event(db, event)
        db.commit()
        assert db.get(UploadRun, 991) is None


def test_native_supporting_writer_cannot_borrow_another_session(contextual_engine):
    from app.models.tables import SetupLifecycleEpisode
    from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService

    config = resolve_setup_configuration()
    lifecycle_config = resolve_lifecycle_configuration()
    with Session(contextual_engine) as db, Session(contextual_engine) as unrelated:
        cutoff, _, _ = seed_native_core(db, extra_configurations=(config, lifecycle_config))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(config.setup_config()).build(context.tickers[0]).dto
        snapshot = SetupLifecycleRepository(config.setup_config()).upsert_snapshot(db, dto)
        db.commit()

        repository = SetupLifecycleRepository(config.setup_config())
        for invoke in (
            lambda: repository.record_snapshot_canonical_decisions(
                db, [(snapshot, "forged", {"selected_snapshot_id": snapshot.id})]
            ),
            lambda: repository.record_snapshot_canonical_decision(
                db, snapshot, reason="forged", decision={"selected_snapshot_id": snapshot.id}
            ),
        ):
            db.add(UploadRun(id=994, filename="unowned-canonical.csv", status="COMPLETED"))
            with pytest.raises(ValueError, match="SUPPORTING_SEMANTIC_TRANSACTION_REQUIRED"):
                invoke()
            db.commit()
            assert db.get(UploadRun, 994) is None
            assert snapshot.is_canonical is False

        class CrossSessionRepository(SetupLifecycleRepository):
            def add(self, session, row):
                if isinstance(row, SetupLifecycleEpisode):
                    unrelated.add(
                        UploadRun(id=995, filename="borrowed-session.csv", status="COMPLETED")
                    )
                    return super().add(unrelated, row)
                return super().add(session, row)

        service = SetupLifecycleEpisodeService(
            repository=CrossSessionRepository(), config=lifecycle_config.setup_config()
        )
        db.add(UploadRun(id=994, filename="parent-session.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="SUPPORTING_SEMANTIC_TRANSACTION_REQUIRED"):
            service.apply_snapshot(db, snapshot)
        db.commit()
        unrelated.commit()
        assert db.get(UploadRun, 994) is None
        assert unrelated.get(UploadRun, 995) is None
        assert db.scalar(select(func.count()).select_from(SetupLifecycleEvaluationEvidence)) == 0


def test_native_first_capture_single_and_batch_converge_concurrently(contextual_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    config = resolve_setup_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(db, extra_configurations=(config,))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(config.setup_config()).build(context.tickers[0]).dto
        db.commit()
    barrier = Barrier(2)

    def capture(batch):
        with Session(contextual_engine) as db:
            repo = SetupLifecycleRepository(config.setup_config())
            barrier.wait(timeout=15)
            snapshot = (
                repo.upsert_snapshots(db, [dto])[0] if batch else repo.upsert_snapshot(db, dto)
            )
            db.commit()
            return snapshot.id, snapshot.evidence_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(capture, False), pool.submit(capture, True)
        assert first.result(timeout=60) == second.result(timeout=60)
    with Session(contextual_engine) as db:
        assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 1
        snapshot = db.scalar(select(SetupSignalSnapshot))
        validate_setup_projection(db, snapshot)
        assert (
            db.scalar(
                select(func.count())
                .select_from(CoreCalculationEvidence)
                .where(CoreCalculationEvidence.artifact_kind == "SETUP")
            )
            == 1
        )


def test_native_setup_selection_and_bookkeeping_purge(contextual_engine):
    from app.models.tables import SetupLifecycleEvaluationRun, SetupSignalSnapshotCurrentSelection
    from app.services.setup_lifecycle.canonicalization import SetupLifecycleCanonicalizer
    from app.services.setup_lifecycle.config import load_setup_lifecycle_config
    from app.services.setup_lifecycle.purge_service import (
        SetupLifecyclePurgeExecuteRequest,
        SetupLifecyclePurgeService,
    )
    from app.services.setup_lifecycle.repository import PurgeScope

    config = resolve_setup_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(db, extra_configurations=(config,))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(config.setup_config()).build(context.tickers[0]).dto
        repo = SetupLifecycleRepository(config.setup_config())
        snapshot = repo.upsert_snapshot(db, dto)
        db.commit()
        canonicalizer = SetupLifecycleCanonicalizer(repository=repo, config=config.setup_config())
        assert canonicalizer.canonicalize_snapshots(db, [snapshot]).changed_count == 1
        db.commit()
        assert canonicalizer.canonicalize_snapshots(db, [snapshot]).changed_count == 0
        db.commit()
        selection = db.scalar(select(SetupSignalSnapshotCurrentSelection))
        assert selection.selected_snapshot_id == snapshot.id
        original_id = selection.selected_snapshot_id
        snapshot.dual_score = 99
        with pytest.raises(ValueError, match="PROJECTION_PAYLOAD_MISMATCH"):
            repo.advance_canonical_selection(db, snapshot, reason="forged-choice")
        db.commit()
        assert selection.selected_snapshot_id == original_id

        native_config = load_setup_lifecycle_config()
        metadata_repo = SetupLifecycleRepository(native_config)
        run = metadata_repo.create_evaluation_run(
            db,
            mode="REPLAY",
            status="RUNNING",
            engine_version=native_config.engine.version,
            config_version=native_config.engine.config_version,
            config_hash=native_config.config_hash,
        )
        db.commit()
        assert db.get(SetupLifecycleEvaluationRun, run.id).mode == "REPLAY"
        metadata_repo.complete_evaluation_run(db, run, status="COMPLETED")
        db.commit()
        run_id = run.id
        enabled = replace(
            native_config, retention=replace(native_config.retention, purge_enabled=True)
        )
        purge_repo = SetupLifecycleRepository(enabled)
        purge = SetupLifecyclePurgeService(config=enabled, repository=purge_repo)
        # Purge bookkeeping from this exact metadata run; retained financial
        # Setup/evaluation evidence and its projection remain outside the scope.
        preview = purge.preview(db, PurgeScope(evaluation_run_id=run.id))
        with pytest.raises(ValueError, match="SUPPORTING_SEMANTIC_TRANSACTION_REQUIRED"):
            purge_repo._execute_purge_unchecked(db, preview, preview.token)
        db.commit()
        removed = purge.execute(
            db,
            SetupLifecyclePurgeExecuteRequest(
                preview=preview,
                confirmation_token=preview.token,
                requester="native-test-operator",
                reason="explicit disposable metadata retention test",
            ),
        )
        db.commit()
        assert removed["evaluation_runs"] == 1
        assert db.get(SetupLifecycleEvaluationRun, run_id) is None
        assert db.get(SetupSignalSnapshot, snapshot.id).evidence_id is not None


def test_comprehensive_ceri_fixture_processes_current_native_sources(
    contextual_engine, monkeypatch
):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification.fixtures import _seed_ceri_manual_evidence

    from app.models.ceri_tables import CeriEstimateSnapshot
    from app.services.market_clock_service import MarketClockService

    with Session(contextual_engine) as db:
        session = (
            MarketClockService()
            .cutoff_for(datetime.now(UTC), reason="CERTIFICATION_NATIVE_CURRENT_INPUT_FIXTURE")
            .latest_completed_session
        )
        _seed_ceri_manual_evidence(db, as_of_session=session)
        db.commit()
        assert db.scalar(select(func.count()).select_from(CeriEstimateSnapshot)) >= 18


def test_native_ceri_score_change_recomputes_sources_and_retry(contextual_engine, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification.fixtures import _seed_ceri_manual_evidence

    from app.models.ceri_tables import CeriChangeEvent, CeriScoreSnapshot
    from app.models.tables import RawCompanyRow
    from app.services.ceri.capture_service import CeriRunCaptureService
    from app.services.ceri.change_detection_service import CeriChangeDetectionService, _severity
    from app.services.ceri.change_semantics import ComparisonState
    from app.services.ceri.enums import CeriChangeType
    from app.services.ceri.snapshot_service import CeriSnapshotService
    from app.services.contextual_effective_configuration import resolve_ceri_configuration
    from app.services.market_clock_service import MarketClockService

    with Session(contextual_engine) as db:
        clock = MarketClockService()
        _seed_ceri_manual_evidence(
            db,
            as_of_session=clock.cutoff_for(
                datetime.now(UTC), reason="T14C_NATIVE_CERI_SOURCE_FIXTURE"
            ).latest_completed_session,
        )
        db.commit()
        baseline = db.scalar(select(CeriScoreSnapshot))
        db.add(UploadRun(id=777, filename="ceri-current-native.csv", status="COMPLETED"))
        db.flush()
        db.add(RawCompanyRow(run_id=777, row_number=1, ticker="ALFA", raw_json={}))
        db.commit()
        snapshot_service = CeriSnapshotService()
        frozen = resolve_ceri_configuration(
            snapshot_service.config,
            consumer={
                "run_capture": True,
                "revision_feature_config_hash": snapshot_service.config.config_hash,
                "ibmi_enabled": False,
                "volatility_enabled": False,
                "short_pressure_enabled": False,
                "volatility": {"ceri_risk_max_contribution": 1.5},
            },
        )
        cutoff = clock.cutoff_for(datetime.now(UTC), reason="T14C_NATIVE_CERI_CHANGE_CAPTURE")
        result = CeriRunCaptureService(snapshot_service=snapshot_service).capture_run(
            db,
            777,
            force=True,
            market_cutoff=cutoff,
            effective_configuration=frozen,
        )
        assert result.failed == 0 and result.score_snapshots == 1 and result.change_events > 0, (
            result
        )
        db.commit()
        current = db.scalar(select(CeriScoreSnapshot).where(CeriScoreSnapshot.run_id == 777))
        event = db.scalar(
            select(CeriChangeEvent).where(CeriChangeEvent.to_snapshot_id == current.id)
        )
        assert (
            event.delta_json["native_change_proof"]["artifact_role"] == "SUPPORTING_CHANGE_LEDGER"
        )
        service = CeriChangeDetectionService()
        change_type = CeriChangeType(event.change_type)
        delta = {
            key: value
            for key, value in event.delta_json.items()
            if key not in {"native_change_proof", "effective_configuration_at_creation"}
        }
        kwargs = dict(
            company_id=current.company_id,
            change_type=change_type,
            severity=_severity(delta),
            effective_session=current.as_of_session,
            scope="native-retry",
            from_snapshot_id=baseline.id,
            to_snapshot_id=current.id,
            delta=delta,
            config_hash=current.config_hash,
            calculation_version=current.calculation_version,
            comparison_state=ComparisonState.COMPARABLE,
        )
        assert service._persist_change(db, **kwargs)[0].id == event.id
        db.commit()
        count = db.scalar(select(func.count()).select_from(CeriChangeEvent))
        db.add(UploadRun(id=989, filename="forged-ceri-delta.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="NATIVE_SCORE_DELTA_MISMATCH"):
            service._persist_change(db, **{**kwargs, "delta": {**delta, "delta": 999}})
        db.commit()
        assert db.get(UploadRun, 989) is None
        assert db.scalar(select(func.count()).select_from(CeriChangeEvent)) == count
        from app.models.ceri_tables import CeriAlertRule
        from app.services.ceri.alert_service import CeriAlertService
        from app.services.ceri.feature_flags import CeriFeatureFlags

        monkeypatch.setattr(
            "app.services.ceri.alert_service.ceri_flags",
            lambda: CeriFeatureFlags(True, True, True, True, True, True, True),
        )
        from app.services.ceri.config import AlertRuleConfig, load_ceri_config

        opportunity = event
        native_type = CeriChangeType(event.change_type)
        alert_native = load_ceri_config()
        alert_native = replace(
            alert_native,
            alerts=replace(
                alert_native.alerts,
                rules={
                    **alert_native.alerts.rules,
                    native_type: AlertRuleConfig(native_type, True, "NOTABLE", native_type, 5),
                },
            ),
        )
        alerts = CeriAlertService(config=alert_native, alerts_enabled=True)
        notification = alerts.persist_alert_for_change(db, change=opportunity, ticker="ALFA")
        assert notification is not None
        db.commit()
        db.add(UploadRun(id=778, filename="ceri-second-native.csv", status="COMPLETED"))
        db.flush()
        db.add(RawCompanyRow(run_id=778, row_number=1, ticker="ALFA", raw_json={}))
        db.commit()
        second_cutoff = clock.cutoff_for(
            datetime.now(UTC), reason="T14C_NATIVE_CERI_COOLDOWN_SOURCE"
        )
        second_result = CeriRunCaptureService(snapshot_service=snapshot_service).capture_run(
            db,
            778,
            force=True,
            market_cutoff=second_cutoff,
            effective_configuration=frozen,
        )
        assert second_result.failed == 0 and second_result.score_snapshots == 1
        db.commit()
        second = db.scalar(select(CeriScoreSnapshot).where(CeriScoreSnapshot.run_id == 778))
        native_delta = service._score_changes(second, baseline)[native_type]
        distinct, created = service._persist_change(
            db,
            company_id=second.company_id,
            change_type=native_type,
            severity=_severity(native_delta),
            effective_session=second.as_of_session,
            scope="native_explicit_comparison",
            delta=native_delta,
            config_hash=second.config_hash,
            calculation_version=second.calculation_version,
            from_snapshot_id=baseline.id,
            to_snapshot_id=second.id,
            comparison_state=ComparisonState.COMPARABLE,
        )
        assert created and distinct.id != opportunity.id
        db.commit()
        assert alerts.persist_alert_for_change(db, change=distinct, ticker="ALFA") is None
        db.commit()
        rule_row = db.get(CeriAlertRule, notification.alert_rule_id)
        assert alerts._within_cooldown(db, rule_row, "ALFA", distinct)
        from app.models.ceri_tables import CeriAlertEvent

        assert db.scalar(select(func.count()).select_from(CeriAlertEvent)) == 1


def test_native_ceri_normalized_changes_require_exact_sources_and_time(
    contextual_engine, monkeypatch
):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification.fixtures import _seed_ceri_manual_evidence

    from app.models.ceri_tables import (
        CeriCatalystEvent,
        CeriCatalystEventRevision,
        CeriChangeEvent,
        CeriGuidanceEvent,
    )
    from app.services.ceri.change_detection_service import CeriChangeDetectionService
    from app.services.market_clock_service import MarketClockService

    with Session(contextual_engine) as db:
        clock = MarketClockService()
        initial = clock.cutoff_for(datetime.now(UTC), reason="T14C_NORMALIZED_SOURCE_FIXTURE")
        _seed_ceri_manual_evidence(db, as_of_session=initial.latest_completed_session)
        db.commit()
        from app.models.ceri_tables import CeriProcessingRun
        from app.services.ceri.enums import CeriDataset
        from app.services.ceri.normalization_service import CeriNormalizationService
        from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
        from app.services.ceri.provider_registry import CeriProviderRegistry
        from app.services.ceri.providers.manual_provider import ManualCeriProvider

        provider = ManualCeriProvider(
            {
                CeriDataset.CATALYSTS: [
                    {
                        "provider_record_id": "t14c-native-relevant-catalyst",
                        "ticker": "ALFA",
                        "category": "PRODUCT",
                        "subject": "Native issuer product launch",
                        "status": "ANNOUNCED",
                        "direction": "POSITIVE",
                        "materiality": 0.7,
                        "issuer_relevance": True,
                        "issuer_relevance_reason": "Issuer launch",
                        "announced_at": "2026-08-07T19:00:00Z",
                        "published_at": "2026-08-07T19:00:00Z",
                    }
                ]
            }
        )
        ingestion = CeriIngestionService(
            registry=CeriProviderRegistry(providers={"manual": provider})
        ).ingest(
            db,
            CeriIngestionRequest(
                provider="manual",
                dataset=CeriDataset.CATALYSTS,
                ticker="ALFA",
                request_key="t14c-native-relevant-catalyst",
            ),
        )
        db.commit()
        processing = CeriProcessingRun(
            job_type="CERI_NORMALIZE",
            status="RUNNING",
            deterministic_request_key="t14c-native-relevant-catalyst-normalization",
        )
        db.add(processing)
        db.flush()
        normalized = CeriNormalizationService().normalize(
            db,
            processing_run=processing,
            ingestion_run_id=ingestion.ingestion_run_id,
        )
        assert normalized.failed == 0
        db.commit()
        cutoff = clock.cutoff_for(datetime.now(UTC), reason="T14C_NORMALIZED_CHANGE_OPERATION")
        service = CeriChangeDetectionService()
        guidance = db.scalar(
            select(CeriGuidanceEvent).where(
                CeriGuidanceEvent.accepted_for_scoring.is_(True),
                CeriGuidanceEvent.action == "RAISED",
            )
        )
        assert guidance is not None
        revision = db.scalar(
            select(CeriCatalystEventRevision).where(
                CeriCatalystEventRevision.issuer_relevance.is_(True)
            )
        )
        assert revision is not None
        company_id = db.get(CeriCatalystEvent, revision.catalyst_event_id).company_id
        result = service.detect_guidance_change(
            db,
            guidance=guidance,
            company_id=guidance.company_id,
            market_cutoff=cutoff,
        )
        assert result.changes == 1
        catalyst = service.detect_catalyst_revision(
            db,
            revision=revision,
            company_id=company_id,
            market_cutoff=cutoff,
        )
        assert catalyst.changes == 1
        db.commit()
        event = db.get(CeriChangeEvent, result.change_ids[0])
        assert (
            event.delta_json["native_change_proof"]["source_kind"] == "NORMALIZED_EVENT_DERIVATION"
        )
        from app.models.ceri_tables import CeriCompany
        from app.services.ceri.alert_service import CeriAlertService
        from app.services.ceri.feature_flags import CeriFeatureFlags

        monkeypatch.setattr(
            "app.services.ceri.alert_service.ceri_flags",
            lambda: CeriFeatureFlags(True, True, True, True, True, True, True),
        )
        from dataclasses import replace

        from app.services.ceri.config import AlertRuleConfig, load_ceri_config
        from app.services.ceri.enums import CeriChangeType

        config = load_ceri_config()
        config = replace(
            config,
            alerts=replace(
                config.alerts,
                rules={
                    **config.alerts.rules,
                    CeriChangeType.GUIDANCE_RAISED: AlertRuleConfig(
                        CeriChangeType.GUIDANCE_RAISED,
                        True,
                        "NOTABLE",
                        CeriChangeType.GUIDANCE_RAISED,
                        5,
                    ),
                },
            ),
        )
        alerts = CeriAlertService(config=config, alerts_enabled=True)
        ticker = db.get(CeriCompany, guidance.company_id).ticker
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from app.models.ceri_tables import CeriAlertEvent

        change_id = event.id
        barrier = Barrier(2)

        def first_notification():
            with Session(contextual_engine) as child:
                source = child.get(CeriChangeEvent, change_id)
                consumer = CeriAlertService(config=config, alerts_enabled=True)
                barrier.wait(timeout=60)
                created = consumer.persist_alert_for_change(child, change=source, ticker=ticker)
                identifier = created.id if created is not None else None
                child.commit()
                return identifier

        with ThreadPoolExecutor(max_workers=2) as workers:
            identifiers = list(workers.map(lambda _: first_notification(), range(2)))
        assert sum(identifier is not None for identifier in identifiers) == 1
        alert = db.get(CeriAlertEvent, next(identifier for identifier in identifiers if identifier))
        assert alert is not None
        assert db.scalar(select(func.count()).select_from(CeriAlertEvent)) == 1
        db.commit()
        assert alerts.persist_alert_for_change(db, change=event, ticker=ticker) is None
        db.commit()
        from app.models.ceri_tables import CeriAlertRule

        physical_rule = db.get(CeriAlertRule, alert.alert_rule_id)
        original_payload = deepcopy(alert.evidence_json)
        physical_rule.enabled = False
        physical_rule.severity = "RISK"
        db.commit()
        assert (
            CeriAlertService(config=config, alerts_enabled=True).persist_alert_for_change(
                db,
                change=event,
                ticker=ticker,
            )
            is None
        )
        db.commit()
        assert alert.evidence_json == original_payload
        assert alerts.persist_alert_for_change(db, change=event, ticker=ticker) is None
        db.commit()
        with pytest.raises(ValueError, match="SEMANTIC_TRANSACTION_REQUIRED"):
            alerts._rule_for_change(db, event)
        db.commit()
        alerts.acknowledge(db, alert)
        db.commit()
        alerts.dismiss(db, alert)
        db.commit()
        assert alert.status == "DISMISSED"
        db.add(UploadRun(id=987, filename="ceri-alert-source-attack.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="COMPANY_TICKER_MISMATCH"):
            alerts.persist_alert_for_change(db, change=event, ticker="OTHER")
        db.commit()
        assert db.get(UploadRun, 987) is None
        alert.severity = "FORGED"
        with pytest.raises(ValueError, match="NOTIFICATION_BODY_MISMATCH"):
            alerts.acknowledge(db, alert)
        db.commit()
        assert alert.severity != "FORGED"
        assert (
            service.detect_guidance_change(
                db,
                guidance=guidance,
                company_id=guidance.company_id,
                market_cutoff=cutoff,
            ).duplicates
            == 1
        )
        db.commit()
        count = db.scalar(select(func.count()).select_from(CeriChangeEvent))
        for attack, expected in (
            ({"market_cutoff": None}, "EXPLICIT_OPERATION_TIME_REQUIRED"),
            ({"company_id": company_id + 10000}, "NORMALIZED_SCOPE_MISMATCH"),
            ({"prior_action": "LOWERED"}, "NATIVE_NORMALIZED_DELTA_MISMATCH"),
        ):
            db.add(UploadRun(id=988, filename="normalized-change-attack.csv", status="COMPLETED"))
            with pytest.raises(ValueError, match=expected):
                service.detect_guidance_change(
                    db,
                    **{
                        "guidance": guidance,
                        "company_id": guidance.company_id,
                        "market_cutoff": cutoff,
                        **attack,
                    },
                )
            db.commit()
            assert db.get(UploadRun, 988) is None
            assert db.scalar(select(func.count()).select_from(CeriChangeEvent)) == count
        guidance.point_value = 999
        with pytest.raises(ValueError, match="NORMALIZED_SOURCE.*MISMATCH"):
            service.detect_guidance_change(
                db,
                guidance=guidance,
                company_id=guidance.company_id,
                market_cutoff=cutoff,
            )
        db.commit()
        assert guidance.point_value != 999
        original_company = guidance.company_id
        from app.models.ceri_tables import CeriCompany

        other_company = db.scalar(select(CeriCompany).where(CeriCompany.id != original_company))
        guidance.company_id = other_company.id
        db.add(UploadRun(id=984, filename="normalized-company-forgery.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="NORMALIZED_COMPANY_SOURCE_REQUIRED"):
            service.detect_guidance_change(
                db, guidance=guidance, company_id=other_company.id, market_cutoff=cutoff
            )
        db.commit()
        assert guidance.company_id == original_company and db.get(UploadRun, 984) is None


def test_comprehensive_csv_has_native_ready_positive_and_degraded_sources(
    contextual_engine, monkeypatch, tmp_path
):
    import csv
    from pathlib import Path

    from native_mutation_support import bound_pipeline

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification.fixtures import write_canonical_csv

    from app.models.tables import FundamentalScore, RawCompanyRow
    from app.services.column_mapper import map_csv_rows
    from app.services.core_effective_configuration import resolve_fundamental_configuration
    from app.services.fundamental_score_service import recalculate_run_fundamentals
    from app.services.producer_readiness import readiness_from_evidence

    csv_path = tmp_path / "native-positive.csv"
    write_canonical_csv(csv_path)
    with csv_path.open(encoding="utf-8") as handle:
        mapped = map_csv_rows(list(csv.DictReader(handle)))
    frozen = resolve_fundamental_configuration()
    with Session(contextual_engine) as db:
        db.add(UploadRun(id=993, filename="native-positive.csv", status="COMPLETED"))
        db.flush()
        for row in mapped:
            db.add(
                RawCompanyRow(
                    run_id=993,
                    row_number=row.row_number,
                    ticker=row.ticker,
                    company_name=row.company_name,
                    sector=row.sector,
                    raw_json=row.raw,
                )
            )
        db.flush()
        cutoff, pipeline_id = bound_pipeline(db, 993, (frozen,))
        recalculate_run_fundamentals(
            db,
            993,
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_id,
            effective_configuration=frozen,
        )
        db.commit()
        scores = {row.ticker: row for row in db.scalars(select(FundamentalScore))}
        alfa = scores["ALFA"]
        assert alfa.data_coverage_score == 10 and alfa.missing_data_penalty == 0
        assert (
            readiness_from_evidence(db.get(CoreCalculationEvidence, alfa.evidence_id)).status.value
            == "READY"
        )
        brav = scores["BRAV"]
        assert (
            readiness_from_evidence(db.get(CoreCalculationEvidence, brav.evidence_id)).status.value
            == "DEGRADED"
        )


def test_worker_heartbeat_uses_child_transaction_retaining_fence(contextual_engine):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.tables import BackgroundJob
    from app.services.background_job_service import enqueue_job
    from app.services.background_worker import run_worker_once
    from app.services.domain_write_fence import (
        assert_current_execution_ownership,
        current_fenced_domain_session,
    )

    engine = create_engine(
        contextual_engine.url,
        connect_args={"options": "-c statement_timeout=3000 -c lock_timeout=1500"},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as db:
            queued = enqueue_job(db, "T14C_HEARTBEAT_CHILD", {})
            db.commit()
            job_id = queued.id

        def handler(parent, job):
            with factory() as child:
                assert_current_execution_ownership(
                    child, job_id=job.id, execution_token=job.execution_token
                )
                assert current_fenced_domain_session(job.id, job.execution_token) is child
                job._heartbeat()
                assert current_fenced_domain_session(job.id, job.execution_token) is child
                assert child.get(BackgroundJob, job.id).heartbeat_at is not None
                child.commit()
                assert current_fenced_domain_session(job.id, job.execution_token) is None
            return {"child_heartbeat": True}

        assert run_worker_once(
            worker_id="t14c-child-heartbeat",
            stale_after_seconds=60,
            session_factory=factory,
            handlers={"T14C_HEARTBEAT_CHILD": handler},
        )
        with factory() as db:
            completed = db.get(BackgroundJob, job_id)
            assert completed.status == "COMPLETED", completed.error_message
            assert completed.result_json == {"child_heartbeat": True}
            assert completed.execution_token is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("durable_global", (False, True))
def test_native_gap_maintenance_retains_source_origin_and_new_operation_time(
    contextual_engine, durable_global
):
    from app.models.tables import SetupLifecycleEpisode, SetupLifecycleTransitionEvidence
    from app.services.calculation_identity import CalculationIdentity
    from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService
    from app.services.setup_lifecycle.maintenance_service import SetupLifecycleMaintenanceService

    setup_config = resolve_setup_configuration()
    lifecycle_config = resolve_lifecycle_configuration()
    with Session(contextual_engine) as db:
        original_cutoff, _, _ = seed_native_core(
            db, extra_configurations=(setup_config, lifecycle_config)
        )
        context = SetupLifecycleSourceLoader().load_run_context(
            db, 7, market_cutoff=original_cutoff
        )
        dto = (
            SetupLifecycleSnapshotBuilder(setup_config.setup_config()).build(context.tickers[0]).dto
        )
        snapshot = SetupLifecycleRepository(setup_config.setup_config()).upsert_snapshot(db, dto)
        episode_service = SetupLifecycleEpisodeService(config=lifecycle_config.setup_config())
        applied = episode_service.apply_snapshot(db, snapshot)
        db.commit()
        assert applied.episode is not None, applied.decision
        episode = db.get(SetupLifecycleEpisode, applied.episode.id)
        prior_evaluation_id = episode.latest_evaluation_evidence_id
        db.add(UploadRun(id=999, filename="rejected-gap-staged-row", status="COMPLETED"))
        with pytest.raises(ValueError, match="GAP_TEMPORAL_AUTHORITY_REQUIRED"):
            episode_service.apply_observation_gap(
                db,
                ticker=episode.ticker,
                timeframe=episode.timeframe,
                setup_family=applied.decision.setup_family,
                observed_on=date(2026, 10, 30),
                market_cutoff=original_cutoff,
            )
        db.commit()
        assert db.get(UploadRun, 999) is None
        assert episode.latest_evaluation_evidence_id == prior_evaluation_id
        maintenance = SetupLifecycleMaintenanceService(episode_service=episode_service)
        # Sunday after US daylight-saving time changes still selects Friday.
        if durable_global:
            from sqlalchemy.orm import sessionmaker

            from app.models.tables import BackgroundJob
            from app.services.background_job_service import enqueue_job
            from app.services.background_worker import run_worker_once
            from app.services.setup_lifecycle.job_handlers import execute_daily_maintenance_job

            queued = enqueue_job(
                db,
                "SETUP_LIFECYCLE_DAILY_MAINTENANCE",
                {
                    "as_of_date": "2026-11-01",
                    "market_session_completed": True,
                },
            )
            db.commit()
            assert queued.related_run_id is None
            assert run_worker_once(
                worker_id="t14c-global-maintenance",
                stale_after_seconds=60,
                session_factory=sessionmaker(bind=contextual_engine, expire_on_commit=False),
                handlers={"SETUP_LIFECYCLE_DAILY_MAINTENANCE": execute_daily_maintenance_job},
            )
            db.expire_all()
            completed = db.get(BackgroundJob, queued.id)
            assert completed.status == "COMPLETED", completed.error_message
            assert completed.result_json["expired"] == 1
        else:
            result = maintenance.daily_maintenance(db, as_of_date=date(2026, 11, 1))
            db.commit()
            assert result.expired == 1
        assert episode.current_as_of_date == date(2026, 10, 30)
        transition = db.get(SetupLifecycleTransitionEvidence, episode.latest_transition_evidence_id)
        evaluation = db.get(
            type(applied.lifecycle_evaluation_evidence), transition.evaluation_evidence_id
        )
        identity = CalculationIdentity.from_canonical_payload(
            evaluation.payload_json["calculation_identity"]
        )
        assert evaluation.prior_evaluation_evidence_id == prior_evaluation_id
        assert evaluation.setup_evidence_id == snapshot.evidence_id
        assert identity.ownership.run_id.value == 7
        assert identity.temporal.calculation_cutoff.value != original_cutoff.cutoff_at
        assert identity.temporal.as_of_session.value == date(2026, 10, 30)
        assert transition.from_state == evaluation.previous_state
        assert transition.to_state == evaluation.output_state == "EXPIRED"
        assert evaluation.payload_json["execution_semantics"] == "CURRENT_STATE_REPAIR"
        with pytest.raises(ValueError, match="MUTATION_LIFECYCLE_EPISODE_REGRESSION"):
            episode_service.apply_snapshot(db, snapshot)
        db.commit()
        assert episode.status == "CLOSED"


def test_native_gate_blocked_alert_uses_exact_evaluation_predecessor(contextual_engine):
    from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
    from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService

    setup = resolve_setup_configuration()
    base = resolve_lifecycle_configuration().setup_config()
    lifecycle_config = resolve_lifecycle_configuration(
        replace(
            base,
            actionability={
                **base.actionability,
                "minimum_actionable_confidence": 0,
            },
        )
    )
    with Session(contextual_engine) as db:
        alerts = SetupLifecycleAlertService()
        rules = alerts.seed_builtin_rules(db)
        frozen_alerts = resolve_alert_configuration(rules=rules)
        alerts._prepare_rules(db, rules)
        lifecycle = SetupLifecycleEpisodeService(config=lifecycle_config.setup_config())
        results = []
        for run_id, day, values in (
            (7, 15, _native_liquidity_source()),
            (8, 16, _native_liquidity_source(weak=True)),
        ):
            cutoff, _, _ = seed_native_core(
                db,
                run_id=run_id,
                cutoff_at=datetime(2026, 9, day, 21, tzinfo=UTC),
                raw_values=values,
                extra_configurations=(setup, lifecycle_config, frozen_alerts),
            )
            context = SetupLifecycleSourceLoader().load_run_context(
                db, run_id, market_cutoff=cutoff
            )
            snapshot = SetupLifecycleRepository(setup.setup_config()).upsert_snapshot(
                db,
                SetupLifecycleSnapshotBuilder(setup.setup_config()).build(context.tickers[0]).dto,
            )
            results.append(lifecycle.apply_snapshot(db, snapshot))
            db.commit()
        assert results[0].actionability.actionability.value in {"ACTIONABLE", "WATCH_ONLY"}, (
            results[0]
        )
        assert results[1].actionability.actionability.value == "BLOCKED", results[1]
        result = alerts.evaluate_episode_result(db, results[1], rules=rules)
        db.commit()
        assert result.created >= 1
        decision = db.scalar(
            select(SignalAlertDecisionEvidence).where(
                SignalAlertDecisionEvidence.semantic_key == f"GATE_BLOCKED:{results[1].episode.id}"
            )
        )
        assert (
            decision is not None
            and decision.lifecycle_evaluation_evidence_id
            == results[1].lifecycle_evaluation_evidence.id
        )


def test_native_signal_alert_cooldown_expiry_and_configuration_drift(contextual_engine):
    from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
    from app.services.setup_lifecycle.canonicalization import SetupLifecycleCanonicalizer
    from app.services.setup_lifecycle.change_detector import SetupLifecycleChangeDetector

    config = resolve_setup_configuration()
    repo = SetupLifecycleRepository(config.setup_config())
    builder = SetupLifecycleSnapshotBuilder(config.setup_config())
    canonicalizer = SetupLifecycleCanonicalizer(repository=repo, config=config.setup_config())
    detector = SetupLifecycleChangeDetector(repository=repo, config=config.setup_config())
    with Session(contextual_engine) as db:
        rule = SignalAlertRule(
            rule_id="T14C_SIGNAL_NATIVE",
            enabled=True,
            severity="INFO",
            scope="signal_change",
            cooldown_sessions=2,
            minimum_confidence=0,
            config_version="t14c-v1",
            condition_json={
                "signal_keys": [
                    item.key for item in config.setup_config().signal_registry.definitions()
                ]
            },
            market_restrictions_json={},
            metadata_json={},
        )
        db.add(rule)
        db.flush()
        frozen = resolve_alert_configuration(rules=(rule,))
        alerts = SetupLifecycleAlertService()
        alerts._prepare_rules(db, (rule,))
        outcomes = []
        events = []
        for offset, (day, weak) in enumerate(
            ((14, False), (15, True), (16, False), (17, True), (23, False), (24, True))
        ):
            run_id = 7 + offset
            cutoff, _, _ = seed_native_core(
                db,
                run_id=run_id,
                cutoff_at=datetime(2026, 9, day, 21, tzinfo=UTC),
                raw_values=_native_liquidity_source(weak=weak),
                extra_configurations=(config, frozen),
            )
            context = SetupLifecycleSourceLoader().load_run_context(
                db, run_id, market_cutoff=cutoff
            )
            snapshot = repo.upsert_snapshot(db, builder.build(context.tickers[0]).dto)
            canonicalizer.canonicalize_run(db, run_id=run_id, snapshot_ids=(snapshot.id,))
            db.commit()
            changes = detector.detect_and_persist(
                db, evaluation_run_id=None, snapshot_ids=(snapshot.id,)
            )
            db.commit()
            if weak:
                native_events = repo.get_signal_change_events_by_ids(db, changes.event_ids)
                assert native_events, changes
                event = (
                    native_events[0]
                    if not events
                    else next(
                        event for event in native_events if event.signal_key == events[0].signal_key
                    )
                )
                events.append(event)
                outcomes.append(alerts.evaluate_signal_change_events(db, (event,)))
                db.commit()
        assert [(result.created, result.suppressed) for result in outcomes] == [
            (1, 0),
            (0, 1),
            (1, 0),
        ]
        decisions = list(
            db.scalars(select(SignalAlertDecisionEvidence).order_by(SignalAlertDecisionEvidence.id))
        )
        assert [decision.decision for decision in decisions] == [
            "GENERATED",
            "SUPPRESSED_COOLDOWN",
            "GENERATED",
        ]
        assert decisions[1].cooldown_predecessor_evidence_id == decisions[0].id
        retained_config = decisions[0].payload_json["effective_configuration_at_creation"]
        rule.minimum_confidence = 100
        rule.severity = "RISK"
        db.commit()
        count = db.scalar(select(func.count()).select_from(SignalAlertDecisionEvidence))
        db.add(UploadRun(id=985, filename="alert-config-drift.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="RETAINED_CONFIGURATION_MISMATCH"):
            SetupLifecycleAlertService().evaluate_signal_change_events(db, (events[-1],))
        db.commit()
        assert db.get(UploadRun, 985) is None
        assert db.scalar(select(func.count()).select_from(SignalAlertDecisionEvidence)) == count
        retry = alerts.evaluate_signal_change_events(db, (events[-1],))
        db.commit()
        assert retry.created == 0 and retry.suppressed == 1
        assert decisions[0].payload_json["effective_configuration_at_creation"] == retained_config


def test_native_alert_rule_bootstrap_and_direct_members_reject(contextual_engine):
    from app.models.tables import SignalAlertRuleEvidence
    from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
    from app.services.setup_lifecycle.decision_evidence import persist_alert_rule_evidence

    with Session(contextual_engine) as db:
        service = SetupLifecycleAlertService()
        rules = service.seed_builtin_rules(db)
        assert rules and len(rules) == len(service.config.alerts.rules)
        db.commit()
        rule_id = rules[0].rule_id
        persisted = db.scalar(select(SignalAlertRule).where(SignalAlertRule.rule_id == rule_id))
        assert persisted is not None
        persisted.severity = "RISK"
        db.commit()
        service.seed_builtin_rules(db)
        db.commit()
        assert persisted.severity == service.config.alerts.rules[rule_id].severity.value
        for action in (
            lambda: persist_alert_rule_evidence(db, persisted),
            lambda: SetupLifecycleRepository().upsert_alert_rule(
                db,
                rule_id=rule_id,
                severity="RISK",
                scope="forged",
                config_version="forged",
            ),
            lambda: SetupLifecycleRepository().add(db, SignalAlertRuleEvidence()),
        ):
            db.add(UploadRun(id=986, filename="rule-member-attack.csv", status="COMPLETED"))
            with pytest.raises(ValueError, match="SEMANTIC_TRANSACTION_REQUIRED"):
                action()
            db.commit()
            assert db.get(UploadRun, 986) is None
        disabled_config = replace(
            service.config,
            alerts=replace(
                service.config.alerts,
                built_in_rules_enabled=False,
            ),
        )
        assert SetupLifecycleAlertService(config=disabled_config).seed_builtin_rules(db) == ()
        db.commit()
        assert db.scalar(select(func.count()).select_from(SignalAlertRule)) == len(rules)


def test_alert_writer_retained_rule_confidence_cooldown_and_retry(contextual_engine):
    setup_config = resolve_setup_configuration()
    lifecycle_config = resolve_lifecycle_configuration()
    with Session(contextual_engine) as db:
        rule = SignalAlertRule(
            rule_id="T14C_NATIVE",
            enabled=True,
            severity="INFO",
            scope="lifecycle_transition",
            cooldown_sessions=2,
            minimum_confidence=0,
            config_version="t14c-v1",
            condition_json={"to_state": "EXPIRED"},
            market_restrictions_json={},
            metadata_json={},
        )
        db.add(rule)
        race_rule = SignalAlertRule(
            rule_id="T14C_NATIVE_SECOND",
            enabled=True,
            severity="INFO",
            scope="lifecycle_transition",
            cooldown_sessions=2,
            minimum_confidence=0,
            config_version="t14c-v1",
            condition_json={"to_state": "EXPIRED"},
            market_restrictions_json={},
            metadata_json={},
        )
        db.add(race_rule)
        db.flush()
        alert_config = resolve_alert_configuration(rules=(rule, race_rule))
        cutoff, _, _ = seed_native_core(
            db, extra_configurations=(setup_config, lifecycle_config, alert_config)
        )
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = (
            SetupLifecycleSnapshotBuilder(setup_config.setup_config()).build(context.tickers[0]).dto
        )
        snapshot = SetupLifecycleRepository(setup_config.setup_config()).upsert_snapshot(db, dto)
        from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService

        episode_service = SetupLifecycleEpisodeService(config=lifecycle_config.setup_config())
        result = episode_service.apply_snapshot(db, snapshot)
        from app.models.tables import SetupLifecycleEvent, SetupLifecycleTransitionEvidence
        from app.services.market_clock_service import MarketClockService
        from app.services.setup_lifecycle.alert_service import _lifecycle_semantic_key

        gap_cutoff = MarketClockService().cutoff_for(
            datetime(2026, 10, 30, 23, tzinfo=UTC), reason="T14C_NATIVE_ALERT_OPERATION"
        )
        expired = episode_service.apply_observation_gap(
            db,
            ticker=snapshot.ticker,
            timeframe=snapshot.timeframe,
            setup_family=result.decision.setup_family,
            observed_on=gap_cutoff.latest_completed_session,
            market_cutoff=gap_cutoff,
        )
        event = db.get(SetupLifecycleEvent, expired.lifecycle_event_id)
        transition = db.get(SetupLifecycleTransitionEvidence, event.transition_evidence_id)
        evaluation = db.get(SetupLifecycleEvaluationEvidence, transition.evaluation_evidence_id)
        db.commit()
        kwargs = dict(
            rule=rule,
            ticker=snapshot.ticker,
            timeframe=snapshot.timeframe,
            effective_session=event.effective_date,
            source_event_key=event.source_event_key,
            semantic_key=_lifecycle_semantic_key(rule, event),
            decision="GENERATED",
            reasons=("T14C_NATIVE_ALERT",),
            payload={
                "source": "lifecycle_event",
                "to_state": event.to_state,
                "to_phase": event.to_phase,
                "actionability_after": event.actionability_after,
                "market_regime": None,
                "setup_family": event.setup_family,
                "source_evidence": event.evidence_json,
                "semantic_key": _lifecycle_semantic_key(rule, event),
                "source_confidence": event.confidence_score,
                "evaluation_run_id": event.evaluation_run_id,
                "lifecycle_event_id": event.id,
                "signal_change_event_id": None,
                "episode_id": event.episode_id,
            },
            setup_evidence_id=snapshot.evidence_id,
            lifecycle_evaluation_evidence_id=evaluation.id,
            lifecycle_transition_evidence_id=transition.id,
            effective_configuration=alert_config,
        )
        from app.models.tables import SignalAlertEvent
        from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
        from app.services.setup_lifecycle.decision_evidence import persist_alert_rule_evidence

        alert_service = SetupLifecycleAlertService()
        alert_service._prepare_rules(db, (rule, race_rule))
        source_payload = kwargs["payload"]
        created = alert_service._persist_alert(
            db,
            rule=rule,
            ticker=event.ticker,
            timeframe=event.timeframe,
            effective_date=event.effective_date,
            source_event_key=event.source_event_key,
            evaluation_run_id=event.evaluation_run_id,
            source_confidence=event.confidence_score,
            semantic_key=kwargs["semantic_key"],
            reason_codes=kwargs["reasons"],
            lifecycle_event_id=event.id,
            episode_id=event.episode_id,
            evidence={
                key: value
                for key, value in source_payload.items()
                if key
                not in {
                    "source_confidence",
                    "evaluation_run_id",
                    "lifecycle_event_id",
                    "signal_change_event_id",
                    "episode_id",
                }
            },
        )
        db.commit()
        assert created.created == 1
        notification = db.get(SignalAlertEvent, created.event_ids[0])
        repository = SetupLifecycleRepository()
        assert repository.acknowledge_alert_event(db, notification.id).status == "ACKNOWLEDGED"
        db.commit()
        assert repository.dismiss_alert_event(db, notification.id).status == "DISMISSED"
        db.commit()
        notification.severity = "FORGED"
        db.add(UploadRun(id=990, filename="alert-status-staged.csv", status="COMPLETED"))
        with pytest.raises(ValueError, match="EVENT_PROJECTION_MISMATCH"):
            repository.acknowledge_alert_event(db, notification.id)
        db.commit()
        assert db.get(UploadRun, 990) is None
        with pytest.raises(ValueError, match="SUPPORTING_SEMANTIC_TRANSACTION_REQUIRED"):
            persist_alert_rule_evidence(db, rule)
        db.commit()
        generated = persist_alert_decision_evidence(db, **kwargs)
        db.commit()
        assert generated.payload_json["calculation_identity"]["configuration"]
        assert generated.calculation_cutoff_at == gap_cutoff.cutoff_at
        assert persist_alert_decision_evidence(db, **kwargs).id == generated.id
        db.commit()
        for attack, expected in (
            (
                {"payload": {**kwargs["payload"], "source_confidence": 999}},
                "SOURCE_EVENT_BINDING_MISMATCH",
            ),
            ({"source_event_key": "bypass-cooldown"}, "SOURCE_EVENT_BINDING_MISMATCH"),
            ({"effective_configuration": None}, "CONFIGURATION_REQUIRED"),
            ({"timeframe": "forged"}, "SOURCE_EVENT_BINDING_MISMATCH"),
            (
                {"payload": {**kwargs["payload"], "to_state": "READY"}},
                "SOURCE_EVENT_BINDING_MISMATCH",
            ),
            (
                {"payload": {**kwargs["payload"], "lifecycle_event_id": None}},
                "EXACT_SOURCE_EVENT_REQUIRED",
            ),
        ):
            with pytest.raises(ValueError, match=expected):
                persist_alert_decision_evidence(db, **{**kwargs, **attack})
            db.commit()
            assert db.scalar(select(func.count()).select_from(SignalAlertDecisionEvidence)) == 1

        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        barrier = Barrier(2)
        frozen_rule = SignalAlertRule(
            **next(r for r in alert_config.values["rules"] if r["rule_id"] == race_rule.rule_id),
            id=race_rule.id,
            metadata_json=dict(race_rule.metadata_json),
        )
        racing_kwargs = {
            **kwargs,
            "rule": frozen_rule,
            "semantic_key": _lifecycle_semantic_key(frozen_rule, event),
            "payload": {
                **kwargs["payload"],
                "semantic_key": _lifecycle_semantic_key(frozen_rule, event),
            },
            "reasons": ("T14C_NATIVE_SECOND_ALERT",),
        }

        def race():
            with Session(contextual_engine) as other:
                barrier.wait(timeout=15)
                row = persist_alert_decision_evidence(other, **racing_kwargs)
                other.commit()
                return row.id

        # The parent has only read state. Concurrent first-time decision writes
        # independently resolve the same retained native rule and evaluation.
        db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = [pool.submit(race) for _ in range(2)]
            assert first.result(timeout=30) == second.result(timeout=30)
        assert db.scalar(select(func.count()).select_from(SignalAlertDecisionEvidence)) == 2


def test_setup_direct_attacks_roll_back_all_staged_rows(contextual_engine):
    config = resolve_setup_configuration()
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(db, extra_configurations=(config,))
        context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        dto = SetupLifecycleSnapshotBuilder(config.setup_config()).build(context.tickers[0]).dto
        repo = SetupLifecycleRepository(config.setup_config())
        snapshot = repo.upsert_snapshot(db, dto)
        db.commit()
        original_id = snapshot.id
        original_evidence_id = snapshot.evidence_id
        evidence_count = db.scalar(select(func.count()).select_from(CoreCalculationEvidence))

        corrupt_price = deepcopy(dto.source_lineage)
        corrupt_price["pit_price_evidence"]["bars"][0]["fields"]["close"] = "999999.0"
        attacks = (
            ("missing-identity", replace(dto, source_lineage={})),
            ("missing-configuration", replace(dto, effective_configuration=None)),
            ("wrong-price", replace(dto, source_lineage=corrupt_price)),
        )
        for index, (name, attacked) in enumerate(attacks, start=1):
            db.add(UploadRun(id=990 + index, filename=name, status="COMPLETED"))
            with pytest.raises(ValueError):
                repo.upsert_snapshot(db, replace(attacked, source_data_hash=name))
            # A caller catching rejection and committing cannot retain a staged
            # business row or unrelated row flushed before the nested writer.
            db.commit()
            assert db.get(UploadRun, 990 + index) is None
            assert db.scalar(select(func.count()).select_from(SetupSignalSnapshot)) == 1
            assert (
                db.scalar(select(func.count()).select_from(CoreCalculationEvidence))
                == evidence_count
            )

        snapshot = db.get(SetupSignalSnapshot, original_id)
        with pytest.raises(ValueError, match="DOMAIN_MUTATION_CONTEXT_REQUIRED"):
            persist_setup_evidence(db, snapshot, mutation_context=object())
        db.commit()
        assert snapshot.evidence_id == original_evidence_id

        original_hash = snapshot.source_data_hash
        snapshot.source_data_hash = "tampered"
        from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService

        with pytest.raises(ValueError, match="MUTATION_SETUP_PROJECTION_PAYLOAD_MISMATCH"):
            SetupLifecycleEpisodeService().apply_snapshot(db, snapshot)
        db.commit()
        assert snapshot.source_data_hash == original_hash

        with pytest.raises(ValueError, match="MUTATION_SUPPORTING_SEMANTIC_TRANSACTION_REQUIRED"):
            SetupLifecycleEpisodeService._apply_snapshot_denormalization(snapshot, None, None)
        db.commit()


def test_diagnostic_reports_cannot_supply_their_own_authority(contextual_engine):
    from app.models.tables import WinnerOutcomeDefinition, WinnerPredictionSnapshot
    from app.services.winner_probability.calibration_service import CalibrationService
    from app.services.winner_probability.drift_service import DriftService
    from app.services.winner_probability.model_training import ShadowModelTrainingService
    from app.services.winner_probability.similarity_service import SimilarityService

    cutoff = datetime(2026, 9, 16, 21, tzinfo=UTC)
    calibration = CalibrationService()
    drift = DriftService()
    training = ShadowModelTrainingService()
    report = training.train_shadow_report(
        (), feature_names=("combined_score",), outcome_definition_id=999, training_cutoff_at=cutoff
    )
    writes = (
        lambda db: calibration.persist_bins(
            db,
            report=calibration.calculate(()),
            outcome_definition_id=999,
            estimate_kind="DECISION_TIME",
        ),
        lambda db: drift.persist_metrics(
            db,
            results=drift.calculate(baseline=(), recent=(), comparison_window="T14C"),
            outcome_definition_id=999,
            as_of_date=cutoff.date(),
        ),
        lambda db: training.persist_training_run(
            db, report=report, outcome_definition_id=999, training_cutoff_at=cutoff
        ),
        lambda db: SimilarityService().persist_neighbors(
            db,
            prediction=WinnerPredictionSnapshot(id=999),
            outcome_definition=WinnerOutcomeDefinition(id=999),
            neighbors=(),
            source_cutoff_at=cutoff,
        ),
    )
    with Session(contextual_engine) as db:
        from app.services.winner_probability.repository import WinnerProbabilityRepository

        for repository, row in (
            (SetupLifecycleRepository(), SetupSignalSnapshot()),
            (WinnerProbabilityRepository(), WinnerPredictionSnapshot()),
        ):
            with pytest.raises(
                ValueError, match="MUTATION_SUPPORTING_SEMANTIC_TRANSACTION_REQUIRED"
            ):
                repository.add(db, row)
            db.commit()
        for writer in writes:
            db.add(UploadRun(id=999, filename="must-roll-back.csv", status="COMPLETED"))
            with pytest.raises(ValueError, match="MUTATION_WINNER_DIAGNOSTIC_CONTEXT_REQUIRED"):
                writer(db)
            db.commit()
            assert db.get(UploadRun, 999) is None
