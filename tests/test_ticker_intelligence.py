from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import ForeignKeyConstraint, MetaData, event, text
from sqlalchemy.exc import SQLAlchemyError
from test_ticker_search import CUTOFF, frozen_readiness, insert, raw
from test_ticker_search import search_db as search_db
from test_ticker_workspace import workspace_decision

from app.db import Base, get_db
from app.main import create_app
from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriAlertRule,
    CeriChangeEvent,
    CeriCompany,
    CeriEvidenceDisposition,
    CeriScoreSnapshot,
)
from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    PipelineRun,
    RankingResult,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupLifecycleTransitionEvidence,
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
    SignalAlertDecisionEvidence,
    SignalAlertEvent,
    SignalAlertRule,
    SignalAlertRuleEvidence,
    SignalChangeEvent,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
)
from app.services import ticker_intelligence_service as service
from app.settings import Settings


def frozen(db, evidence_id, kind, run_id=7, payload=None, profile=None):
    anchor = db.get(CoreCalculationEvidence, run_id)
    payload = dict(payload or {})
    fingerprint = f"intelligence-{evidence_id}"
    if kind == "RANKING":
        payload["producer_readiness"] = frozen_readiness(kind, payload, fingerprint)
    insert(
        db,
        CoreCalculationEvidence,
        id=evidence_id,
        artifact_kind=kind,
        run_id=run_id,
        ticker="EOG",
        ranking_profile=profile,
        calculation_identity_json=anchor.calculation_identity_json,
        calculation_identity_fingerprint=fingerprint,
        payload_json=payload,
        payload_fingerprint=fingerprint,
        evidence_key=fingerprint,
        calculated_at=CUTOFF,
    )


