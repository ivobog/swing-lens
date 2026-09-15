from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import BigInteger, create_engine, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alembic import command
from app.models.ib_market_intelligence_tables import (
    IBHistoricalMetricBar,
    IBHistoricalMetricRevision,
    IBIntelligenceFeature,
    IBIntelligenceRequestItem,
    IBIntelligenceRun,
    IBMarketIntelligenceSnapshot,
)
from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    PriceBar,
    PriceBarRevision,
    UploadRun,
)
from app.services.contextual_calculation_identity import build_ibmi_feature_identity
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    get_current_evidence,
    persist_core_evidence,
)
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ib_market_intelligence.decision_evidence import (
    IbmiFeatureConstituents,
    get_certified_ibmi_evidence,
    get_ibmi_evidence,
)
from app.services.ib_market_intelligence.dtos import (
    FeatureResult,
    HistoricalMetricBarDTO,
    LiveSnapshotDTO,
)
from app.services.ib_market_intelligence.enums import IntelligenceModule
from app.services.ib_market_intelligence.orchestration import _rebuild_ticker_feature
from app.services.ib_market_intelligence.repository import (
    persist_feature,
    persist_historical_metric_bar,
    persist_live_snapshot,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_ibmi_evidence_test(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint_for_ibmi_evidence_test(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@pytest.fixture
def evidence_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = (
        UploadRun.__table__,
        CoreCalculationEvidence.__table__,
        CoreCalculationEvidenceSource.__table__,
        CoreCalculationCurrentProjection.__table__,
        IBIntelligenceRun.__table__,
        IBHistoricalMetricBar.__table__,
        IBHistoricalMetricRevision.__table__,
        IBMarketIntelligenceSnapshot.__table__,
        IBIntelligenceRequestItem.__table__,
        PriceBar.__table__,
        PriceBarRevision.__table__,
        IBIntelligenceFeature.__table__,
    )
    postgres_defaults = []
    for table in tables:
        for column in table.columns:
            if column.server_default is not None and "::jsonb" in str(column.server_default.arg):
                postgres_defaults.append((column, column.server_default))
                column.server_default = None
    for table in tables:
        table.create(engine)
    for column, server_default in postgres_defaults:
        column.server_default = server_default
    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="ranking.csv", status="COMPLETED"))
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def test_metric_correction_same_score_retry_and_current_projection(evidence_db: Session) -> None:
    observed_1 = datetime(2026, 9, 14, 20, tzinfo=UTC)
    dto = _metric(close=0.20)
    metric, outcome = persist_historical_metric_bar(
        evidence_db, dto, observed_at=observed_1
    )
    assert outcome == "INSERTED"
    f1, inserted = _persist_metric_feature(
        evidence_db, metric, score=7.0, cutoff=observed_1 + timedelta(minutes=1)
    )
    assert inserted and f1.evidence_id is not None
    e1 = get_certified_ibmi_evidence(evidence_db, f1)
    original = deepcopy(e1.payload_json)
    m1 = e1.payload_json["constituent_manifest"]["historical_metric_states"][0]
    assert m1["revision_number"] == 0
    assert m1["metric_revision_id"] is not None

    corrected, outcome = persist_historical_metric_bar(
        evidence_db,
        _metric(close=0.40),
        observed_at=observed_1 + timedelta(hours=1),
    )
    assert outcome == "REVISED"
    f2, inserted = _persist_metric_feature(
        evidence_db,
        corrected,
        score=7.0,
        cutoff=observed_1 + timedelta(hours=1, minutes=1),
    )
    assert inserted and f2.evidence_id != f1.evidence_id
    e2 = get_certified_ibmi_evidence(evidence_db, f2)
    m2 = e2.payload_json["constituent_manifest"]["historical_metric_states"][0]
    assert m2["revision_number"] == 1
    assert m2["metric_revision_id"] != m1["metric_revision_id"]
    assert e1.payload_json == original

    retry, inserted = _persist_metric_feature(
        evidence_db,
        corrected,
        score=7.0,
        cutoff=observed_1 + timedelta(hours=1, minutes=1),
    )
    assert inserted is False
    assert retry.id == f2.id and retry.evidence_id == f2.evidence_id
    assert get_current_evidence(
        evidence_db,
        kind=CoreEvidenceKind.IBMI,
        run_id=None,
        ticker="ACME",
        ranking_profile="LIQUIDITY",
    ).id == f2.evidence_id


def test_production_rebuild_path_seals_selected_metric_state(evidence_db: Session) -> None:
    observed = datetime(2026, 9, 14, 20, tzinfo=UTC)
    metric, _ = persist_historical_metric_bar(
        evidence_db, _metric(close=0.20), observed_at=observed
    )
    feature, inserted = _rebuild_ticker_feature(
        evidence_db,
        "ACME",
        IntelligenceModule.LIQUIDITY,
        date(2026, 9, 14),
        load_ib_market_intelligence_config(),
        0,
        calculation_cutoff_at=datetime(2026, 9, 15, 12, tzinfo=UTC),
        operation_mode="HISTORICAL",
    )
    assert inserted is True and feature.evidence_id is not None
    states = get_certified_ibmi_evidence(evidence_db, feature).payload_json[
        "constituent_manifest"
    ]["historical_metric_states"]
    assert states[0]["metric_bar_id"] == metric.id
    assert states[0]["revision_number"] == 0


def test_live_shortability_and_availability_are_exact_observations(
    evidence_db: Session,
) -> None:
    config = load_ib_market_intelligence_config()
    observed = datetime(2026, 9, 14, 20, tzinfo=UTC)
    run = IBIntelligenceRun(
        job_type="IBMI",
        module="SHORT_PRESSURE",
        status="COMPLETED",
        deterministic_request_key="run-1",
        scope_json={},
        config_version=config.config_version,
        config_hash=config.config_hash,
        counts_json={},
        checkpoint_json={},
        warning_flags_json=[],
    )
    evidence_db.add(run)
    evidence_db.flush()
    request = IBIntelligenceRequestItem(
        intelligence_run_id=run.id,
        deterministic_request_key="shortable-1",
        ticker="ACME",
        request_family="LIVE_MARKET_DATA",
        request_type="SHORTABLE",
        priority=20,
        status="COMPLETED",
        availability_status="AVAILABLE",
        request_json={},
        result_counts_json={"fields": 2},
        retry_count=0,
        started_at=observed,
        completed_at=observed,
    )
    evidence_db.add(request)
    o1, _ = persist_live_snapshot(
        evidence_db,
        _snapshot(observed, shares=1000),
        intelligence_run_id=run.id,
    )
    evidence_db.flush()
    f1, _ = persist_feature(
        evidence_db,
        ticker="ACME",
        ib_conid=1,
        as_of_session=date(2026, 9, 14),
        feature=_feature("SHORT_PRESSURE", o1.evidence_hash, score=6.0),
        config=config,
        calculated_at=observed,
        calculation_cutoff_at=observed,
        constituents=IbmiFeatureConstituents(
            live_observations=(o1,), availability_observations=(request,)
        ),
    )
    e1 = get_certified_ibmi_evidence(evidence_db, f1)
    frozen = deepcopy(e1.payload_json)
    o2, _ = persist_live_snapshot(
        evidence_db,
        _snapshot(observed + timedelta(minutes=5), shares=500),
        intelligence_run_id=run.id,
    )
    evidence_db.flush()
    manifest = e1.payload_json["constituent_manifest"]
    assert manifest["live_observations"][0]["id"] == o1.id
    assert manifest["availability_observations"][0]["id"] == request.id
    assert manifest["live_observations"][0]["id"] != o2.id
    assert e1.payload_json == frozen
    assert get_ibmi_evidence(evidence_db, e1.id).payload_json == frozen


def test_price_revision_does_not_reinterpret_feature(evidence_db: Session) -> None:
    config = load_ib_market_intelligence_config()
    observed = datetime(2026, 9, 14, 20, tzinfo=UTC)
    bar = PriceBar(
        ticker="PRICE",
        bar_date=date(2026, 9, 14),
        timeframe="1 day",
        open=100,
        high=102,
        low=99,
        close=101,
        volume=1000,
        source="IBKR",
        what_to_show="TRADES",
        created_at=observed,
        first_seen_at=observed,
        last_seen_at=observed,
        revision_count=0,
        data_hash="price-v1",
    )
    evidence_db.add(bar)
    evidence_db.flush()
    feature, _ = persist_feature(
        evidence_db,
        ticker="PRICE",
        ib_conid=2,
        as_of_session=date(2026, 9, 14),
        feature=_feature("LIQUIDITY", "d" * 64, score=7.0),
        config=config,
        calculated_at=observed,
        calculation_cutoff_at=observed,
        constituents=IbmiFeatureConstituents(
            price_bars=(bar,),
            price_roles=((bar.id, "PRICE_CLOSE"), (bar.id, "TRADE_VOLUME")),
            price_basis=(("price_basis", "TRADES"), ("volume_basis", "TRADES")),
        ),
    )
    evidence = get_certified_ibmi_evidence(evidence_db, feature)
    frozen = deepcopy(evidence.payload_json)
    evidence_db.add(
        PriceBarRevision(
            price_bar_id=bar.id,
            ticker=bar.ticker,
            bar_date=bar.bar_date,
            timeframe=bar.timeframe,
            what_to_show=bar.what_to_show,
            revision_number=1,
            previous_data_hash="price-v1",
            new_data_hash="price-v2",
            previous_values_json={"close": "101", "volume": "1000"},
            new_values_json={"close": "105", "volume": "1000"},
            source="IBKR",
            observed_at=observed + timedelta(hours=1),
        )
    )
    bar.close = Decimal("105")
    bar.data_hash = "price-v2"
    bar.revision_count = 1
    bar.revised_at = observed + timedelta(hours=1)
    evidence_db.flush()
    assert evidence.payload_json == frozen
    state = evidence.payload_json["constituent_manifest"]["price_series"]["states"][0]
    assert state["data_hash"] == "price-v1"
    assert state["state"]["close"] == 101


def test_ranking_edge_stays_on_original_ibmi_evidence(evidence_db: Session) -> None:
    observed = datetime(2026, 9, 14, 20, tzinfo=UTC)
    metric, _ = persist_historical_metric_bar(
        evidence_db, _metric(close=0.20), observed_at=observed
    )
    f1, _ = _persist_metric_feature(
        evidence_db, metric, score=7.0, cutoff=observed + timedelta(minutes=1)
    )
    e1 = get_certified_ibmi_evidence(evidence_db, f1)
    ranking = SimpleNamespace(
        run_id=1,
        ticker="ACME",
        ranking_profile="momentum_swing",
        evidence_id=None,
    )
    ranking_evidence = persist_core_evidence(
        evidence_db,
        kind=CoreEvidenceKind.RANKING,
        current_row=ranking,
        sources={"ibmi_liquidity": f1},
        payload={"score": 88},
        calculation_identity=build_ibmi_feature_identity(f1),
    )
    corrected, _ = persist_historical_metric_bar(
        evidence_db, _metric(close=0.40), observed_at=observed + timedelta(hours=1)
    )
    f2, _ = _persist_metric_feature(
        evidence_db,
        corrected,
        score=7.0,
        cutoff=observed + timedelta(hours=1, minutes=1),
    )
    assert f2.evidence_id != f1.evidence_id
    assert ranking_evidence is not None
    assert ranking_evidence.source_evidence_ids_json == {"ibmi_liquidity": e1.id}
    edge = evidence_db.scalar(
        select(CoreCalculationEvidenceSource).where(
            CoreCalculationEvidenceSource.evidence_id == ranking_evidence.id
        )
    )
    assert edge is not None and edge.source_evidence_id == e1.id


def test_legacy_lookup_and_winner_edge_are_rejected(evidence_db: Session) -> None:
    legacy = IBIntelligenceFeature(
        ticker="LEGACY",
        as_of_session=date(2026, 9, 14),
        calculated_at=datetime(2026, 9, 14, 20, tzinfo=UTC),
        module="LIQUIDITY",
        classification="GOOD",
        score=7,
        confidence="NORMAL",
        freshness_status="AVAILABLE",
        coverage_status="AVAILABLE",
        components_json={},
        reasons_json=[],
        warnings_json=[],
        source_evidence_hashes_json=[],
        source_version="legacy",
        calculation_version="legacy",
        config_hash="legacy",
        input_signature="legacy",
    )
    evidence_db.add(legacy)
    evidence_db.flush()
    with pytest.raises(EvidenceUnavailableError, match="LEGACY_CURRENT/LEGACY_UNKNOWN"):
        get_certified_ibmi_evidence(evidence_db, legacy)

    root = Path(__file__).resolve().parents[2] / "app" / "services" / "winner_probability"
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "IBIntelligenceFeature" not in source
        assert "CoreEvidenceKind.IBMI" not in source


def test_0078_upgrade_and_downgrade_compile_as_postgresql_ddl() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260915_0078_ibmi_immutable_constituent_evidence.py"
    )
    spec = spec_from_file_location("t11b3_migration", migration_path)
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
    assert "ALTER TABLE ib_intelligence_features ADD COLUMN evidence_id BIGINT" in ddl
    assert "IBMI" in ddl
    assert "fk_ib_intelligence_features_evidence" in ddl


