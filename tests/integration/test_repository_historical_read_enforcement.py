from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriScoreSnapshot
from app.models.tables import (
    CombinedResult,
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    MarketRegimeSnapshot,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvent,
    SetupLifecycleTransitionEvidence,
    SetupSignalSnapshot,
    SignalAlertDecisionEvidence,
    SignalAlertEvent,
    SignalAlertRule,
    SignalAlertRuleEvidence,
    UploadRun,
    WinnerPredictionSnapshot,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.query_service import (
    CeriListQuery,
    CeriQueryError,
    CeriQueryFilters,
    CeriQueryService,
)
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    calculation_evidence_payload,
)
from app.services.historical_read_service import (
    HistoricalReadError,
    ReadMode,
    read_alert_evidence,
    read_core_artifact,
    read_current_lifecycle,
    read_lifecycle_evidence,
)
from app.services.history_query_service import DecisionFilters, paged_decisions
from app.services.ib_market_intelligence.query_service import feature_evidence
from app.services.market_regime_repository import MarketRegimeRepository
from app.services.pipeline_executor import (
    _require_frozen_context_evidence,
    _require_manifest_artifact,
    _require_unchanged_core_evidence,
    _validate_resume_evidence,
)
from app.services.sector_rotation_repository import SectorRotationRepository
from app.services.setup_lifecycle.query_service import (
    SetupLifecycleViewScope,
    _MarketChangePayloadContext,
    alert_payload,
    market_change_payload,
)
from app.services.winner_probability.api_service import _prediction_payload


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint_sqlite(_type, _compiler, **_kwargs):
    return "INTEGER"