def seed_intelligence(db):
    if db.get(CeriScoreSnapshot, 9000):
        return
    workspace_decision(db, 6, cutoff=CUTOFF - timedelta(days=3))
    workspace_decision(db, 7)
    insert(db, CeriCompany, id=8000, ticker="EOG", company_name="EOG Resources", exchange="US")
    for sid, run, score, days in ((8999, 6, 5.0, -3), (9000, 7, 8.2, 2), (9001, 7, 9.9, 3)):
        frozen(db, sid + 10000, "CERI", run)
        insert(
            db,
            CeriScoreSnapshot,
            id=sid,
            run_id=run,
            company_id=8000,
            ticker="EOG",
            evidence_id=sid + 10000,
            as_of_session=(CUTOFF + timedelta(days=days)).date(),
            cutoff_at=CUTOFF + timedelta(days=days),
            opportunity_score=score,
            event_risk_score=0,
            data_confidence="High",
            coverage_pct=95,
            posture="Supportive",
            earnings_proximity_risk=0,
            opportunity_ledger_json={"components": [{"name": "Revision", "score": 8.2}]},
            top_positive_contributors_json=[{"name": "Revisions", "value": 8.2}],
            warnings_json=["provider_data_stale"],
            config_version="frozen-ceri-v1",
            config_hash=f"ceri-{sid}",
            calculation_version="ceri-1",
            evidence_contract_version="v1",
            comparison_state="COMPARABLE",
            evidence_hash=f"score-{sid}",
        )
    insert(
        db,
        CeriEvidenceDisposition,
        ceri_snapshot_id=9001,
        disposition="EXCLUDED",
        reason_code="QUARANTINED",
        incident_reference="fixture",
        actor_source="test",
        event_fingerprint="excluded-9001",
    )
    insert(
        db,
        CeriChangeEvent,
        id=9100,
        company_id=8000,
        from_snapshot_id=8999,
        to_snapshot_id=9000,
        change_type="OPPORTUNITY_UPGRADED",
        severity="NOTABLE",
        importance="NOTABLE",
        signal_class="POSITIVE",
        comparison_state="COMPARABLE",
        dedup_key="change-9100",
        created_at=CUTOFF + timedelta(days=2),
        delta_json={},
    )
    insert(
        db, CeriAlertRule, id=9200, rule_id="test-alert", severity="NOTABLE", config_version="v1"
    )
    insert(
        db,
        CeriAlertEvent,
        id=9300,
        alert_rule_id=9200,
        source_change_event_id=9100,
        event_key="ceri-alert-9300",
        ticker="EOG",
        severity="NOTABLE",
        importance="NOTABLE",
        signal_class="POSITIVE",
        validity_classification="VALID_CURRENT",
        status="ACTIVE",
        created_at=CUTOFF + timedelta(days=2),
    )
    for run in (6, 7):
        for index, name in enumerate(("momentum", "quality", "custom_persisted")):
            eid = run * 1000 + index
            payload = dict(
                ranking_profile=name,
                ranking_label=name.title(),
                profile_rank=run + index,
                profile_score=str(run + 0.25),
                technical_profile_score="8.2",
                fundamental_score="9.11",
                base_technical_score="6.83",
                is_complete=True,
                has_fundamental=True,
                has_technical=True,
                decision_label="Research candidate",
                warning_flags_json=["market_risk_off"],
                penalties_json={"risk": 0},
                gates_json={"ready": True},
                effective_configuration_at_creation={
                    "identity": {"fingerprint": f"frozen-{run}"},
                    "semantic_hash": "historical-profile",
                    "semantic": {"values": {"ignored": "large"}},
                },
            )
            frozen(db, eid, "RANKING", run, payload=payload, profile=name)
            insert(
                db,
                RankingResult,
                run_id=run,
                ticker="EOG",
                ranking_profile=name,
                ranking_label=name.title(),
                profile_rank=999,
                profile_score=999,
                technical_profile_score=999,
                decision_label="Mutable mismatch",
                evidence_id=eid,
            )
            insert(
                db,
                CoreCalculationCurrentProjection,
                artifact_kind="RANKING",
                run_id=run,
                ticker="EOG",
                ranking_profile_key=name,
                evidence_id=eid,
            )
    insert(
        db,
        SetupLifecycleEvaluationRun,
        id=8400,
        mode="LIVE",
        status="COMPLETED",
        source_run_id=7,
        engine_version="slse-1",
        config_version="v1",
        config_hash="historical-slse",
    )
    frozen(db, 8500, "SETUP")
    insert(
        db,
        SetupSignalSnapshot,
        id=8600,
        ticker="EOG",
        timeframe="1d",
        data_as_of_date=(CUTOFF + timedelta(days=2)).date(),
        calculated_at=CUTOFF + timedelta(days=2),
        origin_type="RUN_CAPTURE",
        run_id=7,
        engine_version="slse-1",
        config_version="v1",
        config_hash="historical-slse",
        source_data_hash="snapshot-8600",
        schema_version="v1",
        is_canonical=True,
        data_quality_label="HIGH",
        setup_score=8.1,
        distance_to_pivot_pct=0,
        evidence_id=8500,
        technical_classification="Filtered momentum",
        warning_flags_json=["BLOCKED_BY_MARKET"],
    )
    insert(
        db,
        SetupSignalSnapshotCurrentSelection,
        ticker="EOG",
        timeframe="1d",
        data_as_of_date=(CUTOFF + timedelta(days=2)).date(),
        selected_snapshot_id=8600,
        selected_evaluation_run_id=8400,
        selection_reason="published",
    )
    for index, family in enumerate(("BREAKOUT", "PULLBACK")):
        insert(
            db,
            SetupLifecycleEvaluationEvidence,
            id=8700 + index,
            setup_evidence_id=8500,
            evaluation_run_id=8400,
            ticker="EOG",
            timeframe="1d",
            setup_family=family,
            decision_session=CUTOFF.date(),
            calculation_identity_fingerprint="test",
            execution_mode="LIVE",
            output_state="READY",
            output_phase="READY",
            transition_eligible=True,
            engine_version="slse-1",
            config_version="v1",
            config_hash="historical-slse",
            payload_json={},
            payload_fingerprint=f"e-{index}",
            evidence_key=f"e-{index}",
        )
        insert(
            db,
            SetupLifecycleTransitionEvidence,
            id=8800 + index,
            evaluation_evidence_id=8700 + index,
            setup_evidence_id=8500,
            ticker="EOG",
            timeframe="1d",
            setup_family=family,
            effective_session=CUTOFF.date(),
            event_type="STATE_TRANSITION",
            to_state="READY",
            to_phase="READY",
            config_hash="historical-slse",
            payload_json={},
            payload_fingerprint=f"t-{index}",
            evidence_key=f"t-{index}",
        )
        insert(
            db,
            SetupLifecycleEpisode,
            id=8900 + index,
            ticker="EOG",
            timeframe="1d",
            setup_family=family,
            status="ACTIVE",
            opened_on=CUTOFF.date() - timedelta(days=5),
            current_as_of_date=CUTOFF.date() + timedelta(days=2),
            last_observed_on=CUTOFF.date() + timedelta(days=2),
            current_state="READY",
            current_phase="READY",
            state_entered_on=CUTOFF.date(),
            current_actionability="BLOCKED",
            confidence_score=85,
            confidence_label="HIGH",
            current_snapshot_id=8600,
            opening_evaluation_id=8400,
            latest_evaluation_evidence_id=8700 + index,
            is_primary=index == 0,
            primary_rank=index + 1,
            engine_version="slse-1",
            config_version="v1",
            config_hash="historical-slse",
            metadata_json={"blockers": ["MARKET_RISK_OFF"]},
        )
        insert(
            db,
            SetupLifecycleEvent,
            id=9000 + index,
            episode_id=8900 + index,
            evaluation_run_id=8400,
            snapshot_id=8600,
            transition_evidence_id=8800 + index,
            ticker="EOG",
            timeframe="1d",
            setup_family=family,
            effective_date=CUTOFF.date() + timedelta(days=index),
            event_type="STATE_TRANSITION",
            from_state="DEVELOPING",
            to_state="READY",
            to_phase="READY",
            actionability_after="BLOCKED",
            confidence_score=85,
            confidence_label="HIGH",
            severity="NOTABLE",
            source_event_key=f"slse-{index}",
            engine_version="slse-1",
            config_version="v1",
            config_hash="historical-slse",
        )
    insert(
        db,
        SignalChangeEvent,
        id=9100,
        ticker="EOG",
        timeframe="1d",
        effective_date=CUTOFF.date(),
        episode_id=8900,
        current_snapshot_id=8600,
        evaluation_run_id=8400,
        category="TECHNICAL",
        signal_key="technical_score",
        value_type="NUMERIC",
        direction="UP",
        severity="NOTABLE",
        signal_definition_version="v1",
        source_event_key="signal-change",
        config_hash="historical-slse",
    )
    insert(
        db,
        SignalAlertRule,
        id=9200,
        rule_id="ready",
        severity="NOTABLE",
        scope="TICKER",
        config_version="v1",
    )
    insert(
        db,
        SignalAlertRuleEvidence,
        id=9200,
        rule_id="ready",
        config_version="v1",
        payload_json={"rule_id": "ready"},
        payload_fingerprint="rule",
        evidence_key="rule",
    )
    insert(
        db,
        SignalAlertDecisionEvidence,
        id=9300,
        rule_evidence_id=9200,
        ticker="EOG",
        timeframe="1d",
        effective_session=CUTOFF.date(),
        source_event_key="alert-src",
        semantic_key="alert-semantic",
        decision="GENERATED",
        payload_json={"decision_payload": {"blockers": ["MARKET_RISK_OFF"]}},
        payload_fingerprint="alert",
        evidence_key="alert",
    )
    insert(
        db,
        SignalAlertEvent,
        id=9400,
        alert_rule_id=9200,
        lifecycle_event_id=9000,
        signal_change_event_id=9100,
        evaluation_run_id=8400,
        decision_evidence_id=9300,
        ticker="EOG",
        timeframe="1d",
        effective_date=CUTOFF.date() + timedelta(days=2),
        event_key="slse-alert",
        source_event_key="alert-src",
        severity="NOTABLE",
        status="UNREAD",
    )
    insert(
        db,
        WinnerOutcomeDefinition,
        id=8000,
        definition_id="FROZEN_H5",
        label="Frozen outcome",
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        calculation_version="owpe-v1",
        config_hash="old-outcome",
        is_primary=True,
        is_active=False,
    )
    for run in (6, 7):
        pid = 80000 + run
        insert(
            db,
            WinnerPredictionSnapshot,
            id=pid,
            run_id=run,
            ticker="EOG",
            prediction_as_of_date=CUTOFF.date(),
            source_data_cutoff_at=CUTOFF,
            entry_schedule_status="RESOLVED",
            entry_data_status="NOT_DUE",
            eligibility_status="ELIGIBLE",
            feature_schema_version="v1",
            feature_vector_hash=f"f-{run}",
            config_hash="historical-winner",
            calculation_version="owpe-v1",
            feature_json={},
            setup_family="BREAKOUT",
            source_ids_json={"combined_evidence_id": run},
        )
        for offset, status, probability in (
            (0, "PUBLISHED", 0.64 if run == 7 else 0.41),
            (10, "CANDIDATE", 0.99),
            (20, "SUPERSEDED", 0.98),
        ):
            insert(
                db,
                WinnerProbabilityEstimate,
                id=81000 + run + offset,
                prediction_id=pid,
                outcome_definition_id=8000,
                estimate_kind="DECISION_TIME",
                lifecycle_status=status,
                source="COHORT",
                source_version=f"frozen-{run}-{offset}",
                training_cutoff_at=CUTOFF - timedelta(days=2),
                point_probability=probability,
                lower_bound=0.55 if run == 7 else 0.3,
                upper_bound=0.73 if run == 7 else 0.5,
                interval_width=0.18,
                sample_n=125,
                effective_n=118,
                evidence_grade="High",
                config_hash=f"estimate-{run}-{offset}",
                feature_schema_version="v1",
                published_at=CUTOFF if status != "CANDIDATE" else None,
                superseded_at=CUTOFF + timedelta(days=1) if status == "SUPERSEDED" else None,
            )