def test_0078_disposable_postgresql_upgrade_and_downgrade(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    schema = inspect(engine)
    columns = {
        column["name"]: column
        for column in schema.get_columns("ib_intelligence_features")
    }
    assert columns["evidence_id"]["nullable"] is True
    command.downgrade(config, "0077_ceri_decision_evidence")
    assert "evidence_id" not in {
        column["name"]
        for column in inspect(engine).get_columns("ib_intelligence_features")
    }
    engine.dispose()


def _persist_metric_feature(
    db: Session,
    metric: IBHistoricalMetricBar,
    *,
    score: float,
    cutoff: datetime,
) -> tuple[IBIntelligenceFeature, bool]:
    return persist_feature(
        db,
        ticker=metric.ticker,
        ib_conid=metric.ib_conid,
        as_of_session=metric.effective_session,
        feature=_feature("LIQUIDITY", metric.data_hash, score=score),
        config=load_ib_market_intelligence_config(),
        calculated_at=cutoff,
        calculation_cutoff_at=cutoff,
        constituents=IbmiFeatureConstituents(metric_bars=(metric,)),
    )


def _feature(module: str, digest: str, *, score: float) -> FeatureResult:
    return FeatureResult(
        module=module,
        classification="TEST",
        score=score,
        confidence="NORMAL",
        freshness_status="AVAILABLE",
        coverage_status="AVAILABLE",
        components={"stable_score": score},
        evidence_hashes=(digest,),
    )


def _metric(*, close: float) -> HistoricalMetricBarDTO:
    return HistoricalMetricBarDTO(
        ticker="ACME",
        ib_conid=1,
        session_date=date(2026, 9, 14),
        timeframe="1 day",
        metric_type="BID_ASK",
        open_value=close,
        high_value=close,
        low_value=close,
        close_value=close,
        requested_range="60 D",
        source_semantic_type="BID_ASK_SPREAD_PERCENT",
    )


def _snapshot(observed_at: datetime, *, shares: int) -> LiveSnapshotDTO:
    return LiveSnapshotDTO(
        ticker="ACME",
        ib_conid=1,
        effective_session=date(2026, 9, 14),
        observed_at=observed_at,
        snapshot_type="SHORTABLE",
        values={"shortable_shares": shares, "shortable_state": "SHORTABLE"},
        availability_status="AVAILABLE",
        source_request={"generic_ticks": "236"},
    )
