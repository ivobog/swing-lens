from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    CombinedResult,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    FundamentalScore,
    RankingResult,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
    WinnerPredictionSnapshot,
    _reject_core_evidence_mutation,
)
from app.services.combined_ranking_identity import (
    build_combined_result_identity,
    build_fundamental_score_identity,
    build_ranking_result_identity,
    build_technical_score_identity,
    cohort_identity_fingerprint,
    embed_calculation_identity,
)
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    EvidenceUnavailableError,
    get_current_evidence,
    get_evidence_for_identity,
    persist_core_evidence,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.ranking_profile_config import get_ranking_profile

pytestmark = [pytest.mark.integration, pytest.mark.destructive]

SESSION = date(2026, 9, 15)


class FakeEvidenceDb:
    """Small repository double exercising the evidence service without infrastructure."""

    def __init__(self) -> None:
        self.rows: dict[type, list[object]] = {
            CoreCalculationEvidence: [],
            CoreCalculationEvidenceSource: [],
        }
        from app.models.tables import CoreCalculationCurrentProjection

        self.rows[CoreCalculationCurrentProjection] = []

    def add(self, row: object) -> None:
        bucket = self.rows.setdefault(type(row), [])
        if getattr(row, "id", None) is None:
            row.id = len(bucket) + 1
        if row not in bucket:
            bucket.append(row)

    def flush(self) -> None:
        return None

    def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params
        if entity is CoreCalculationEvidence and " JOIN " in str(statement):
            from app.models.tables import CoreCalculationCurrentProjection

            projection = self._first(CoreCalculationCurrentProjection, params)
            if projection is None:
                return None
            return next(
                (
                    row
                    for row in self.rows[CoreCalculationEvidence]
                    if row.id == projection.evidence_id
                ),
                None,
            )
        return self._first(entity, params)

    def scalars(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return self._matching(entity, statement.compile().params)

    def _first(self, entity: type, params: dict[str, object]):
        rows = self._matching(entity, params)
        return rows[0] if rows else None

    def _matching(self, entity: type, params: dict[str, object]) -> list[object]:
        result = list(self.rows.get(entity, []))
        for field in (
            "evidence_key",
            "artifact_kind",
            "run_id",
            "ticker",
            "ranking_profile_key",
            "ranking_profile",
            "calculation_identity_fingerprint",
            "payload_fingerprint",
            "evidence_id",
        ):
            value = next((value for key, value in params.items() if field in key), None)
            if value is not None and result and hasattr(result[0], field):
                result = [row for row in result if getattr(row, field) == value]
        return result


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_isolated_orm_guard_test(_type, _compiler, **_kwargs) -> str:
    return "JSON"


def test_0075_upgrade_and_downgrade_compile_as_postgresql_ddl() -> None:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260915_0075_core_immutable_evidence.py"
    )
    spec = spec_from_file_location("t11a_migration", migration_path)
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
    assert "CREATE TABLE core_calculation_evidence" in ddl
    assert "CREATE TABLE core_calculation_current_projections" in ddl
    assert "ALTER TABLE fundamental_scores ADD COLUMN evidence_id BIGINT" in ddl
    assert "DROP TABLE core_calculation_evidence" in ddl


def test_supported_orm_rejects_evidence_update_and_delete() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    UploadRun.__table__.create(engine)
    CoreCalculationEvidence.__table__.create(engine)
    calculated_at = datetime(2026, 9, 15, 20, tzinfo=UTC)
    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="guard.csv", status="COMPLETED"))
        evidence = CoreCalculationEvidence(
            id=1,
            artifact_kind="FUNDAMENTAL",
            run_id=1,
            ticker="ACME",
            calculation_identity_fingerprint="a" * 64,
            calculation_identity_json={"schema_version": "test"},
            payload_fingerprint="b" * 64,
            payload_json={"score": "8.2"},
            source_evidence_ids_json={},
            evidence_key="c" * 64,
            calculated_at=calculated_at,
        )
        db.add(evidence)
        db.commit()

        evidence.payload_json = {"score": "9.9"}
        with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
            db.flush()
        db.rollback()

        evidence = db.get(CoreCalculationEvidence, 1)
        db.delete(evidence)
        with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
            db.flush()
        db.rollback()
        assert db.get(CoreCalculationEvidence, 1).payload_json == {"score": "8.2"}
    engine.dispose()