@pytest.fixture
def intelligence_db(search_db):
    # Reuse D1's small disposable-table pattern; drop only foreign-key constraints
    # so read fixtures can target the publication predicates without all writers.
    metadata = MetaData()
    for original in Base.metadata.tables.values():
        table = original.to_metadata(metadata)
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        table.foreign_keys.clear()
    metadata.create_all(search_db.get_bind())
    seed_intelligence(search_db)
    return search_db


@pytest.fixture
def intelligence_client(intelligence_db):
    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            db_monitor_enabled=False,
            observability_metrics_enabled=False,
            ceri_enabled=True,
            ceri_ui_enabled=True,
        )
    )
    app.dependency_overrides[get_db] = lambda: intelligence_db
    return TestClient(app)


def request(client, section, run=7, detail=True):
    return client.get(
        f"/api/tickers/EOG/intelligence/{section}",
        params={
            "identity": "ceri:8000",
            "run_id": run,
            "evidence_id": run,
            "detail": str(detail).lower(),
        },
    )


def test_ceri_eligible_current_cutoff_scores_changes_alerts_and_freshness(intelligence_client):
    result = request(intelligence_client, "ceri").json()
    assert result["state"] == "available" and "Opportunity 8.20" in result["summary"]
    assert "Event risk 0.00" in result["summary"] and "9.90" not in result["summary"]
    assert "2026-10-21" in result["as_of"]
    for value in (
        "OPPORTUNITY",
        "Alert #9300",
        "Change #9100",
        "Source freshness",
        "provider_data_stale",
    ):
        assert value.lower() in result["html"].lower()
    assert request(intelligence_client, "ceri", run=6).json()["as_of"] == result["as_of"]