@pytest.fixture
def evidence_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        UploadRun.__table__,
        CoreCalculationEvidence.__table__,
        CoreCalculationCurrentProjection.__table__,
        CombinedResult.__table__,
    ):
        table.create(engine)
    with Session(engine) as db:
        db.add(
            UploadRun(
                id=1,
                filename="history.csv",
                status="COMPLETED",
                row_count=1,
                uploaded_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )
        db.flush()
        yield db


@pytest.mark.parametrize(
    "kind",
    [
        CoreEvidenceKind.FUNDAMENTAL,
        CoreEvidenceKind.TECHNICAL,
        CoreEvidenceKind.COMBINED,
        CoreEvidenceKind.RANKING,
    ],
)
def test_core_read_modes_never_cross_fallback_or_collapse_same_run(
    evidence_db: Session,
    kind: CoreEvidenceKind,
) -> None:
    profile = "quality" if kind is CoreEvidenceKind.RANKING else None
    old = _evidence(evidence_db, 10, kind, identity="identity-old")
    current = _evidence(
        evidence_db,
        11,
        kind,
        identity="identity-current",
    )
    evidence_db.add(
        CoreCalculationCurrentProjection(
            id=1,
            artifact_kind=kind.value,
            run_id=1,
            ticker="ACME",
            ranking_profile_key=profile or "",
            evidence_id=current.id,
        )
    )
    evidence_db.flush()

    historical = read_core_artifact(
        evidence_db,
        kind=kind,
        mode=ReadMode.EVIDENCE,
        evidence_id=old.id,
    )
    projected = read_core_artifact(
        evidence_db,
        kind=kind,
        mode=ReadMode.CURRENT,
        run_id=1,
        ticker="ACME",
        ranking_profile=profile,
    )

    assert historical.id == old.id
    assert projected.id == current.id
    with pytest.raises(EvidenceUnavailableError, match="EVIDENCE_UNAVAILABLE"):
        read_core_artifact(
            evidence_db,
            kind=kind,
            mode=ReadMode.EVIDENCE,
            evidence_id=999,
        )
    with pytest.raises(HistoricalReadError) as exc:
        read_core_artifact(
            evidence_db,
            kind=kind,
            mode=ReadMode.ORIGINAL_CONTEXT,
        )
    assert exc.value.code == "ORIGINAL_CONTEXT_RECONSTRUCTION_UNSUPPORTED"


@pytest.mark.parametrize(
    "kind",
    [
        CoreEvidenceKind.FUNDAMENTAL,
        CoreEvidenceKind.TECHNICAL,
        CoreEvidenceKind.COMBINED,
        CoreEvidenceKind.REGIME,
        CoreEvidenceKind.IBMI,
        CoreEvidenceKind.SETUP,
    ],
)
def test_historical_evidence_miss_never_uses_an_existing_current_projection(
    evidence_db: Session,
    kind: CoreEvidenceKind,
) -> None:
    current = _evidence(evidence_db, None, kind, identity=f"current-{kind.value}")
    evidence_db.add(
        CoreCalculationCurrentProjection(
            artifact_kind=kind.value,
            run_id=1,
            ticker="ACME",
            ranking_profile_key="",
            evidence_id=current.id,
        )
    )
    evidence_db.flush()

    with pytest.raises(EvidenceUnavailableError):
        read_core_artifact(
            evidence_db,
            kind=kind,
            mode=ReadMode.EVIDENCE,
            evidence_id=987654,
        )


def test_generic_historical_decisions_read_evidence_not_combined_projection(
    evidence_db: Session,
) -> None:
    historical = _evidence(
        evidence_db,
        20,
        CoreEvidenceKind.COMBINED,
        identity="combined-history",
        payload={
            "final_rank": 1,
            "company_name": "Historical Co",
            "sector": "Technology",
            "final_score": "8.75",
            "combined_decision": "Strong candidate",
            "position_size_hint": "Starter",
            "has_warning": False,
            "is_complete": True,
        },
    )
    evidence_db.add(
        CombinedResult(
            id=50,
            run_id=1,
            ticker="ACME",
            company_name="Current Rewrite",
            final_score=Decimal("1.0"),
            combined_decision="No trade",
            is_complete=True,
        )
    )
    evidence_db.flush()

    page = paged_decisions(
        evidence_db,
        DecisionFilters(ticker="ACME"),
        page=1,
        page_size=20,
    )

    assert page.total_items == 1
    assert page.items[0].evidence_id == historical.id
    assert page.items[0].company_name == "Historical Co"
    assert page.items[0].combined_decision == "Strong candidate"
    assert page.items[0].read_mode == "EVIDENCE"


def test_contextual_and_ibmi_exact_id_readers_reject_wrong_kinds(
    evidence_db: Session,
) -> None:
    regime = _evidence(evidence_db, 30, CoreEvidenceKind.REGIME, identity="regime")
    sector = _evidence(evidence_db, 31, CoreEvidenceKind.SECTOR, identity="sector")
    ibmi = _evidence(evidence_db, 32, CoreEvidenceKind.IBMI, identity="ibmi")

    assert MarketRegimeRepository().evidence(evidence_db, regime.id).id == regime.id
    assert SectorRotationRepository().evidence(evidence_db, sector.id).id == sector.id
    assert feature_evidence(evidence_db, evidence_id=ibmi.id)["evidence_id"] == ibmi.id
    with pytest.raises(EvidenceUnavailableError):
        MarketRegimeRepository().evidence(evidence_db, sector.id)


def test_regime_sector_ibmi_and_setup_ignore_newer_current_pointers(
    evidence_db: Session,
) -> None:
    regime_old = _evidence(
        evidence_db,
        33,
        CoreEvidenceKind.REGIME,
        identity="regime-cross-run-old",
        run_id=None,
        payload={"regime": "RISK_ON"},
    )
    ranking_old = _evidence(
        evidence_db,
        34,
        CoreEvidenceKind.RANKING,
        identity="ranking-old",
    )
    sector_old = _evidence(
        evidence_db,
        35,
        CoreEvidenceKind.SECTOR,
        identity="sector-old",
        source_ids={"regime": regime_old.id, "ranking": ranking_old.id},
    )
    ibmi_old = _evidence(
        evidence_db,
        36,
        CoreEvidenceKind.IBMI,
        identity="ibmi-old",
        source_ids={"borrow": 901, "market_data": 902},
    )
    setup_old = _evidence(
        evidence_db,
        37,
        CoreEvidenceKind.SETUP,
        identity="setup-old",
        source_ids={"combined": 903, "ranking_metadata": ranking_old.id},
    )
    current_rows = [
        _evidence(evidence_db, 38, CoreEvidenceKind.REGIME, identity="regime-new"),
        _evidence(evidence_db, 39, CoreEvidenceKind.SECTOR, identity="sector-new"),
        _evidence(evidence_db, 40, CoreEvidenceKind.IBMI, identity="ibmi-new"),
        _evidence(evidence_db, 41, CoreEvidenceKind.SETUP, identity="setup-new"),
    ]
    for pointer_id, current in enumerate(current_rows, start=100):
        evidence_db.add(
            CoreCalculationCurrentProjection(
                id=pointer_id,
                artifact_kind=current.artifact_kind,
                run_id=current.run_id,
                ticker=current.ticker,
                ranking_profile_key="",
                evidence_id=current.id,
            )
        )
    evidence_db.flush()

    assert MarketRegimeRepository().evidence(evidence_db, regime_old.id).id == regime_old.id
    sector = SectorRotationRepository().evidence(evidence_db, sector_old.id)
    assert sector.source_evidence_ids_json == {
        "regime": regime_old.id,
        "ranking": ranking_old.id,
    }
    assert feature_evidence(evidence_db, evidence_id=ibmi_old.id)[
        "source_evidence_ids"
    ] == {"borrow": 901, "market_data": 902}
    assert read_core_artifact(
        evidence_db,
        kind=CoreEvidenceKind.SETUP,
        mode=ReadMode.EVIDENCE,
        evidence_id=setup_old.id,
    ).id == setup_old.id
    assert read_core_artifact(
        evidence_db,
        kind=CoreEvidenceKind.SETUP,
        mode=ReadMode.CURRENT,
        run_id=1,
        ticker="ACME",
    ).id == current_rows[-1].id


def test_lifecycle_history_ignores_advanced_episode_and_missing_evidence_fails() -> None:
    evaluation = SetupLifecycleEvaluationEvidence(
        id=451,
        payload_json={"output_state": "DEVELOPING", "setup_evidence_id": 37},
        payload_fingerprint="evaluation-old",
    )
    transition = SetupLifecycleTransitionEvidence(
        id=452,
        payload_json={
            "from_state": None,
            "to_state": "DEVELOPING",
            "evaluation_evidence_id": 451,
        },
        payload_fingerprint="transition-old",
    )
    episode = SetupLifecycleEpisode(
        id=453,
        current_state="READY",
        current_phase="READY",
        latest_evaluation_evidence_id=451,
        latest_transition_evidence_id=452,
    )
    db = _ObjectDb([evaluation, transition, episode])

    historical = read_lifecycle_evidence(db, transition_evidence_id=452)
    current = read_current_lifecycle(db, episode_id=453)

    assert historical["payload"]["to_state"] == "DEVELOPING"
    assert current["state"] == "READY"
    with pytest.raises(EvidenceUnavailableError):
        read_lifecycle_evidence(db, transition_evidence_id=999)


def test_historical_setup_change_payload_overlays_sealed_transition() -> None:
    snapshot = SetupSignalSnapshot(
        id=460,
        evidence_id=461,
        ticker="ACME",
        timeframe="1d",
    )
    event = SetupLifecycleEvent(
        id=462,
        snapshot_id=460,
        transition_evidence_id=463,
        ticker="ACME",
        timeframe="1d",
        from_state="DEVELOPING",
        to_state="FAILED",
        effective_date=date(2026, 9, 1),
    )
    transition = SetupLifecycleTransitionEvidence(
        id=463,
        payload_json={
            "ticker": "ACME",
            "timeframe": "1d",
            "from_state": "DISCOVERED",
            "to_state": "DEVELOPING",
            "effective_session": "2026-09-01",
        },
    )
    setup = CoreCalculationEvidence(
        id=461,
        artifact_kind="SETUP",
        payload_json={"ticker": "ACME"},
    )
    context = _MarketChangePayloadContext(
        current_snapshots={460: snapshot},
        explicit_previous_snapshots={},
        previous_by_current_snapshot={},
        lifecycle_by_snapshot={460: event},
        episodes={},
        view_scope=SetupLifecycleViewScope.HISTORICAL_RUN,
        transition_evidence={463: transition},
        setup_evidence={461: setup},
    )

    payload = market_change_payload(
        _ObjectDb([snapshot, event, transition, setup]),
        lifecycle_event=event,
        context=context,
    )

    assert payload["read_mode"] == "CERTIFIED_EVIDENCE"
    assert payload["current_state"] == "DEVELOPING"
    assert payload["to_state"] == "DEVELOPING"
    assert payload["historical_evidence"]["from_state"] == "DISCOVERED"


def test_ceri_stored_history_uses_sealed_output_and_excludes_current_changes() -> None:
    cutoff = datetime(2026, 9, 2, 12, tzinfo=UTC)
    snapshot = CeriScoreSnapshot(
        id=40,
        evidence_id=400,
        run_id=1,
        company_id=1,
        ticker="ACME",
        as_of_session=date(2026, 9, 1),
        cutoff_at=cutoff + timedelta(days=30),
        opportunity_score=1.0,
        event_risk_score=9.0,
        data_confidence="Low",
        coverage_pct=10.0,
        posture="Current rewrite",
        config_version="r2",
        config_hash="r2",
        calculation_version="ceri-2",
        evidence_hash="current",
    )
    evidence = SimpleNamespace(
        id=400,
        artifact_kind="CERI",
        run_id=1,
        ticker="ACME",
        calculation_identity_fingerprint="ceri-old",
        payload_fingerprint="ceri-payload-old",
        source_evidence_ids_json={},
        payload_json={
            "decision_output": {
                "ticker": "ACME",
                "run_id": 1,
                "cutoff_at": cutoff.isoformat(),
                "as_of_session": "2026-09-01",
                "opportunity_score": "8.0",
                "event_risk_score": "2.0",
                "posture": "Historical sealed",
            }
        },
    )
    db = _CollectionDb({CeriScoreSnapshot: [snapshot], CoreCalculationEvidence: [evidence]})

    payload = CeriQueryService().ticker_history(
        db,
        "ACME",
        CeriListQuery(
            filters=CeriQueryFilters(
                mode="STORED_SNAPSHOT",
                as_of=cutoff + timedelta(days=1),
                opportunity_min=7,
                posture="Historical sealed",
            ),
            sort="cutoff_at",
        ),
    )

    assert payload["read_mode"] == "CERTIFIED_EVIDENCE"
    assert payload["items"][0]["posture"] == "Historical sealed"
    assert payload["items"][0]["opportunity_score"] == "8.0"

    with pytest.raises(CeriQueryError) as exc:
        CeriQueryService().ticker_history(
            db,
            "ACME",
            CeriListQuery(
                filters=CeriQueryFilters(
                    mode="STORED_SNAPSHOT",
                    as_of=cutoff + timedelta(days=1),
                    catalyst_category="EARNINGS",
                )
            ),
        )
    assert exc.value.code == "HISTORICAL_RECONSTRUCTION_UNSUPPORTED"


def test_alert_history_resolves_r1_while_current_notification_uses_r2() -> None:
    r1 = SignalAlertRuleEvidence(
        id=501,
        rule_id="READY",
        config_version="r1",
        payload_json={"rule_id": "READY", "severity": "HIGH", "config_version": "r1"},
        payload_fingerprint="r1",
        evidence_key="r1",
    )
    decision = SignalAlertDecisionEvidence(
        id=502,
        rule_evidence_id=501,
        ticker="ACME",
        timeframe="1d",
        effective_session=date(2026, 9, 1),
        source_event_key="ready-1",
        semantic_key="ready",
        decision="GENERATED",
        reasons_json=["READY"],
        payload_json={"decision_payload": {"source_confidence": 91}},
        payload_fingerprint="decision",
        evidence_key="decision",
    )
    current_rule = SignalAlertRule(
        id=503,
        rule_id="READY_R2",
        enabled=True,
        severity="LOW",
        scope="GLOBAL",
        cooldown_sessions=0,
        minimum_confidence=0,
        config_version="r2",
    )
    event = SignalAlertEvent(
        id=504,
        alert_rule_id=503,
        decision_evidence_id=502,
        ticker="ACME",
        timeframe="1d",
        effective_date=date(2026, 9, 1),
        event_key="event",
        source_event_key="ready-1",
        status="ACKNOWLEDGED",
        severity="LOW",
        reason_codes_json=["current"],
    )
    db = _ObjectDb([r1, decision, current_rule, event])

    historical = read_alert_evidence(db, decision_evidence_id=502)
    projected = alert_payload(event, db=db)

    assert historical["rule_payload"]["config_version"] == "r1"
    assert projected["review_status"] == "ACKNOWLEDGED"
    assert projected["alert_type"] == "READY"
    assert projected["severity"] == "HIGH"
    assert projected["read_mode"] == "CURRENT_NOTIFICATION_WITH_CERTIFIED_DECISION"


def test_winner_historical_payload_uses_pinned_upstream_evidence() -> None:
    prediction = WinnerPredictionSnapshot(
        id=601,
        run_id=1,
        ticker="ACME",
        prediction_as_of_date=date(2026, 9, 1),
        source_data_cutoff_at=datetime(2026, 9, 1, tzinfo=UTC),
        combined_result_id=9001,
        ranking_result_id=9002,
        feature_schema_version="winner-features-v1",
        feature_vector_hash="frozen-vector",
        config_hash="winner-config",
        calculation_version="winner-v1",
        feature_json={"frozen": True},
        source_ids_json={
            "combined_evidence_id": 602,
            "ranking_evidence_id": 603,
        },
    )
    combined = SimpleNamespace(
        id=602,
        artifact_kind="COMBINED",
        run_id=1,
        ticker="ACME",
        payload_json={"final_rank": 2},
    )
    ranking = SimpleNamespace(
        id=603,
        artifact_kind="RANKING",
        run_id=1,
        ticker="ACME",
        payload_json={"profile_rank": 3, "profile_score": "7.25"},
    )

    payload = _prediction_payload(prediction, db=_ObjectDb([combined, ranking]))

    assert payload["read_mode"] == "FROZEN_WINNER_EVIDENCE"
    assert payload["final_rank"] == 2
    assert payload["ranking_rank"] == 3
    assert payload["ranking_score"] == 7.25


def test_resume_evidence_guards_reject_legacy_and_changed_current_rows(
    evidence_db: Session,
) -> None:
    with pytest.raises(ValueError, match="LEGACY_EVIDENCE_UNAVAILABLE"):
        _validate_resume_evidence(
            _NoHandoffDb(),
            pipeline=SimpleNamespace(id=7),
            upload_run_id=1,
        )

    row = CombinedResult(
        id=70,
        run_id=1,
        ticker="ACME",
        final_score=Decimal("8.0"),
        is_complete=True,
    )
    evidence_db.add(row)
    evidence_db.flush()
    original_payload = calculation_evidence_payload(row)
    evidence = _evidence(
        evidence_db,
        71,
        CoreEvidenceKind.COMBINED,
        identity="resume",
        payload=original_payload,
    )
    row.evidence_id = evidence.id
    evidence_db.flush()
    newer_current = _evidence(
        evidence_db,
        72,
        CoreEvidenceKind.COMBINED,
        identity="resume-new-current",
    )
    evidence_db.add(
        CoreCalculationCurrentProjection(
            id=73,
            artifact_kind=CoreEvidenceKind.COMBINED.value,
            run_id=1,
            ticker="ACME",
            ranking_profile_key="",
            evidence_id=newer_current.id,
        )
    )
    evidence_db.flush()

    expected = {
        "id": row.id,
        "semantic_hash": CanonicalEvidenceSerializer.fingerprint(
            {
                column.name: getattr(row, column.name, None)
                for column in row.__table__.columns
                if column.name
                not in {"created_at", "updated_at", "last_seen_at", "calculated_at"}
            }
        ),
    }
    _require_manifest_artifact(row, expected, label="ACME:combined")
    assert (
        _require_unchanged_core_evidence(
            evidence_db,
            kind=CoreEvidenceKind.COMBINED,
            row=row,
        )
        == evidence.id
    )
    assert read_core_artifact(
        evidence_db,
        kind=CoreEvidenceKind.COMBINED,
        mode=ReadMode.CURRENT,
        run_id=1,
        ticker="ACME",
    ).id == newer_current.id
    row.final_score = Decimal("1.0")
    with pytest.raises(ValueError, match="artifact changed"):
        _require_manifest_artifact(row, expected, label="ACME:combined")
    with pytest.raises(ValueError, match="compatibility row changed"):
        _require_unchanged_core_evidence(
            evidence_db,
            kind=CoreEvidenceKind.COMBINED,
            row=row,
        )


def test_resume_uses_manifest_context_evidence_after_current_revision_advances() -> None:
    frozen = MarketRegimeSnapshot(
        id=801,
        run_id=44,
        evidence_id=802,
        is_current_revision=True,
        superseded_by_snapshot_id=None,
        superseded_at=None,
    )
    expected = {
        "id": frozen.id,
        "semantic_hash": CanonicalEvidenceSerializer.fingerprint(
            {
                column.name: getattr(frozen, column.name, None)
                for column in frozen.__table__.columns
                if column.name
                not in {"created_at", "updated_at", "last_seen_at", "calculated_at"}
            }
        ),
    }
    frozen.is_current_revision = False
    frozen.superseded_by_snapshot_id = 999
    frozen.superseded_at = datetime(2026, 9, 2, tzinfo=UTC)
    evidence = CoreCalculationEvidence(
        id=802,
        artifact_kind=CoreEvidenceKind.REGIME.value,
        run_id=44,
        ticker=None,
    )

    assert (
        _require_frozen_context_evidence(
            _ObjectDb([frozen, evidence]),
            kind=CoreEvidenceKind.REGIME,
            model=MarketRegimeSnapshot,
            expected=expected,
            label="ACME:market_regime_snapshot",
        )
        == evidence.id
    )


def _evidence(
    db: Session,
    row_id: int | None,
    kind: CoreEvidenceKind,
    *,
    identity: str,
    payload: dict | None = None,
    run_id: int | None = 1,
    source_ids: dict[str, int] | None = None,
) -> CoreCalculationEvidence:
    payload = payload or {"value": identity}
    fingerprint = CanonicalEvidenceSerializer.fingerprint(payload)
    row = CoreCalculationEvidence(
        id=row_id,
        artifact_kind=kind.value,
        run_id=run_id,
        ticker="ACME",
        ranking_profile="quality" if kind is CoreEvidenceKind.RANKING else None,
        calculation_identity_fingerprint=identity,
        calculation_identity_json={"identity": identity},
        payload_fingerprint=fingerprint,
        payload_json=payload,
        source_evidence_ids_json=source_ids or {},
        evidence_key=f"{kind.value}:{identity}",
        calculated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    db.add(row)
    db.flush()
    return row


class _CollectionDb:
    def __init__(self, collections: dict) -> None:
        self.collections = collections

    def get(self, model, row_id):
        return next(
            (row for row in self.collections.get(model, []) if row.id == row_id),
            None,
        )


class _ObjectDb:
    def __init__(self, rows: list) -> None:
        self.rows = {(type(row), row.id): row for row in rows}

    def get(self, model, row_id):
        direct = self.rows.get((model, row_id))
        if direct is not None:
            return direct
        return next(
            (
                row
                for (_row_type, candidate_id), row in self.rows.items()
                if candidate_id == row_id
                and getattr(row, "artifact_kind", None) is not None
                and model is CoreCalculationEvidence
            ),
            None,
        )

    def scalar(self, _statement):
        return None


class _NoHandoffDb:
    def scalar(self, _statement):
        return None