def test_0075_schema_is_forward_safe_and_does_not_backfill_legacy_rows(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    schema = inspect(engine)

    assert {
        "core_calculation_evidence",
        "core_calculation_evidence_sources",
        "core_calculation_current_projections",
    } <= set(schema.get_table_names())
    for table_name in (
        "fundamental_scores",
        "technical_scores",
        "combined_results",
        "ranking_results",
    ):
        columns = {column["name"]: column for column in schema.get_columns(table_name)}
        assert columns["evidence_id"]["nullable"] is True

    engine.dispose()


def test_core_evidence_versions_pin_sources_move_projection_and_reject_mutation() -> None:
    db = FakeEvidenceDb()
    run_id = 7
    raw = RawCompanyRow(
        id=101,
        run_id=run_id,
        row_number=1,
        ticker="ACME",
        company_name="Acme",
        sector="Technology",
        raw_json={"Symbol": "ACME"},
    )

    first = _evidence_chain(db, run_id, raw, _cutoff(17, 20), pipeline_id=101)
    evidence_kinds = ("FUNDAMENTAL", "TECHNICAL", "COMBINED", "RANKING")
    original_payloads = {
        kind: deepcopy(first[kind].payload_json) for kind in evidence_kinds
    }
    winner_source_ids = {
        f"{kind.lower()}_evidence_id": first[kind].id for kind in evidence_kinds
    }
    winner = _winner(run_id, winner_source_ids)

    second = _evidence_chain(db, run_id, raw, _cutoff(18, 21), pipeline_id=102)

        # Families 1-3: all original evidence survives, and equal values under a
        # distinct calculation identity remain distinct evidence.
    for kind in evidence_kinds:
        assert first[kind].id != second[kind].id
        assert first[kind].payload_json == original_payloads[kind]
        assert (
            first[kind].calculation_identity_fingerprint
            != second[kind].calculation_identity_fingerprint
        )

        # Family 4: exact retry is deterministic reuse, not proliferation.
    retry = persist_core_evidence(
        db,
        kind=CoreEvidenceKind.FUNDAMENTAL,
        current_row=second["fundamental_row"],
    )
    assert retry.id == second["FUNDAMENTAL"].id

        # Family 5: historical identity remains stable while current moves.
    historical = get_evidence_for_identity(
        db,
        kind=CoreEvidenceKind.FUNDAMENTAL,
        calculation_identity=first["FUNDAMENTAL"].calculation_identity_fingerprint,
        ticker="ACME",
    )
    current = get_current_evidence(
        db,
        kind=CoreEvidenceKind.FUNDAMENTAL,
        run_id=run_id,
        ticker="ACME",
    )
    assert historical.id == first["FUNDAMENTAL"].id
    assert current.id == second["FUNDAMENTAL"].id

        # Family 6: an identity-less legacy current row is never promoted/fallback.
    legacy = FundamentalScore(run_id=run_id, ticker="LEGACY", debug_json={})
    assert (
        persist_core_evidence(db, kind=CoreEvidenceKind.FUNDAMENTAL, current_row=legacy)
        is None
    )
    with pytest.raises(EvidenceUnavailableError, match="EVIDENCE_UNAVAILABLE"):
        get_evidence_for_identity(
            db,
            kind=CoreEvidenceKind.FUNDAMENTAL,
            calculation_identity="legacy-current-has-no-evidence",
        )

        # Families 7-8: Combined and Ranking retain exact F1/T1 graph edges.
    for downstream in (first["COMBINED"], first["RANKING"]):
        links = [
            link
            for link in db.rows[CoreCalculationEvidenceSource]
            if link.evidence_id == downstream.id
        ]
        assert {link.source_role: link.source_evidence_id for link in links} == {
            "fundamental": first["FUNDAMENTAL"].id,
            "technical": first["TECHNICAL"].id,
        }

        # Family 9: Winner's frozen values/source evidence do not follow projections.
    assert winner.source_ids_json == winner_source_ids
    assert winner.fundamental_score == Decimal("8.2")
    assert winner.technical_score == Decimal("8.2")
    assert winner.combined_score == Decimal("8.2")

        # Family 11: readiness representation is preserved; no new gate is introduced.
    assert first["TECHNICAL"].payload_json["insufficient_data"] is True

    # Family 10: the ORM's supported update/delete hooks both fail closed.
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
        _reject_core_evidence_mutation(None, None, first["FUNDAMENTAL"])
    assert first["FUNDAMENTAL"].payload_json == original_payloads["FUNDAMENTAL"]


def _evidence_chain(
    db: Session,
    run_id: int,
    raw: RawCompanyRow,
    cutoff: MarketCalculationCutoff,
    *,
    pipeline_id: int,
) -> dict[str, object]:
    fundamental = FundamentalScore(
        id=200 + cutoff.context_id,
        run_id=run_id,
        ticker="ACME",
        fundamental_score=Decimal("8.2"),
        fundamental_label="Clean compounder",
        data_coverage_score=Decimal("9.5"),
        scoring_model_version="fundamentals_v2.1",
        debug_json={"config_hash": "a" * 64, "model_version": "fundamentals_v2.1"},
    )
    fundamental_identity = build_fundamental_score_identity(
        fundamental,
        raw_row=raw,
        market_cutoff=cutoff,
        pipeline_run_id=pipeline_id,
    )
    fundamental.debug_json = embed_calculation_identity(
        fundamental.debug_json, fundamental_identity, policy="T11A_TEST"
    )

    technical = TechnicalScore(
        id=300 + cutoff.context_id,
        run_id=run_id,
        ticker="ACME",
        calculation_context_id=cutoff.context_id,
        calculation_cutoff_at=cutoff.cutoff_at,
        input_as_of_session=cutoff.latest_completed_session,
        calendar_version=cutoff.calendar_version,
        dual_score=Decimal("8.2"),
        classification="Prime clean pullback",
        technical_confidence="low",
        technical_engine_version="5.0.0",
        insufficient_data=True,
        warning_flags_json=["insufficient_data"],
        debug_json={"feature_cache_identity": "feature-cache-fixed"},
    )
    technical_identity = build_technical_score_identity(
        technical,
        market_cutoff=cutoff,
        pipeline_run_id=pipeline_id,
        effective_config={"technical": "complete-test-config"},
    )
    technical.debug_json = embed_calculation_identity(
        technical.debug_json, technical_identity, policy="T11A_TEST"
    )

    fundamental_evidence = persist_core_evidence(
        db, kind=CoreEvidenceKind.FUNDAMENTAL, current_row=fundamental
    )
    technical_evidence = persist_core_evidence(
        db, kind=CoreEvidenceKind.TECHNICAL, current_row=technical
    )
    assert fundamental_evidence is not None and technical_evidence is not None

    cohort = cohort_identity_fingerprint((fundamental_identity, technical_identity))
    combined_identity = build_combined_result_identity(
        fundamental_identity=fundamental_identity,
        technical_identity=technical_identity,
        fundamental_score=fundamental,
        technical_score=technical,
        config={"combined_score": {"fundamental": 0.5, "technical": 0.5}},
        calculation_version="combined-test-v1",
        cohort_fingerprint=cohort,
    )
    combined = CombinedResult(
        id=400 + cutoff.context_id,
        run_id=run_id,
        ticker="ACME",
        final_score=Decimal("8.2"),
        combined_decision="Candidate",
        earnings_risk_level="LOW",
        is_complete=True,
        has_fundamental=True,
        has_technical=True,
        has_warning=False,
        debug_json=embed_calculation_identity({}, combined_identity, policy="T11A_TEST"),
    )
    combined_evidence = persist_core_evidence(
        db,
        kind=CoreEvidenceKind.COMBINED,
        current_row=combined,
        sources={"fundamental": fundamental, "technical": technical},
    )
    assert combined_evidence is not None

    profile = get_ranking_profile("quality_momentum")
    ranking_identity = build_ranking_result_identity(
        fundamental_identity=fundamental_identity,
        technical_identity=technical_identity,
        fundamental_score=fundamental,
        technical_score=technical,
        profile=profile,
        global_config={"earnings_risk_gate": {}},
        calculation_version="ranking-test-v1",
        cohort_fingerprint=cohort,
        liquidity_feature=None,
        liquidity_identity=None,
    )
    ranking = RankingResult(
        id=500 + cutoff.context_id,
        run_id=run_id,
        ticker="ACME",
        ranking_profile=profile.name,
        ranking_label=profile.label,
        profile_rank=1,
        profile_score=Decimal("8.2"),
        decision_label="Candidate",
        debug_json=embed_calculation_identity({}, ranking_identity, policy="T11A_TEST"),
    )
    ranking_evidence = persist_core_evidence(
        db,
        kind=CoreEvidenceKind.RANKING,
        current_row=ranking,
        sources={"fundamental": fundamental, "technical": technical},
    )
    assert ranking_evidence is not None
    return {
        "FUNDAMENTAL": fundamental_evidence,
        "TECHNICAL": technical_evidence,
        "COMBINED": combined_evidence,
        "RANKING": ranking_evidence,
        "fundamental_row": fundamental,
    }


def _winner(run_id: int, source_ids: dict[str, int]) -> WinnerPredictionSnapshot:
    cutoff = datetime(2026, 9, 15, 20, tzinfo=UTC)
    return WinnerPredictionSnapshot(
        run_id=run_id,
        ticker="ACME",
        prediction_as_of_date=SESSION,
        source_data_cutoff_at=cutoff,
        decision_at=cutoff,
        captured_at=cutoff,
        entry_schedule_status="READY",
        entry_data_status="READY",
        eligibility_status="ELIGIBLE",
        fundamental_score=Decimal("8.2"),
        technical_score=Decimal("8.2"),
        combined_score=Decimal("8.2"),
        feature_schema_version="winner-test-v1",
        feature_vector_hash="b" * 64,
        config_hash="c" * 64,
        calculation_version="winner-test-v1",
        feature_json={"frozen": True},
        source_ids_json=source_ids,
        warning_flags_json=[],
        lineage_json={"core_evidence": source_ids},
    )


def _cutoff(context_id: int, hour: int) -> MarketCalculationCutoff:
    return MarketCalculationCutoff(
        cutoff_at=datetime(2026, 9, 15, hour, tzinfo=UTC),
        exchange_timezone="America/New_York",
        latest_completed_session=SESSION,
        daily_bar_ready_at=datetime(2026, 9, 15, 20, tzinfo=UTC),
        calendar_version="XNYS-2026a",
        bar_readiness_version="daily-close-v1",
        cutoff_reason="T11A_TEST",
        context_id=context_id,
    )
