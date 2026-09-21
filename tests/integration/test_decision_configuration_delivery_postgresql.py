"""Native durable boundaries with real persisted compositional configuration."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    BackgroundJob,
    EffectiveConfigurationRecord,
    ExecutionConfigurationAnchor,
    ExecutionConfigurationBinding,
    PipelineRun,
    UploadRun,
)
from app.services.background_job_service import enqueue_job
from app.services.background_worker import execute_job
from app.services.configuration_delivery import (
    ANCHOR_KEY,
    binding_reference,
    configuration_delivery_scope,
    load_configuration_delivery,
    persist_configuration_anchor,
    resolve_pipeline_configurations,
)
from app.services.core_effective_configuration import resolve_fundamental_configuration
from app.services.decision_effective_configuration import resolve_setup_configuration
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.technical_indicators import load_pine_defaults
from app.services.winner_probability.config import load_winner_probability_config
from app.settings import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@pytest.fixture
def delivery_db(disposable_postgres_database):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="t13d.csv", status="COMPLETED"))
        db.add(PipelineRun(id=1, upload_run_id=1, status="PENDING"))
        db.commit()
        # Explicit fixture PKs do not advance PostgreSQL identity sequences.
        for table in ("upload_runs", "pipeline_runs"):
            db.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:table, 'id'), "
                    "(SELECT max(id) FROM " + table + "))"
                ),
                {"table": table},
            )
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def _handler(db, job):
    # Actual native loader/resolver delivery, not reading an anchor JSON in isolation.
    return {
        "setup": resolve_setup_configuration().snapshot.semantic_hash,
        "fundamental": resolve_fundamental_configuration().snapshot.semantic_hash,
        "winner": load_winner_probability_config().config_hash,
        "pine": load_pine_defaults(),
        "setup_native": load_setup_lifecycle_config().confidence.high_min,
        "flags": get_settings().winner_probability_capture_in_pipeline,
    }


def test_queued_job_retry_and_resume_keep_c1_after_current_drift(delivery_db, monkeypatch):
    db = delivery_db
    job = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1}, related_run_id=1)
    db.commit()
    first = execute_job(db, job, {"FULL_PIPELINE": _handler})
    anchor = deepcopy(job.payload_json[ANCHOR_KEY])
    current = load_setup_lifecycle_config()
    changed = replace(
        current, confidence=replace(current.confidence, high_min=current.confidence.high_min + 1)
    )
    c2 = resolve_setup_configuration(changed)
    assert c2.snapshot.semantic_hash != first["setup"]
    monkeypatch.setenv("WINNER_PROBABILITY_CAPTURE_IN_PIPELINE", "false")
    # If any native file is reopened by worker execution, fail instead of tolerating it.
    monkeypatch.setattr(
        "pathlib.Path.open",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("current C2 file read")),
    )
    second = execute_job(db, job, {"FULL_PIPELINE": _handler})
    assert second == first
    resumed = enqueue_job(
        db,
        "FULL_PIPELINE",
        {"pipeline_run_id": 1, "resume_from_step": "setup_lifecycle"},
        related_run_id=1,
        coalesce=False,
    )
    assert resumed.payload_json[ANCHOR_KEY] == anchor
    assert execute_job(db, resumed, {"FULL_PIPELINE": _handler}) == first


def test_child_inherits_parent_even_without_live_execution_context(delivery_db, monkeypatch):
    db = delivery_db
    parent = enqueue_job(db, "FULL_PIPELINE", {"run_id": 1}, coalesce=False)
    db.commit()
    monkeypatch.setattr(
        "app.services.configuration_delivery.resolve_pipeline_configurations",
        lambda *_a: (_ for _ in ()).throw(AssertionError("child resolves C2")),
    )
    child = enqueue_job(
        db, "WINNER_PREDICTION_CAPTURE", {"run_id": 1}, parent_job_id=parent.id, coalesce=False
    )
    assert child.payload_json[ANCHOR_KEY] == parent.payload_json[ANCHOR_KEY]
    assert execute_job(db, child, {child.job_type: _handler}) == execute_job(
        db, parent, {parent.job_type: _handler}
    )


def test_worker_rejects_valid_other_anchor_before_handler(delivery_db):
    db = delivery_db
    job = enqueue_job(db, "SETUP_LIFECYCLE_EVALUATE_RUN", {"run_id": 1}, coalesce=False)
    configurations = resolve_pipeline_configurations(db)
    current = load_setup_lifecycle_config()
    configurations["decision.setup"] = resolve_setup_configuration(
        replace(
            current,
            confidence=replace(current.confidence, high_min=current.confidence.high_min + 1),
        )
    )
    other = persist_configuration_anchor(db, configurations)
    job.payload_json = {**job.payload_json, ANCHOR_KEY: other}
    with pytest.raises(ValueError, match="PARENT_MISMATCH"):
        execute_job(db, job, {job.job_type: lambda *_a: pytest.fail("tampered calculation ran")})


def test_missing_anchor_and_legacy_job_fail_closed(delivery_db):
    db = delivery_db
    legacy = BackgroundJob(
        job_type="WINNER_PREDICTION_CAPTURE", status="QUEUED", payload_json={"run_id": 1}
    )
    db.add(legacy)
    db.flush()
    with pytest.raises(ValueError, match="MISSING_CONFIGURATION_ANCHOR_BINDING"):
        execute_job(
            db, legacy, {legacy.job_type: lambda *_a: pytest.fail("legacy resolved current")}
        )
    with pytest.raises(ValueError, match="FOR_RESUME"):
        enqueue_job(
            db,
            "FULL_PIPELINE",
            {"pipeline_run_id": 1, "resume_from_step": "setup_lifecycle"},
            coalesce=False,
        )
    assert db.get(ExecutionConfigurationBinding, "job:" + str(legacy.id)) is None


def test_frozen_records_and_anchors_are_immutable_in_postgresql(delivery_db):
    db = delivery_db
    job = enqueue_job(db, "SETUP_LIFECYCLE_EVALUATE_RUN", {"run_id": 1}, coalesce=False)
    db.commit()
    for table in (
        "effective_configuration_records",
        "execution_configuration_anchors",
        "execution_configuration_bindings",
    ):
        with pytest.raises(Exception, match="immutable"):
            with db.begin_nested():
                db.execute(text(f"DELETE FROM {table}"))
    assert binding_reference(db, job_id=job.id) == job.payload_json[ANCHOR_KEY]


def test_secret_and_operational_settings_are_absent_from_anchor(delivery_db, monkeypatch):
    db = delivery_db
    sentinel = "T13D-SYNTHETIC-SECRET-NEVER-RETAIN"
    monkeypatch.setenv("IB_FLEX_TOKEN", sentinel)
    reference = persist_configuration_anchor(db, resolve_pipeline_configurations(db))
    anchor = db.get(ExecutionConfigurationAnchor, reference["anchor_id"])
    assert sentinel not in str(anchor.payload_json)
    rows = db.scalars(select(EffectiveConfigurationRecord)).all()
    assert sentinel not in str([row.payload_json for row in rows])
    assert "job_worker_concurrency" not in str(anchor.payload_json)
    assert "smtp" not in str(anchor.payload_json)
    assert len(str(reference)) < 220


def test_stage_missing_from_bundle_never_resolves_current(delivery_db):
    db = delivery_db
    reference = persist_configuration_anchor(db, [resolve_setup_configuration()])
    delivery = load_configuration_delivery(db, reference)
    with configuration_delivery_scope(delivery):
        with pytest.raises(ValueError, match="MISSING_FROZEN_CONFIGURATION"):
            resolve_fundamental_configuration()


def test_stale_reclaim_changes_lease_but_preserves_configuration(delivery_db, monkeypatch):
    from app.services.background_job_service import claim_next_job, recover_stale_jobs
    from app.services.worker_registry import register_worker

    db = delivery_db
    job = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1}, coalesce=False)
    register_worker(db, worker_id="t13d", queues=["interactive"], heartbeat_timeout_seconds=30)
    db.commit()
    claimed = claim_next_job(db, "t13d")
    db.commit()
    assert claimed.id == job.id
    reference = deepcopy(claimed.payload_json[ANCHOR_KEY])
    token = claimed.execution_token
    first = execute_job(db, claimed, {claimed.job_type: _handler})
    claimed.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert recover_stale_jobs(db, 1) == 1
    db.commit()
    monkeypatch.setattr("pathlib.Path.open", lambda *_a, **_k: pytest.fail("reclaim reads C2"))
    reclaimed = claim_next_job(db, "t13d")
    assert reclaimed.execution_token != token
    assert reclaimed.payload_json[ANCHOR_KEY] == reference
    assert execute_job(db, reclaimed, {reclaimed.job_type: _handler}) == first


def test_readiness_policy_code_drift_fails_under_anchored_c1(delivery_db):
    from app.services.decision_effective_configuration import validate_delivered_readiness_policy
    from app.services.technical_consumer_eligibility import TECHNICAL_TO_SETUP

    db = delivery_db
    reference = persist_configuration_anchor(db, resolve_pipeline_configurations(db))
    with configuration_delivery_scope(load_configuration_delivery(db, reference)):
        validate_delivered_readiness_policy(TECHNICAL_TO_SETUP)
        with pytest.raises(ValueError, match="POLICY_CONFIGURATION_ANCHOR_MISMATCH"):
            validate_delivered_readiness_policy(
                replace(TECHNICAL_TO_SETUP, policy_version="next-policy")
            )


def test_native_setup_lifecycle_winner_artifacts_retain_own_c1(
    disposable_postgres_database,
    monkeypatch,
):
    from integration.test_t14c_decision_writer_postgresql import _native_liquidity_source
    from native_mutation_support import seed_native_core

    from app.models.tables import (
        CoreCalculationEvidence,
        SetupLifecycleEvaluationEvidence,
        WinnerPredictionSnapshot,
    )
    from app.services.decision_effective_configuration import (
        resolve_lifecycle_configuration,
        resolve_winner_configuration,
    )
    from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY
    from app.services.setup_lifecycle.canonicalization import SetupLifecycleCanonicalizer
    from app.services.setup_lifecycle.decision_evidence import persist_setup_evidence
    from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository
    from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
    from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
    from app.services.transition_preflight_plan_service import (
        freeze_transition_decision_handoff_manifest,
    )
    from app.services.winner_probability.capture_service import WinnerPredictionCaptureService

    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    monkeypatch.setenv("WINNER_PROBABILITY_ENABLED", "true")
    monkeypatch.setenv("WINNER_PROBABILITY_CAPTURE_IN_PIPELINE", "true")
    monkeypatch.setenv("SETUP_LIFECYCLE_ENABLED", "true")
    get_settings.cache_clear()
    winner_config = load_winner_probability_config()
    winner_config = replace(winner_config, engine=replace(winner_config.engine, enabled=True))
    extra = (resolve_setup_configuration(), resolve_lifecycle_configuration()) + tuple(
        resolve_winner_configuration(winner_config, family=family)
        for family in ("prediction", "outcome", "cohort", "generation")
    )
    with Session(engine) as db:
        bundle = resolve_pipeline_configurations(db)
        bundle.update({item.snapshot.family.namespace: item for item in extra})
        cutoff, _, _ = seed_native_core(
            db,
            extra_configurations=tuple(bundle.values()),
            cutoff_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
            raw_values={**_native_liquidity_source(), "upcoming_earnings_date": "2026-11-10"},
        )
        pipeline_id = db.scalar(select(PipelineRun.id).where(PipelineRun.upload_run_id == 7))
        job = enqueue_job(
            db, "FULL_PIPELINE", {"pipeline_run_id": pipeline_id, "run_id": 7}, coalesce=False
        )
        db.commit()

        def calculate(db, _job):
            repository = SetupLifecycleRepository()
            context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
            built = SetupLifecycleSnapshotBuilder().build(context.tickers[0])
            row = repository.upsert_snapshot(db, built.dto)
            assert persist_setup_evidence(db, row) is not None
            SetupLifecycleCanonicalizer().canonicalize_run(db, run_id=7, snapshot_ids=(row.id,))
            setup = SetupLifecycleEpisodeService().apply_snapshot(db, row)
            handoff = freeze_transition_decision_handoff_manifest(
                db, upload_run_id=7, market_cutoff=cutoff
            )
            db.flush()
            # Capture uses independent per-ticker Sessions, as normal stages do.
            db.commit()
            winner = WinnerPredictionCaptureService().capture_run(
                db,
                run_id=7,
                market_cutoff=cutoff,
                config=winner_config,
                decision_handoff_manifest_id=handoff.id,
                decision_at=datetime.now(UTC),
            )
            return setup, winner

        setup, winner = execute_job(db, job, {job.job_type: calculate})
        assert setup.lifecycle_evaluation_evidence is not None
        assert winner.failed == 0, "\n".join(
            failure["message"] for failure in winner.representative_failures
        )
        db.commit()
        evidence = db.scalars(
            select(CoreCalculationEvidence).where(CoreCalculationEvidence.artifact_kind == "SETUP")
        ).all()
        lifecycle = db.scalars(select(SetupLifecycleEvaluationEvidence)).all()
        predictions = db.scalars(select(WinnerPredictionSnapshot)).all()
        assert evidence and lifecycle and predictions
        from datetime import date

        from app.models.tables import SetupLifecycleEpisode, SetupSignalSnapshot
        from app.services.decision_effective_configuration import resolve_lifecycle_configuration
        from app.services.market_clock_service import MarketClockService
        from app.services.setup_lifecycle.enums import SetupFamily
        from app.services.setup_lifecycle.replay_service import (
            SetupLifecycleReplayRequest,
            SetupLifecycleReplayService,
        )

        episode = db.scalar(select(SetupLifecycleEpisode))
        assert episode is not None
        prior_payload = deepcopy(
            db.get(
                SetupLifecycleEvaluationEvidence, episode.latest_evaluation_evidence_id
            ).payload_json
        )
        native = load_setup_lifecycle_config()
        c2_native = replace(
            native,
            episodes=replace(
                native.episodes, history_window_sessions=native.episodes.history_window_sessions + 1
            ),
        )
        c2 = resolve_lifecycle_configuration(c2_native)
        repair_service = SetupLifecycleEpisodeService(config=c2_native)
        repair_service.apply_observation_gap(
            db,
            ticker=episode.ticker,
            timeframe=episode.timeframe,
            setup_family=SetupFamily(episode.setup_family),
            observed_on=date(2026, 8, 4),
            market_cutoff=MarketClockService().cutoff_for(
                datetime(2026, 8, 4, 22, tzinfo=UTC), reason="T14C_EXPLICIT_GAP_REPAIR"
            ),
        )
        repaired = db.get(SetupLifecycleEvaluationEvidence, episode.latest_evaluation_evidence_id)
        assert repaired.payload_json["execution_semantics"] == "CURRENT_STATE_REPAIR"
        assert (
            repaired.payload_json[CONFIGURATION_PAYLOAD_KEY]["semantic_hash"]
            == c2.snapshot.semantic_hash
        )
        assert (
            repaired.payload_json["calculation_identity_fingerprint"]
            != prior_payload["calculation_identity_fingerprint"]
        )
        snapshot = db.scalar(
            select(SetupSignalSnapshot).where(SetupSignalSnapshot.evidence_id == evidence[0].id)
        )
        SetupLifecycleCanonicalizer().canonicalize_run(db, run_id=7, snapshot_ids=(snapshot.id,))
        retrospective = SetupLifecycleReplayService(config=c2_native).replay(
            db, SetupLifecycleReplayRequest(ticker=snapshot.ticker, persist=True)
        )
        assert retrospective["snapshot_count"] >= 1
        replay_id = retrospective["proposed"][0]["retrospective_evidence_id"]
        replay = db.get(SetupLifecycleEvaluationEvidence, replay_id)
        assert replay.payload_json["execution_semantics"] == "CURRENT_RULES_RETROSPECTIVE"
        assert (
            replay.payload_json[CONFIGURATION_PAYLOAD_KEY]["semantic_hash"]
            == c2.snapshot.semantic_hash
        )
        assert lifecycle[0].payload_json == prior_payload
        db.commit()
        frozen = [deepcopy(row.payload_json) for row in evidence + lifecycle]
        winner_frozen = [deepcopy(row.lineage_json) for row in predictions]
        winner_c1 = winner_config
        winner_c2 = replace(
            winner_c1,
            horizon=replace(winner_c1.horizon, sessions=(*winner_c1.horizon.sessions, 99)),
        )
        recaptured = WinnerPredictionCaptureService().capture_run(
            db, run_id=7, config=winner_c2, market_cutoff=cutoff
        )
        assert recaptured.failed > 0
        assert any(
            "MUTATION_RETAINED_CONFIGURATION_MISMATCH" in failure["message"]
            for failure in recaptured.representative_failures
        ), "\n".join(failure["message"] for failure in recaptured.representative_failures)
        assert [row.lineage_json for row in predictions] == winner_frozen
        for row in evidence + lifecycle:
            assert row.payload_json[CONFIGURATION_PAYLOAD_KEY]["semantic_hash"]
        for row in predictions:
            assert row.lineage_json[CONFIGURATION_PAYLOAD_KEY]["semantic_hash"]
            assert row.lineage_json["outcome_effective_configuration"]["semantic_hash"]
            assert row.lineage_json["outcome_reference_policy"]["version"]
        monkeypatch.setattr(
            "pathlib.Path.open", lambda *_a, **_k: pytest.fail("history reads current")
        )
        db.expire_all()
        assert [row.payload_json for row in evidence + lifecycle] == frozen
        assert [row.lineage_json for row in predictions] == winner_frozen
    engine.dispose()


def test_0079_upgrade_downgrade_reupgrade_constraints(disposable_postgres_database):
    from sqlalchemy import inspect

    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "0079_setup_lifecycle_alert_ev")
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)
    schema = inspect(engine)
    assert len(schema.get_foreign_keys("execution_configuration_bindings")) == 4
    assert schema.get_check_constraints("execution_configuration_bindings")
    command.downgrade(config, "0079_setup_lifecycle_alert_ev")
    assert "effective_configuration_records" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    command.check(config)
    assert "effective_configuration_records" in inspect(engine).get_table_names()
    engine.dispose()


def test_alert_configuration_history_rule_drift_and_predecessor_are_immutable(delivery_db):
    from integration.test_t14c_decision_writer_postgresql import _native_liquidity_source
    from native_mutation_support import seed_native_core

    from app.models.tables import SignalAlertDecisionEvidence, SignalAlertRule
    from app.services.decision_effective_configuration import (
        configuration_from_payload,
        resolve_alert_configuration,
    )
    from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
    from app.services.setup_lifecycle.canonicalization import SetupLifecycleCanonicalizer
    from app.services.setup_lifecycle.change_detector import SetupLifecycleChangeDetector
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository
    from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
    from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader

    db = delivery_db
    setup = resolve_setup_configuration()
    rule = SignalAlertRule(
        rule_id="t13d-native-rule",
        enabled=True,
        severity="ACTIONABLE",
        scope="signal_change",
        cooldown_sessions=2,
        minimum_confidence=0,
        config_version="C1",
        condition_json={
            "signal_keys": [item.key for item in setup.setup_config().signal_registry.definitions()]
        },
        market_restrictions_json={},
        metadata_json={},
    )
    db.add(rule)
    db.flush()
    c1 = resolve_alert_configuration(rules=(rule,))
    sources = []
    for offset, (day, weak) in enumerate(((14, False), (15, True), (16, False), (17, True))):
        if offset == 2:
            rule.cooldown_sessions, rule.config_version = 99, "C2"
            db.commit()
        current = resolve_alert_configuration(rules=(rule,))
        alerts = SetupLifecycleAlertService()
        alerts._prepare_rules(db, (rule,))
        cutoff, _, _ = seed_native_core(
            db,
            run_id=7 + offset,
            cutoff_at=datetime(2026, 9, day, 21, tzinfo=UTC),
            raw_values=_native_liquidity_source(weak=weak),
            extra_configurations=(setup, current),
        )
        context = SetupLifecycleSourceLoader().load_run_context(
            db, 7 + offset, market_cutoff=cutoff
        )
        repository = SetupLifecycleRepository(setup.setup_config())
        snapshot = repository.upsert_snapshot(
            db, SetupLifecycleSnapshotBuilder(setup.setup_config()).build(context.tickers[0]).dto
        )
        SetupLifecycleCanonicalizer(
            repository=repository, config=setup.setup_config()
        ).canonicalize_run(db, run_id=7 + offset, snapshot_ids=(snapshot.id,))
        db.commit()
        changes = SetupLifecycleChangeDetector(
            repository=repository, config=setup.setup_config()
        ).detect_and_persist(db, evaluation_run_id=None, snapshot_ids=(snapshot.id,))
        db.commit()
        if weak:
            events = repository.get_signal_change_events_by_ids(db, changes.event_ids)
            source = (
                events[0]
                if not sources
                else next(event for event in events if event.signal_key == sources[0].signal_key)
            )
            sources.append(source)
            alerts.evaluate_signal_change_events(db, (source,))
            db.commit()
    first, second = db.scalars(
        select(SignalAlertDecisionEvidence).order_by(SignalAlertDecisionEvidence.id)
    ).all()
    assert first.decision == "GENERATED" and second.decision == "SUPPRESSED_COOLDOWN"
    assert second.cooldown_predecessor_evidence_id == first.id
    frozen = deepcopy(first.payload_json)
    restored = configuration_from_payload(frozen["effective_configuration_at_creation"])
    assert restored.snapshot.semantic_hash == c1.snapshot.semantic_hash
    assert restored.values["rules"][0]["cooldown_sessions"] == 2
    db.expire_all()
    assert first.payload_json == frozen
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE"):
        first.payload_json = second.payload_json
        db.flush()


def test_ceri_downstream_native_writes_keep_c1_rules_and_allow_ack(delivery_db, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "e2e"))
    from single_run_certification.fixtures import _seed_ceri_manual_evidence

    from app.models.ceri_tables import CeriAlertRule, CeriChangeEvent
    from app.models.tables import RawCompanyRow
    from app.services.ceri.alert_service import CeriAlertService
    from app.services.ceri.capture_service import CeriRunCaptureService
    from app.services.ceri.config import AlertRuleConfig, load_ceri_config
    from app.services.ceri.enums import CeriChangeType
    from app.services.ceri.feature_flags import CeriFeatureFlags
    from app.services.ceri.snapshot_service import CeriSnapshotService
    from app.services.contextual_effective_configuration import resolve_ceri_configuration
    from app.services.market_clock_service import MarketClockService

    db = delivery_db
    clock = MarketClockService()
    cutoff = clock.cutoff_for(datetime.now(UTC), reason="T14C_CERI_CONFIGURATION_SOURCE")
    _seed_ceri_manual_evidence(db, as_of_session=cutoff.latest_completed_session)
    db.commit()
    db.add(UploadRun(id=777, filename="ceri-config-native.csv", status="COMPLETED"))
    db.flush()
    db.add(RawCompanyRow(run_id=777, row_number=1, ticker="ALFA", raw_json={}))
    db.commit()
    cutoff = clock.cutoff_for(datetime.now(UTC), reason="T14C_CERI_RETAINED_CAPTURE")
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
    result = CeriRunCaptureService(snapshot_service=snapshot_service).capture_run(
        db,
        777,
        force=True,
        market_cutoff=cutoff,
        effective_configuration=frozen,
    )
    assert result.failed == 0 and result.change_events > 0
    db.commit()
    change = db.scalar(select(CeriChangeEvent).where(CeriChangeEvent.company_id == 1))
    assert change is not None and change.delta_json["native_change_proof"]
    change_type = CeriChangeType(change.change_type)
    config = load_ceri_config()
    config = replace(
        config,
        alerts=replace(
            config.alerts,
            rules={
                **config.alerts.rules,
                change_type: AlertRuleConfig(change_type, True, "RISK", change_type, 2),
            },
        ),
    )
    monkeypatch.setattr(
        "app.services.ceri.alert_service.ceri_flags",
        lambda: CeriFeatureFlags(True, True, True, True, True, True, True),
    )
    service = CeriAlertService(config=config, alerts_enabled=True)
    db.add(
        CeriAlertRule(
            rule_id=change.change_type,
            enabled=True,
            severity="RISK",
            cooldown_sessions=2,
            config_version=config.engine.config_version,
            source_event_types_json=[change.change_type],
            thresholds_json={},
            scope_json={},
        )
    )
    db.flush()
    service._ensure_rule_configuration(db)
    rule = db.scalar(select(CeriAlertRule).where(CeriAlertRule.rule_id == change.change_type))
    rule.cooldown_sessions, rule.severity = 99, "NOTABLE"
    db.commit()
    alert = service.persist_alert_for_change(db, change=change, ticker="ALFA")
    assert alert is not None and alert.severity == "RISK"
    assert alert.evidence_json["effective_configuration_at_creation"]["semantic_hash"]
    assert change.delta_json["effective_configuration_at_creation"]["semantic_hash"]
    frozen_alert = deepcopy(alert.evidence_json)
    service.acknowledge(db, alert)
    db.commit()
    assert alert.evidence_json == frozen_alert
    with pytest.raises(ValueError, match="IMMUTABLE_CONFIGURATION_PROOF"):
        alert.evidence_json = {}
        db.flush()


def test_pipeline_direct_execution_rejects_flag_override_before_math(delivery_db):
    from app.services.pipeline_executor import PipelineExecutionDependencies, execute_full_pipeline

    db = delivery_db
    job = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1}, coalesce=False)
    flags = (
        load_configuration_delivery(db, job.payload_json[ANCHOR_KEY])
        .configurations["execution.settings"]
        .values
    )
    dependencies = PipelineExecutionDependencies(
        setup_lifecycle_pipeline_step_enabled=not flags["setup_lifecycle_pipeline_step_enabled"]
    )
    with pytest.raises(ValueError, match="FLAG_ANCHOR_MISMATCH"):
        execute_full_pipeline(db, 1, dependencies=dependencies)


def test_config_code_policy_drift_fails_before_execution_but_history_decodes(
    delivery_db, monkeypatch
):
    from app.services.decision_effective_configuration import configuration_from_payload

    db = delivery_db
    job = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1}, coalesce=False)
    original = resolve_setup_configuration()
    monkeypatch.setattr(
        "app.services.decision_effective_configuration.current_code_policy",
        lambda namespace: (
            {"fresh_bar_grace_sessions": 999} if namespace == "decision.setup" else None
        ),
    )
    assert (
        configuration_from_payload(original.snapshot.as_dict()).snapshot.as_dict()
        == original.snapshot.as_dict()
    )
    with pytest.raises(ValueError, match="CODE_POLICY_MISMATCH"):
        execute_job(db, job, {job.job_type: lambda *_a: pytest.fail("unsupported code ran")})


def test_public_resume_pipeline_preserves_c1_and_temporal_context(delivery_db, monkeypatch):
    from app.models.tables import PipelineStep
    from app.services.market_calculation_context_service import create_pipeline_market_context
    from app.services.pipeline_service import resume_pipeline
    from app.services.scope_refresh_adoption import admit_frozen_operation, bind_semantic_authority
    from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

    db = delivery_db
    pipeline = db.get(PipelineRun, 1)
    authority = admit_frozen_operation(
        db,
        operation_kind="configuration-delivery-resume-fixture",
        subject_kind="ticker",
        members=(ScopeMember("TICKER", "MSFT"),),
        cycle_key="configuration-delivery-resume:1",
        business_cutoff=datetime(2026, 9, 15, 22, tzinfo=UTC),
        provider_source_class="UPLOAD",
        request_type="FULL_PIPELINE",
        requirements=(AcquisitionRequirement("CONFIGURATION_DELIVERY"),),
        policy_identity="configuration-delivery-resume-v1",
        scope_definition={"pipeline_run_id": pipeline.id},
    )
    bind_semantic_authority(pipeline, authority)
    temporal = create_pipeline_market_context(
        db, pipeline, cutoff_at=datetime(2026, 9, 15, 22, tzinfo=UTC)
    )
    root = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1}, coalesce=False)
    bind_semantic_authority(root, authority)
    db.add(
        PipelineStep(pipeline_run_id=1, step_name="SETUP_LIFECYCLE", step_order=1, status="FAILED")
    )
    root.status = "COMPLETED"
    pipeline.status = "FAILED"
    db.commit()
    anchor = deepcopy(root.payload_json[ANCHOR_KEY])
    monkeypatch.setattr(
        "app.services.configuration_delivery.resolve_pipeline_configurations",
        lambda *_a: (_ for _ in ()).throw(AssertionError("resume resolves current C2")),
    )
    resumed = resume_pipeline(db, 1, resume_from_step="SETUP_LIFECYCLE")
    job = db.get(BackgroundJob, resumed.result_json["background_job_id"])
    assert job.payload_json[ANCHOR_KEY] == anchor
    assert job.payload_json["market_calculation_context_id"] == temporal.context_id
    assert (
        execute_job(db, job, {"FULL_PIPELINE": _handler})["setup"]
        == load_configuration_delivery(db, anchor)
        .configurations["decision.setup"]
        .snapshot.semantic_hash
    )


def test_configuration_delivery_bulk_load_has_bounded_queries_and_batch_no_reads(
    delivery_db, monkeypatch
):
    from sqlalchemy import event

    db = delivery_db
    reference = persist_configuration_anchor(db, resolve_pipeline_configurations(db))
    db.commit()
    db.expire_all()
    queries = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", record)
    try:
        delivery = load_configuration_delivery(db, reference)
        assert len(delivery.configurations) > 30
        assert len(queries) == 2  # One anchor + one bulk configuration SELECT.
        queries.clear()
        monkeypatch.setattr(
            "pathlib.Path.open",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("batch file read")),
        )
        with configuration_delivery_scope(delivery):
            for _ in range(100):
                _handler(db, None)
        assert queries == []
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", record)


def test_pipeline_fingerprint_tracks_only_business_switches(delivery_db, monkeypatch):
    db = delivery_db
    baseline = persist_configuration_anchor(db, resolve_pipeline_configurations(db))
    monkeypatch.setenv("JOB_POLL_INTERVAL_SECONDS", "7")
    monkeypatch.setenv("IB_FLEX_TOKEN", "t13d-synthetic-test-secret")
    get_settings.cache_clear()
    operational = persist_configuration_anchor(db, resolve_pipeline_configurations(db))
    assert operational == baseline
    original = get_settings().winner_probability_capture_in_pipeline
    monkeypatch.setenv("WINNER_PROBABILITY_CAPTURE_IN_PIPELINE", str(not original).lower())
    get_settings.cache_clear()
    behavioral = persist_configuration_anchor(db, resolve_pipeline_configurations(db))
    assert behavioral["fingerprint"] != baseline["fingerprint"]
    get_settings.cache_clear()


def test_c1_builtin_rule_seed_preserves_existing_c2_database_authority(delivery_db):
    from app.models.tables import SignalAlertRule
    from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService

    db = delivery_db
    root = enqueue_job(db, "FULL_PIPELINE", {"pipeline_run_id": 1}, coalesce=False)
    service = SetupLifecycleAlertService()
    service.seed_builtin_rules(db)
    row = db.scalar(select(SignalAlertRule).where(SignalAlertRule.rule_id == "NEW_READY"))
    row.severity = "CRITICAL"
    row.cooldown_sessions = 99
    db.commit()
    reference = root.payload_json[ANCHOR_KEY]
    with configuration_delivery_scope(load_configuration_delivery(db, reference)):
        anchored = SetupLifecycleAlertService()
        seeded = anchored.seed_builtin_rules(db)
        matched = next(rule for rule in seeded if rule.rule_id == "NEW_READY")
        assert matched.cooldown_sessions != 99
        assert (
            anchored.effective_configuration.values["rule_sources"]["NEW_READY"]["kind"]
            == "PROFILE"
        )
    db.expire(row)
    assert row.severity == "CRITICAL"
    assert row.cooldown_sessions == 99


def test_valid_anchor_with_unknown_decision_authority_fails_before_math(delivery_db):
    from app.services.core_effective_configuration import _decode
    from app.services.decision_effective_configuration import DecisionEffectiveConfiguration
    from app.services.effective_configuration import (
        ConfigurationEntry,
        ConfigurationSource,
        EffectiveConfigurationSnapshot,
    )

    original = resolve_setup_configuration()
    entries = list(original.snapshot.entries)
    first = entries[0]
    entries[0] = ConfigurationEntry(
        first.key,
        _decode(json.loads(first.canonical_value_json)),
        first.classification,
        first.value_type,
        ConfigurationSource(),
        defaulted=first.defaulted,
        overridden=first.overridden,
    )
    unknown = DecisionEffectiveConfiguration(
        EffectiveConfigurationSnapshot(original.snapshot.family, tuple(entries))
    )
    reference = persist_configuration_anchor(delivery_db, [unknown])
    with pytest.raises(ValueError, match="DECISION_CONFIGURATION_UNKNOWN_AUTHORITY"):
        load_configuration_delivery(delivery_db, reference)
