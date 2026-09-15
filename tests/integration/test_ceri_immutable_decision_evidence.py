from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, create_engine, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alembic import command
from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriChangeEvent,
    CeriDerivedFeature,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriPriceResponseFeature,
    CeriPurgeAudit,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    PriceBar,
    PriceBarRevision,
    UploadRun,
)
from app.services.ceri.config import ceri_config_hash, load_ceri_config
from app.services.ceri.decision_evidence import (
    persist_ceri_decision_evidence,
)
from app.services.ceri.purge_service import (
    CeriPurgeError,
    CeriPurgeExecuteRequest,
    CeriPurgePreviewRequest,
    CeriPurgeService,
    confirmation_token_for_preview,
)
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.contextual_calculation_identity import (
    build_contextual_result_identity,
    build_ibmi_feature_identity,
    consumer_context_identity,
    identity_metadata,
)
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    get_current_evidence,
    get_evidence_for_identity,
    persist_core_evidence,
)
from app.services.market_clock_service import MarketCalculationCutoff

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_ceri_evidence_test(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint_for_ceri_evidence_test(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@pytest.fixture
def evidence_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = (
        UploadRun.__table__,
        CoreCalculationEvidence.__table__,
        CoreCalculationEvidenceSource.__table__,
        CoreCalculationCurrentProjection.__table__,
        CeriSourceRecord.__table__,
        CeriEstimateSnapshot.__table__,
        CeriEarningsActual.__table__,
        CeriGuidanceEvent.__table__,
        CeriCatalystEvent.__table__,
        CeriCatalystEventRevision.__table__,
        CeriCatalystSource.__table__,
        CeriRevisionFeature.__table__,
        CeriDerivedFeature.__table__,
        IBIntelligenceFeature.__table__,
        PriceBar.__table__,
        PriceBarRevision.__table__,
        CeriPriceResponseFeature.__table__,
        CeriScoreSnapshot.__table__,
        CeriChangeEvent.__table__,
        CeriAlertEvent.__table__,
        CeriPurgeAudit.__table__,
    )
    for table in tables:
        table.create(engine)
    with Session(engine) as db:
        for run_id in range(7, 21):
            db.add(UploadRun(id=run_id, filename=f"ceri-{run_id}.csv", status="COMPLETED"))
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def test_0077_upgrade_and_downgrade_compile_as_postgresql_ddl() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260915_0077_ceri_immutable_decision_evidence.py"
    )
    spec = spec_from_file_location("t11b2_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    ddl = output.getvalue()
    assert "ALTER TABLE ceri_score_snapshots ADD COLUMN evidence_id BIGINT" in ddl
    assert "CERI" in ddl
    assert "run_id IS NULL AND ticker IS NOT NULL" in ddl


def test_0077_disposable_postgresql_upgrade_and_downgrade(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    schema = inspect(engine)
    columns = {column["name"]: column for column in schema.get_columns("ceri_score_snapshots")}
    assert columns["evidence_id"]["nullable"] is True
    command.downgrade(config, "0076_regime_sector_evidence")
    assert "evidence_id" not in {
        column["name"] for column in inspect(engine).get_columns("ceri_score_snapshots")
    }
    engine.dispose()


def test_ceri_evidence_immutability_and_recalculation(evidence_db: Session) -> None:
    first, identity_1 = _persist(evidence_db, run_id=7, source_ids=[])
    evidence_1 = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.CERI,
        calculation_identity=identity_1,
        ticker="ACME",
    )
    original = deepcopy(evidence_1.payload_json)

    second, _identity_2 = _persist(evidence_db, run_id=8, source_ids=[], posture="Mixed")
    assert first.evidence_id != second.evidence_id
    assert evidence_1.payload_json == original
    assert (
        get_current_evidence(
            evidence_db,
            kind=CoreEvidenceKind.CERI,
            run_id=7,
            ticker="ACME",
        ).id
        == first.evidence_id
    )


def test_provider_correction_separates_as_known_and_latest_corrected(
    evidence_db: Session,
) -> None:
    old = _source(evidence_db, 101, "old-hash", mode="AS_KNOWN")
    corrected = _source(
        evidence_db,
        102,
        "corrected-hash",
        mode="LATEST_CORRECTED",
        supersedes_id=old.id,
    )
    c1, identity_1 = _persist(evidence_db, run_id=9, source_ids=[old.id], view_mode="AS_KNOWN")
    c2, _identity_2 = _persist(
        evidence_db,
        run_id=10,
        source_ids=[corrected.id],
        view_mode="LATEST_CORRECTED",
    )
    first = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.CERI,
        calculation_identity=identity_1,
        ticker="ACME",
    )
    assert c1.evidence_id != c2.evidence_id
    assert first.payload_json["source_manifest"]["historical_view_mode"] == "AS_KNOWN"
    assert first.payload_json["source_manifest"]["source_records"][0]["id"] == old.id
    assert first.payload_json["source_manifest"]["source_records"][0]["content_hash"] == "old-hash"


def test_price_correction_cannot_reinterpret_old_decision(evidence_db: Session) -> None:
    cutoff = _cutoff(11)
    bar = PriceBar(
        id=301,
        ticker="ACME",
        bar_date=cutoff.latest_completed_session,
        timeframe="1 day",
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10.5"),
        volume=Decimal("1000"),
        source="ibkr",
        what_to_show="TRADES",
        first_seen_at=cutoff.cutoff_at,
        last_seen_at=cutoff.cutoff_at,
        data_hash="bar-old",
    )
    evidence_db.add(bar)
    evidence_db.add(
        CeriPriceResponseFeature(
            id=401,
            company_id=1,
            ticker="ACME",
            event_type="EARNINGS",
            event_id=1,
            feature_as_of_session=cutoff.latest_completed_session,
            calculation_cutoff_at=cutoff.cutoff_at,
            ownership_mode="STANDALONE",
            metrics_json={"quality": 7.0},
            price_bar_ids_json=[301],
            evidence_hash="price-response-old",
            event_key="price-401",
            config_version="v1",
            config_hash="a" * 64,
            calculation_version="ceri-1.3.0",
        )
    )
    evidence_db.flush()
    snapshot, identity = _persist(
        evidence_db,
        run_id=11,
        source_ids=[],
        price_feature_ids=[401],
        price_bar_ids=[301],
    )
    sealed = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.CERI,
        calculation_identity=identity,
        ticker="ACME",
    )
    frozen = deepcopy(sealed.payload_json["source_manifest"]["price_bars_as_known"])
    bar.close = Decimal("12")
    bar.data_hash = "bar-corrected"
    evidence_db.flush()
    assert snapshot.evidence_id == sealed.id
    assert sealed.payload_json["source_manifest"]["price_bars_as_known"] == frozen
    assert frozen[0]["data_hash"] == "bar-old"