def test_ceri_excluded_only_empty_and_disabled(intelligence_db, intelligence_client):
    intelligence_db.execute(
        CeriEvidenceDisposition.__table__.insert().values(
            ceri_snapshot_id=9000,
            disposition="EXCLUDED",
            reason_code="incident",
            incident_reference="test",
            actor_source="test",
            event_fingerprint="exclude-9000",
        )
    )
    intelligence_db.execute(
        CeriEvidenceDisposition.__table__.insert().values(
            ceri_snapshot_id=8999,
            disposition="EXCLUDED",
            reason_code="incident",
            incident_reference="test",
            actor_source="test",
            event_fingerprint="exclude-8999",
        )
    )
    assert request(intelligence_client, "ceri").json()["state"] == "excluded"
    intelligence_client.app.state.settings.ceri_ui_enabled = False
    disabled = request(intelligence_client, "ceri").json()
    assert disabled["state"] == "disabled" and "/ceri/ticker/" not in disabled["html"]


def test_ceri_quarantined_owner_and_uncertified_snapshots(intelligence_db, intelligence_client):
    intelligence_db.execute(
        PipelineRun.__table__.update()
        .where(PipelineRun.id == 7)
        .values(execution_authority_state="QUARANTINED")
    )
    result = request(intelligence_client, "ceri").json()
    assert "Opportunity 5.00" in result["summary"] and "8.20" not in result["summary"]
    intelligence_db.execute(CeriScoreSnapshot.__table__.update().values(evidence_id=None))
    assert request(intelligence_client, "ceri").json()["state"] == "excluded"


