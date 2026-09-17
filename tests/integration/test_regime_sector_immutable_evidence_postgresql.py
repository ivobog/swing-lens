from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
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
from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    MarketRegimeSnapshot,
    RankingResult,
    SectorRotationRow,
    SectorRotationSnapshot,
    UploadRun,
)
from app.services.contextual_calculation_identity import (
    build_contextual_result_identity,
    consumer_context_identity,
    embed_identity,
)
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    get_current_evidence,
    get_evidence_for_identity,
    persist_core_evidence,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.market_regime_repository import (
    MarketRegimeRepository,
    MarketRegimeSnapshotWrite,
)
from app.services.sector_rotation_repository import (
    SectorRotationRepository,
    SectorRotationRowWrite,
    SectorRotationSnapshotWrite,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_contextual_evidence_test(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint_for_contextual_evidence_test(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@pytest.fixture
def evidence_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        UploadRun.__table__,
        CoreCalculationEvidence.__table__,
        CoreCalculationEvidenceSource.__table__,
        CoreCalculationCurrentProjection.__table__,
        MarketRegimeSnapshot.__table__,
        SectorRotationSnapshot.__table__,
        SectorRotationRow.__table__,
    ):
        table.create(engine)
    with Session(engine) as db:
        db.add(UploadRun(id=7, filename="context.csv", status="COMPLETED"))
        db.commit()
        yield db
        db.rollback()
    engine.dispose()


def test_0076_upgrade_and_downgrade_compile_as_postgresql_ddl() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260915_0076_regime_sector_immutable_evidence.py"
    )
    spec = spec_from_file_location("t11b1_migration", migration_path)
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
    assert "ALTER TABLE market_regime_snapshots ADD COLUMN evidence_id BIGINT" in ddl
    assert "ALTER TABLE sector_rotation_snapshots ADD COLUMN evidence_id BIGINT" in ddl
    assert "REGIME" in ddl and "SECTOR" in ddl
    assert "WHERE run_id IS NULL AND ticker IS NULL" in ddl


def test_0076_disposable_postgresql_upgrade_and_downgrade(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    schema = inspect(engine)
    for table_name in ("market_regime_snapshots", "sector_rotation_snapshots"):
        columns = {column["name"]: column for column in schema.get_columns(table_name)}
        assert columns["evidence_id"]["nullable"] is True
    evidence_columns = {
        column["name"]: column
        for column in schema.get_columns("core_calculation_evidence")
    }
    assert evidence_columns["run_id"]["nullable"] is True
    assert evidence_columns["ticker"]["nullable"] is True

    command.downgrade(config, "0075_core_immutable_evidence")
    schema = inspect(engine)
    for table_name in ("market_regime_snapshots", "sector_rotation_snapshots"):
        assert "evidence_id" not in {
            column["name"] for column in schema.get_columns(table_name)
        }
    engine.dispose()


def test_regime_versions_retry_projection_history_and_legacy(evidence_db: Session) -> None:
    repository = MarketRegimeRepository()
    cutoff_1 = _cutoff(date(2026, 9, 14), 20)
    cutoff_2 = _cutoff(date(2026, 9, 15), 21)
    identity_1 = _contextual_identity(cutoff_1, None, "market-regime")
    identity_2 = _contextual_identity(cutoff_2, None, "market-regime")

    row_1 = repository.upsert_snapshot(
        evidence_db, _regime_write(cutoff_1, identity_1), run_id=None
    )
    assert row_1.evidence_id is not None
    immutable_1 = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.REGIME,
        calculation_identity=identity_1,
    )
    original_payload = deepcopy(immutable_1.payload_json)

    retried = repository.upsert_snapshot(
        evidence_db, _regime_write(cutoff_1, identity_1), run_id=None
    )
    assert retried.id == row_1.id
    assert retried.evidence_id == immutable_1.id

    row_2 = repository.upsert_snapshot(
        evidence_db, _regime_write(cutoff_2, identity_2), run_id=None
    )
    current = get_current_evidence(
        evidence_db,
        kind=CoreEvidenceKind.REGIME,
        run_id=None,
        ticker=None,
    )
    assert current.id == row_2.evidence_id
    assert immutable_1.payload_json == original_payload
    assert immutable_1.id != row_2.evidence_id
    assert get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.REGIME,
        calculation_identity=identity_1,
    ).id == immutable_1.id

    legacy = MarketRegimeSnapshot(debug_json={})
    assert (
        persist_core_evidence(
            evidence_db,
            kind=CoreEvidenceKind.REGIME,
            current_row=legacy,
            scope_ticker=None,
        )
        is None
    )
    with pytest.raises(EvidenceUnavailableError, match="EVIDENCE_UNAVAILABLE"):
        get_evidence_for_identity(
            evidence_db,
            kind=CoreEvidenceKind.REGIME,
            calculation_identity="legacy-regime-current",
        )