def test_rule_change_freezes_old_rule_payload(evidence_db: Session) -> None:
    config_1 = load_ceri_config()
    weights = dict(config_1.opportunity_weights)
    weights["revision_magnitude"] -= 0.01
    weights["revision_breadth"] += 0.01
    config_2 = replace(config_1, opportunity_weights=weights, config_hash="")
    config_2 = replace(config_2, config_hash=ceri_config_hash(config_2))
    c1, identity_1 = _persist(evidence_db, run_id=12, source_ids=[], config=config_1)
    c2, _identity_2 = _persist(evidence_db, run_id=13, source_ids=[], config=config_2)
    sealed = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.CERI,
        calculation_identity=identity_1,
        ticker="ACME",
    )
    rules = sealed.payload_json["effective_rule_payload"]
    assert c1.evidence_id != c2.evidence_id
    assert rules["declared_config_hash"] == config_1.config_hash
    assert rules["resolved_config"]["opportunity_weights"] == config_1.opportunity_weights
    assert rules["posture_thresholds"]["binary_risk_min"] == 6.0


def test_purge_is_forbidden_for_referenced_decision_evidence(
    evidence_db: Session,
) -> None:
    source = _source(evidence_db, 501, "licensed-hash", purge_eligible=True)
    snapshot, _identity = _persist(evidence_db, run_id=14, source_ids=[source.id])
    service = CeriPurgeService()
    preview = service.preview(
        evidence_db,
        CeriPurgePreviewRequest(
            provider="eodhd",
            license_scope="licensed-test",
            actor="test",
            reason="retention test",
        ),
    )
    with pytest.raises(CeriPurgeError, match="immutable CERI decision evidence"):
        service.execute(
            evidence_db,
            CeriPurgeExecuteRequest(
                provider="eodhd",
                license_scope="licensed-test",
                actor="test",
                reason="retention test",
                preview_manifest_hash=preview.preview_manifest_hash,
                confirmation_token=confirmation_token_for_preview(preview.preview_manifest_hash),
            ),
        )
    assert source.raw_json == {"mode": "AS_KNOWN"}
    assert snapshot.posture == "Positive"