def test_published_primary_secondary_daily_lifecycle_timeline_and_blockers(intelligence_client):
    result = request(intelligence_client, "lifecycle").json()
    assert result["state"] == "available"
    for value in (
        "Primary episode",
        "Secondary episode",
        "BREAKOUT",
        "PULLBACK",
        "MARKET_RISK_OFF",
        "signal",
        "alert",
        "Distance to pivot",
        "0.00",
    ):
        assert value in result["html"]
    assert request(intelligence_client, "lifecycle", run=6).json()["as_of"] == result["as_of"]


def test_unpublished_episode_and_superseded_transition_never_current(
    intelligence_db, intelligence_client
):
    intelligence_db.execute(
        SetupLifecycleEvaluationRun.__table__.update()
        .where(SetupLifecycleEvaluationRun.id == 8400)
        .values(status="RUNNING")
    )
    result = request(intelligence_client, "lifecycle").json()
    assert result["state"] == "empty" and "Primary episode" not in result["html"]
    assert "STATE_TRANSITION" not in result["html"]
    intelligence_db.execute(
        SetupLifecycleEvaluationRun.__table__.update()
        .where(SetupLifecycleEvaluationRun.id == 8400)
        .values(status="COMPLETED")
    )
    intelligence_db.execute(SetupLifecycleEvent.__table__.update().values(is_current_version=False))
    result = request(intelligence_client, "lifecycle").json()
    assert "STATE_TRANSITION" not in result["html"]


def test_rankings_all_persisted_profiles_frozen_config_and_run_consistency(intelligence_client):
    result = request(intelligence_client, "rankings").json()
    assert result["state"] == "available" and "3 profiles" in result["summary"]
    assert "Best profile rank #7" in result["summary"]
    assert "7.25" in result["html"] and "Custom_Persisted" in result["html"]
    assert "frozen-7" in result["html"] and "999" not in result["html"]
    historical = request(intelligence_client, "rankings", run=6).json()
    assert "6.25" in historical["html"] and "frozen-6" in historical["html"]
    assert "7.25" not in historical["html"]


def test_unpublished_and_incompatible_rankings_are_unavailable(
    intelligence_db, intelligence_client
):
    evidence = intelligence_db.get(CoreCalculationEvidence, 7000)
    identity = deepcopy(evidence.calculation_identity_json)
    identity["ownership"]["run_id"]["value"] = 6
    intelligence_db.execute(
        CoreCalculationEvidence.__table__.update()
        .where(CoreCalculationEvidence.id == 7000)
        .values(calculation_identity_json=identity)
    )
    intelligence_db.expire_all()
    assert "2 profiles available" in request(intelligence_client, "rankings").json()["summary"]
    intelligence_db.execute(
        CoreCalculationEvidence.__table__.update()
        .where(CoreCalculationEvidence.id == 7001)
        .values(ranking_profile="wrong-profile")
    )
    intelligence_db.expire_all()
    assert "1 profiles available" in request(intelligence_client, "rankings").json()["summary"]
    intelligence_db.execute(
        CoreCalculationCurrentProjection.__table__.delete().where(
            CoreCalculationCurrentProjection.artifact_kind == "RANKING"
        )
    )
    result = request(intelligence_client, "rankings").json()
    assert result["state"] == "empty" and "7.25" not in result["html"]
    assert "Not calculated or published for this run" in result["html"]


def test_winner_published_selected_run_probability_interval_and_frozen_definition(
    intelligence_client,
):
    result = request(intelligence_client, "winner").json()
    assert result["state"] == "available" and "64%" in result["summary"]
    for value in (
        "64.0%",
        "55.0%",
        "73.0%",
        "125",
        "118",
        "Frozen outcome",
        "5 sessions",
        "PUBLISHED",
        "frozen-7-0",
    ):
        assert value in result["html"]
    assert "99.0%" not in result["html"] and "98.0%" not in result["html"]
    older = request(intelligence_client, "winner", run=6).json()
    assert "41%" in older["summary"] and "64.0%" not in older["html"]