def test_sector_pins_ranking_regime_prior_and_survives_newer_evidence(
    evidence_db: Session,
) -> None:
    regime_repository = MarketRegimeRepository()
    sector_repository = SectorRotationRepository()
    cutoff_0 = _cutoff(date(2026, 9, 11), 19)
    cutoff_1 = _cutoff(date(2026, 9, 14), 20)
    cutoff_2 = _cutoff(date(2026, 9, 15), 21)

    regime_1_identity = _contextual_identity(cutoff_1, None, "market-regime")
    regime_1 = regime_repository.upsert_snapshot(
        evidence_db, _regime_write(cutoff_1, regime_1_identity), run_id=None
    )
    ranking_1, ranking_1_identity = _ranking(evidence_db, cutoff_1, score=8)

    sector_0_identity = _contextual_identity(cutoff_0, 7, "sector-rotation")
    sector_0 = sector_repository.save_snapshot(
        evidence_db,
        _sector_write(cutoff_0, sector_0_identity, previous_rank=None),
        evidence_sources={"ranking:000001": ranking_1, "regime": regime_1},
    )
    sector_1_identity = _contextual_identity(cutoff_1, 7, "sector-rotation")
    sector_1 = sector_repository.save_snapshot(
        evidence_db,
        _sector_write(cutoff_1, sector_1_identity, previous_rank=2),
        evidence_sources={
            "ranking:000001": ranking_1,
            "regime": regime_1,
            "prior_sector": sector_0,
        },
    )
    sector_1_evidence = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.SECTOR,
        calculation_identity=sector_1_identity,
    )
    original_payload = deepcopy(sector_1_evidence.payload_json)
    original_sources = dict(sector_1_evidence.source_evidence_ids_json)
    assert original_sources == {
        "prior_sector": sector_0.evidence_id,
        "ranking:000001": ranking_1.evidence_id,
        "regime": regime_1.evidence_id,
    }

    retried = sector_repository.save_snapshot(
        evidence_db,
        _sector_write(cutoff_1, sector_1_identity, previous_rank=2),
        evidence_sources={
            "ranking:000001": ranking_1,
            "regime": regime_1,
            "prior_sector": sector_0,
        },
    )
    assert retried.id == sector_1.id
    assert retried.evidence_id == sector_1_evidence.id

    regime_2 = regime_repository.upsert_snapshot(
        evidence_db,
        _regime_write(
            cutoff_2,
            _contextual_identity(cutoff_2, None, "market-regime"),
        ),
        run_id=None,
    )
    ranking_2, ranking_2_identity = _ranking(evidence_db, cutoff_2, score=8)
    sector_2_identity = _contextual_identity(cutoff_2, 7, "sector-rotation")
    sector_2 = sector_repository.save_snapshot(
        evidence_db,
        _sector_write(cutoff_2, sector_2_identity, previous_rank=1),
        evidence_sources={
            "ranking:000001": ranking_2,
            "regime": regime_2,
            "prior_sector": sector_1,
        },
    )

    evidence_db.expire_all()
    historical = get_evidence_for_identity(
        evidence_db,
        kind=CoreEvidenceKind.SECTOR,
        calculation_identity=sector_1_identity,
    )
    current = get_current_evidence(
        evidence_db,
        kind=CoreEvidenceKind.SECTOR,
        run_id=7,
        ticker=None,
        ranking_profile="universe_only",
    )
    assert historical.payload_json == original_payload
    assert historical.source_evidence_ids_json == original_sources
    assert current.id == sector_2.evidence_id
    assert ranking_1_identity.fingerprint() != ranking_2_identity.fingerprint()


def test_sector_legacy_and_missing_immutable_sources_fail_closed(
    evidence_db: Session,
) -> None:
    cutoff = _cutoff(date(2026, 9, 15), 21)
    identity = _contextual_identity(cutoff, 7, "sector-rotation")
    repository = SectorRotationRepository()
    with pytest.raises(EvidenceUnavailableError, match="immutable source set"):
        repository.save_snapshot(
            evidence_db,
            _sector_write(cutoff, identity, previous_rank=None),
        )
    evidence_db.rollback()

    legacy = SectorRotationSnapshot(debug_json={})
    assert (
        persist_core_evidence(
            evidence_db,
            kind=CoreEvidenceKind.SECTOR,
            current_row=legacy,
            scope_ticker=None,
            scope_profile="universe_only",
        )
        is None
    )
    with pytest.raises(EvidenceUnavailableError, match="EVIDENCE_UNAVAILABLE"):
        get_evidence_for_identity(
            evidence_db,
            kind=CoreEvidenceKind.SECTOR,
            calculation_identity="legacy-sector-current",
        )