def test_rebuild_creates_new_evidence_without_mutating_prior(evidence_db: Session) -> None:
    c1, identity_1 = _persist(evidence_db, run_id=15, source_ids=[])
    before = deepcopy(
        get_evidence_for_identity(
            evidence_db,
            kind=CoreEvidenceKind.CERI,
            calculation_identity=identity_1,
            ticker="ACME",
        ).payload_json
    )
    c2, _identity_2 = _persist(
        evidence_db,
        run_id=16,
        source_ids=[],
        view_mode="LATEST_CORRECTED",
        posture="Improving",
    )
    assert c1.evidence_id != c2.evidence_id
    assert (
        get_evidence_for_identity(
            evidence_db,
            kind=CoreEvidenceKind.CERI,
            calculation_identity=identity_1,
            ticker="ACME",
        ).payload_json
        == before
    )


def test_same_output_different_source_evidence_is_distinct(evidence_db: Session) -> None:
    first_source = _source(evidence_db, 601, "first")
    second_source = _source(evidence_db, 602, "second")
    c1, _ = _persist(evidence_db, run_id=17, source_ids=[first_source.id])
    c2, _ = _persist(evidence_db, run_id=18, source_ids=[second_source.id])
    assert c1.opportunity_score == c2.opportunity_score
    assert c1.evidence_id != c2.evidence_id


def test_exact_retry_reuses_deterministic_evidence(evidence_db: Session) -> None:
    snapshot, _identity = _persist(evidence_db, run_id=19, source_ids=[])
    first_id = snapshot.evidence_id
    retried = persist_ceri_decision_evidence(
        evidence_db, snapshot=snapshot, config=load_ceri_config()
    )
    assert retried is not None
    assert retried.id == first_id
    assert (
        evidence_db.query(CoreCalculationEvidence)
        .filter_by(artifact_kind="CERI", run_id=19, ticker="ACME")
        .count()
        == 1
    )


def test_legacy_ceri_row_cannot_satisfy_phase2_history(evidence_db: Session) -> None:
    legacy = _snapshot(run_id=20, lineage={}, config=load_ceri_config())
    evidence_db.add(legacy)
    evidence_db.flush()
    assert (
        persist_ceri_decision_evidence(evidence_db, snapshot=legacy, config=load_ceri_config())
        is None
    )
    with pytest.raises(EvidenceUnavailableError, match="EVIDENCE_UNAVAILABLE"):
        get_evidence_for_identity(
            evidence_db,
            kind=CoreEvidenceKind.CERI,
            calculation_identity="legacy-current",
            ticker="ACME",
        )


def test_negative_dependency_edges_remain_absent() -> None:
    root = Path(__file__).resolve().parents[2] / "app" / "services"
    paths = [
        *root.glob("ranking_*.py"),
        *root.joinpath("setup_lifecycle").glob("*.py"),
        *root.joinpath("winner_probability").glob("*.py"),
    ]
    forbidden = ("CeriScoreSnapshot", "CoreEvidenceKind.CERI")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden), path