def test_winner_insufficient_and_no_cross_run_or_superseded_prediction(
    intelligence_db, intelligence_client
):
    intelligence_db.execute(
        WinnerProbabilityEstimate.__table__.update()
        .where(WinnerProbabilityEstimate.id == 81007)
        .values(
            point_probability=None,
            lower_bound=None,
            upper_bound=None,
            evidence_grade="Insufficient",
            insufficient_reasons_json=["SAMPLE_TOO_SMALL"],
        )
    )
    result = request(intelligence_client, "winner").json()
    assert result["state"] == "insufficient" and "SAMPLE_TOO_SMALL" in result["html"]
    assert "0.0%" not in result["html"]
    intelligence_db.execute(
        WinnerPredictionSnapshot.__table__.update()
        .where(WinnerPredictionSnapshot.run_id == 7)
        .values(superseded_at=CUTOFF)
    )
    assert request(intelligence_client, "winner").json()["state"] == "empty"
    intelligence_db.execute(
        WinnerPredictionSnapshot.__table__.update()
        .where(WinnerPredictionSnapshot.run_id == 7)
        .values(superseded_at=None, source_ids_json={"combined_evidence_id": 6})
    )
    assert request(intelligence_client, "winner").json()["state"] == "empty"


def test_identity_run_validation_missing_and_changed_evidence(intelligence_db, intelligence_client):
    assert (
        intelligence_client.get("/api/tickers/UNKNOWN/intelligence/ceri").json()["state"]
        == "unknown_ticker"
    )
    raw(intelligence_db, "EMPTY", "No intelligence", 1)
    for section in service.SECTION_TITLES:
        result = intelligence_client.get(
            f"/api/tickers/EMPTY/intelligence/{section}?detail=true"
        ).json()
        assert result["state"] in {"empty", "unavailable"}
    changed = intelligence_client.get(
        "/api/tickers/EOG/intelligence/rankings?run_id=7&evidence_id=999"
    ).json()
    assert changed["state"] == "changed"
    assert request(intelligence_client, "rankings", run=999).json()["state"] == "unavailable"
    assert intelligence_client.get("/api/tickers/EOG/intelligence/bad").status_code == 422
    assert (
        intelligence_client.get("/api/tickers/EOG/intelligence/ceri?identity=bad").status_code
        == 422
    )

    insert(
        intelligence_db,
        CeriCompany,
        id=8001,
        ticker="EOG",
        company_name="Other issuer",
        exchange="LSE",
    )
    for section in service.SECTION_TITLES:
        assert (
            request(intelligence_client, section).json()["state"] == "decision_identity_unverified"
        )


def test_section_failure_does_not_block_page_or_siblings(intelligence_client, monkeypatch):
    def fail(*args, **kwargs):
        raise SQLAlchemyError("private diagnostic must not leak")

    monkeypatch.setattr(service, "_ceri", fail)
    failed = request(intelligence_client, "ceri")
    assert failed.status_code == 503 and "private diagnostic" not in failed.text
    assert request(intelligence_client, "rankings").status_code == 200
    main = intelligence_client.get("/tickers/EOG?run_id=7")
    assert main.status_code == 200 and main.text.count("data-intelligence-url=") == 4


def test_all_intelligence_reads_in_read_only_transaction_and_bounded_summaries(
    intelligence_db, intelligence_client
):
    intelligence_db.commit()
    intelligence_db.execute(text("SET TRANSACTION READ ONLY"))
    statements = []
    event.listen(
        intelligence_db.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )
    for section in service.SECTION_TITLES:
        start = len(statements)
        summary = request(intelligence_client, section, detail=False).json()
        assert "html" not in summary and len(str(summary)) < 1000
        assert len(statements) - start <= 12
        assert request(intelligence_client, section).status_code == 200
    assert all(s.lstrip().upper().startswith(("SELECT", "WITH")) for s in statements)
    assert not intelligence_db.new and not intelligence_db.dirty and not intelligence_db.deleted


def test_external_text_escaped(intelligence_db, intelligence_client):
    intelligence_db.execute(
        CeriScoreSnapshot.__table__.update()
        .where(CeriScoreSnapshot.id == 9000)
        .values(
            top_positive_contributors_json=[{"name": "<img src=x onerror=alert(1)>", "value": 8}]
        )
    )
    html = request(intelligence_client, "ceri").json()["html"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in html and "<img src=x" not in html