def _ranking(
    db: Session,
    cutoff: MarketCalculationCutoff,
    *,
    score: int,
) -> tuple[RankingResult, object]:
    identity = _contextual_identity(cutoff, 7, "ranking")
    row = RankingResult(
        run_id=7,
        ticker="ACME",
        ranking_profile="quality_momentum",
        ranking_label="Quality Momentum",
        profile_rank=1,
        profile_score=score,
        decision_label="Candidate",
        debug_json=embed_identity({}, identity, policy="T11B1_TEST"),
    )
    evidence = persist_core_evidence(db, kind=CoreEvidenceKind.RANKING, current_row=row)
    assert evidence is not None
    return row, identity


def _contextual_identity(
    cutoff: MarketCalculationCutoff,
    run_id: int | None,
    namespace: str,
):
    base = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=run_id,
        pipeline_id=None,
        globally_reusable=run_id is None,
    )
    return build_contextual_result_identity(
        base=base,
        namespace=namespace,
        config_hash="a" * 64,
        calculation_version=f"{namespace}-1",
        engine_version=f"{namespace}-1",
        source_artifacts=[],
        source_payload={"session": cutoff.latest_completed_session.isoformat()},
    )


def _regime_write(
    cutoff: MarketCalculationCutoff,
    identity,
) -> MarketRegimeSnapshotWrite:
    return MarketRegimeSnapshotWrite(
        as_of_date=cutoff.latest_completed_session,
        calculation_version="market-regime-1",
        config_version="config-1",
        regime="RISK_ON",
        risk_state="NORMAL",
        score=8.0,
        risk_off=False,
        gate_ok=True,
        confidence="normal",
        action_summary="Constructive",
        position_size_multiplier=1.0,
        warnings=["test-warning"],
        debug=embed_identity(
            {
                "temporal_lineage": {
                    "calculation_cutoff_at": cutoff.cutoff_at.isoformat(),
                    "input_as_of_session": cutoff.latest_completed_session.isoformat(),
                    "calendar_version": cutoff.calendar_version,
                }
            },
            identity,
            policy="T11B1_TEST",
        ),
    )


def _sector_write(
    cutoff: MarketCalculationCutoff,
    identity,
    *,
    previous_rank: int | None,
) -> SectorRotationSnapshotWrite:
    return SectorRotationSnapshotWrite(
        run_id=7,
        as_of_date=cutoff.latest_completed_session,
        calculation_version="sector-rotation-1",
        config_version="config-1",
        config_hash="a" * 64,
        mode="universe_only",
        default_ranking_profile="quality_momentum",
        sector_count=1,
        ticker_count=1,
        leading_sector="Technology",
        summary={"leading_sector": "Technology", "sector_count": 1},
        warning_flags=["test-warning"],
        debug=embed_identity(
            {
                "temporal_lineage": {
                    "calculation_cutoff_at": cutoff.cutoff_at.isoformat(),
                    "input_as_of_session": cutoff.latest_completed_session.isoformat(),
                    "calendar_version": cutoff.calendar_version,
                }
            },
            identity,
            policy="T11B1_TEST",
        ),
        rows=[
            SectorRotationRowWrite(
                sector="Technology",
                sector_slug="technology",
                rotation_state="LEADING",
                sector_permission="ALLOW",
                confidence="normal",
                current_rank=1,
                previous_rank=previous_rank,
                sector_final_score=8.0,
                warning_flags=["test-warning"],
            )
        ],
    )


def _cutoff(session: date, hour: int) -> MarketCalculationCutoff:
    return MarketCalculationCutoff(
        cutoff_at=datetime.combine(session, datetime.min.time(), tzinfo=UTC).replace(hour=hour),
        exchange_timezone="America/New_York",
        latest_completed_session=session,
        daily_bar_ready_at=datetime.combine(
            session, datetime.min.time(), tzinfo=UTC
        ).replace(hour=20),
        calendar_version="XNYS-2026a",
        bar_readiness_version="daily-close-v1",
        cutoff_reason="T11B1_TEST",
        context_id=None,
    )