def test_phase1_identity_is_retained_in_ceri_evidence(evidence_db: Session) -> None:
    snapshot, identity = _persist(evidence_db, run_id=7, source_ids=[])
    sealed = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.CERI,
        calculation_identity=identity,
        ticker="ACME",
    )
    assert snapshot.evidence_id == sealed.id
    assert sealed.calculation_identity_json == identity.canonical_payload()
    assert sealed.calculation_identity_fingerprint == str(identity.fingerprint())


def test_legacy_ibmi_reference_cannot_certify_ceri_evidence(evidence_db: Session) -> None:
    cutoff = _cutoff(8)
    ibmi = IBIntelligenceFeature(
        id=701,
        ticker="ACME",
        as_of_session=cutoff.latest_completed_session,
        calculated_at=cutoff.cutoff_at,
        calculation_cutoff_at=cutoff.cutoff_at,
        calendar_version=cutoff.calendar_version,
        module="VOLATILITY",
        classification="ELEVATED",
        score=Decimal("7"),
        confidence="NORMAL",
        freshness_status="FRESH",
        coverage_status="AVAILABLE",
        components_json={"event_premium": 1.25},
        reasons_json=[],
        warnings_json=[],
        source_evidence_hashes_json=["a" * 64],
        source_version="ibmi-source-v1",
        calculation_version="ibmi-v1",
        config_hash="b" * 64,
        input_signature="ibmi-701",
    )
    evidence_db.add(ibmi)
    evidence_db.flush()
    config = load_ceri_config()
    base = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=8,
        pipeline_id=None,
        ticker="ACME",
        company_id=1,
    )
    identity = build_contextual_result_identity(
        base=base,
        namespace="ceri-context",
        config_hash=config.config_hash,
        calculation_version=config.engine.calculation_version,
        engine_version=config.engine.calculation_version,
        source_artifacts=[],
        source_payload={"ib_volatility_feature_ids": [701]},
        company_id=1,
    )
    lineage = {
        "historical_view_mode": "AS_KNOWN",
        "ib_volatility_feature_ids": [701],
        **identity_metadata(identity, policy="T11B2_TEST"),
    }
    snapshot = _snapshot(run_id=8, lineage=lineage, config=config)
    snapshot.cutoff_at = cutoff.cutoff_at
    snapshot.as_of_session = cutoff.latest_completed_session
    with pytest.raises(EvidenceUnavailableError, match="LEGACY_CURRENT/LEGACY_UNKNOWN"):
        CeriSnapshotService(config=config).persist_snapshot(evidence_db, snapshot)

    ibmi_evidence = persist_core_evidence(
        evidence_db,
        kind=CoreEvidenceKind.IBMI,
        current_row=ibmi,
        payload={
            "schema_version": "ibmi-feature-evidence-v1",
            "constituent_fingerprint": "c" * 64,
            "constituent_manifest": {"historical_metric_states": []},
        },
        scope_ticker="ACME",
        scope_profile="VOLATILITY",
        calculation_identity=build_ibmi_feature_identity(ibmi),
    )
    assert ibmi_evidence is not None
    CeriSnapshotService(config=config).persist_snapshot(evidence_db, snapshot)
    sealed = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.CERI,
        calculation_identity=identity,
        ticker="ACME",
    )
    reference = sealed.payload_json["source_manifest"]["ibmi_features"][0]
    assert reference["immutable_evidence_id"] == ibmi_evidence.id
    assert reference["phase2_constituent_certification"] == "CERTIFIED_T11B3"
    assert sealed.source_evidence_ids_json == {"ibmi_volatility": ibmi_evidence.id}


def _source(
    db: Session,
    source_id: int,
    content_hash: str,
    *,
    mode: str = "AS_KNOWN",
    supersedes_id: int | None = None,
    purge_eligible: bool = False,
) -> CeriSourceRecord:
    row = CeriSourceRecord(
        id=source_id,
        provider="eodhd",
        provider_terms_version="test-v1",
        dataset="estimates",
        provider_record_id=f"provider-{source_id}",
        retrieved_at=datetime(2026, 9, 15, 19, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 15, 19, tzinfo=UTC),
        raw_json={"mode": mode},
        restricted_normalized_json={"consensus": source_id},
        content_hash=content_hash,
        normalized_hash=f"normalized-{content_hash}",
        idempotency_key=f"idempotency-{source_id}",
        export_policy="exportable",
        supersedes_id=supersedes_id,
        correction_type="PROVIDER_CORRECTION" if supersedes_id else None,
        license_scope="licensed-test",
        redistribution_allowed=False,
        purge_eligible=purge_eligible,
    )
    db.add(row)
    db.flush()
    return row


def _persist(
    db: Session,
    *,
    run_id: int,
    source_ids: list[int],
    view_mode: str = "AS_KNOWN",
    posture: str = "Positive",
    config=None,
    price_feature_ids: list[int] | None = None,
    price_bar_ids: list[int] | None = None,
):
    config = config or load_ceri_config()
    cutoff = _cutoff(run_id)
    base = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=run_id,
        pipeline_id=None,
        ticker="ACME",
        company_id=1,
    )
    identity = build_contextual_result_identity(
        base=base,
        namespace="ceri-context",
        config_hash=config.config_hash,
        calculation_version=config.engine.calculation_version,
        engine_version=config.engine.calculation_version,
        source_artifacts=[],
        source_payload={
            "source_ids": source_ids,
            "view_mode": view_mode,
            "price_bar_ids": price_bar_ids or [],
        },
        company_id=1,
    )
    lineage = {
        "historical_view_mode": view_mode,
        "revision_source_ids": list(source_ids),
        "price_response_feature_ids": list(price_feature_ids or []),
        "price_bar_ids": list(price_bar_ids or []),
        **identity_metadata(identity, policy="T11B2_TEST"),
    }
    snapshot = _snapshot(run_id=run_id, lineage=lineage, config=config, posture=posture)
    snapshot.cutoff_at = cutoff.cutoff_at
    snapshot.as_of_session = cutoff.latest_completed_session
    CeriSnapshotService(config=config).persist_snapshot(db, snapshot)
    return snapshot, identity


def _snapshot(*, run_id: int, lineage: dict, config, posture: str = "Positive"):
    return CeriScoreSnapshot(
        run_id=run_id,
        source_run_id_text=str(run_id),
        company_id=1,
        ticker="ACME",
        as_of_session=date(2026, 9, 15),
        cutoff_at=datetime(2026, 9, 15, 20, tzinfo=UTC),
        opportunity_score=7.5,
        opportunity_coverage_pct=100.0,
        event_risk_score=2.0,
        data_confidence="High",
        coverage_pct=100.0,
        posture=posture,
        evidence_lineage_json=lineage,
        component_json={"components": [], "source_ids": lineage.get("revision_source_ids", [])},
        opportunity_ledger_json={"score": 7.5},
        confidence_ledger_json={"score": 9.0},
        event_risk_ledger_json={"score": 2.0},
        config_version=config.engine.config_version,
        config_hash=config.config_hash,
        calculation_version=config.engine.calculation_version,
        evidence_contract_version="ceri-evidence-contract-v2",
        comparison_state="NO_PRIOR_COMPARABLE_SNAPSHOT",
        evidence_hash=f"score-{run_id}-{config.config_hash}",
        hash_schema_version="ceri-canonical-json-v2",
    )


def _cutoff(run_id: int) -> MarketCalculationCutoff:
    session = date(2026, 9, 14)
    cutoff_at = datetime.combine(session, datetime.min.time(), tzinfo=UTC).replace(
        hour=20, minute=run_id
    )
    return MarketCalculationCutoff(
        cutoff_at=cutoff_at,
        exchange_timezone="America/New_York",
        latest_completed_session=session,
        daily_bar_ready_at=cutoff_at,
        calendar_version="XNYS-2026a",
        bar_readiness_version="daily-close-v1",
        cutoff_reason="T11B2_TEST",
        context_id=None,
    )
