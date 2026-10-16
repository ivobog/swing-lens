"""Run-37 regression: production combine must not depend on relationship load state."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest
from alembic.config import Config
from native_mutation_support import bound_pipeline, seed_price_frame
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import NO_VALUE
from test_core_effective_configuration import core_configurations
from test_fundamental_ranker_v2 import _quality_values
from test_technical_work import _synthetic_frame

from alembic import command
from app.models.tables import (
    CombinedResult,
    ConsumerEligibilityDecisionRecord,
    CoreCalculationEvidence,
    EligibilityShadowEvaluationRecord,
    FundamentalScore,
    RankingResult,
    RawCompanyRow,
    UploadRun,
)
from app.services import combined_decision, fundamental_score_service, technical_score_service
from app.services.combined_decision import refresh_combined_results
from app.services.contextual_consumer_eligibility import (
    CONTEXTUAL_ELIGIBILITY_KEY,
    FundamentalAdmissionEvidenceContractError,
    certify_fundamental_admission_orm_contract,
)
from app.services.core_effective_configuration import resolve_technical_configuration
from app.services.ranking_profile_service import refresh_ranking_profile
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.settings import FundamentalAdmissionMode, Settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def _config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _seed_persisted_sources(
    engine,
    *,
    run_id: int,
    ticker: str,
) -> dict[str, object]:
    configurations = list(core_configurations())
    technical_settings = Settings(
        _env_file=None,
        technical_v5_enabled=False,
        technical_v5_shadow_compare_enabled=False,
    )
    configurations[1] = resolve_technical_configuration(
        pine=load_pine_defaults(),
        v4=load_technical_scoring_v4_config(),
        v5=load_technical_scoring_v5_config(),
        settings=technical_settings,
    )
    with Session(engine) as db:
        db.add(UploadRun(id=run_id, filename=f"run37-regression-{run_id}.csv", status="COMPLETED"))
        db.flush()
        db.add(
            RawCompanyRow(
                run_id=run_id,
                row_number=1,
                ticker=ticker,
                company_name=f"{ticker} Corporation",
                sector="Technology",
                sector_canonical="Information Technology",
                raw_json={"Symbol": ticker, **_quality_values()},
            )
        )
        db.flush()
        cutoff, pipeline_run_id = bound_pipeline(
            db,
            run_id,
            configurations,
            cutoff_at=datetime(2026, 9, 16, 21, tzinfo=UTC),
        )
        frame = _synthetic_frame()
        frame["date"] = pd.bdate_range(end=cutoff.latest_completed_session, periods=len(frame))
        for symbol in (ticker, "SPY", "QQQ", "XLK"):
            seed_price_frame(db, symbol, frame, cutoff.cutoff_at)
        fundamental = fundamental_score_service.recalculate_run_fundamentals(
            db,
            run_id,
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_run_id,
            effective_configuration=configurations[0],
        )[0]
        technical = technical_score_service.score_run_technicals(
            db,
            run_id,
            tickers=[ticker],
            market_cutoff=cutoff,
            pipeline_run_id=pipeline_run_id,
            effective_configuration=configurations[1],
        )[0]
        pins = {
            ticker: {
                "fundamental": int(fundamental.evidence_id),
                "technical": int(technical.evidence_id),
            }
        }
        db.commit()
    return {
        "run_id": run_id,
        "ticker": ticker,
        "cutoff": cutoff,
        "pipeline_run_id": pipeline_run_id,
        "configuration": configurations[2],
        "ranking_configuration": configurations[3],
        "pins": pins,
    }


def _refresh_in_mode(
    db: Session,
    seeded: dict[str, object],
    *,
    mode: FundamentalAdmissionMode,
    monkeypatch: pytest.MonkeyPatch,
) -> CombinedResult:
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "app.settings.get_settings",
            lambda: SimpleNamespace(fundamental_admission_mode=mode),
        )
        result = refresh_combined_results(
            db,
            int(seeded["run_id"]),
            market_cutoff=seeded["cutoff"],
            pipeline_run_id=int(seeded["pipeline_run_id"]),
            effective_configuration=seeded["configuration"],
            source_evidence=seeded["pins"],
        )[0]
    db.commit()
    return result


def _count(db: Session, model, *, run_id: int) -> int:
    column = model.upload_run_id if hasattr(model, "upload_run_id") else model.run_id
    return int(db.scalar(select(func.count()).select_from(model).where(column == run_id)) or 0)


def test_run37_admission_loading_contract_and_mode_matrix_on_fresh_postgresql_sessions(
    disposable_postgres_database: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command.upgrade(_config(disposable_postgres_database), "head")
    engine = create_engine(disposable_postgres_database)
    try:
        active = _seed_persisted_sources(engine, run_id=701, ticker="ACME")

        # Deployment certification uses an ordinary fresh-session load and rolls back its probe.
        with monkeypatch.context() as scoped:
            scoped.setattr(
                "app.settings.get_settings",
                lambda: SimpleNamespace(
                    fundamental_admission_mode=FundamentalAdmissionMode.ACTIVE
                ),
            )
            with Session(engine) as db:
                certification = certify_fundamental_admission_orm_contract(
                    db,
                    expected_mode="ACTIVE",
                )
                assert certification["status"] == "PASSED"
                assert certification["relationship_initially_loaded"] is False
        with Session(engine) as db:
            assert _count(db, ConsumerEligibilityDecisionRecord, run_id=701) == 0

        # The deployment gate translates the original lazy='raise' signature into
        # the deterministic admission-contract failure used by retry policy.
        with monkeypatch.context() as scoped:
            scoped.setattr(
                "app.settings.get_settings",
                lambda: SimpleNamespace(
                    fundamental_admission_mode=FundamentalAdmissionMode.ACTIVE
                ),
            )
            scoped.setattr(
                "app.services.contextual_consumer_eligibility.record_fundamental_admission",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    InvalidRequestError(
                        "'FundamentalScore.calculation_evidence' is not available due to "
                        "lazy='raise'"
                    )
                ),
            )
            with Session(engine) as db, pytest.raises(
                FundamentalAdmissionEvidenceContractError,
                match="lazy='raise'",
            ):
                certify_fundamental_admission_orm_contract(db, expected_mode="ACTIVE")

        # The production exact-source query bulk-loads evidence after the initial unloaded read.
        statements: list[str] = []

        def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        with Session(engine, expire_on_commit=False) as db:
            unloaded = db.scalar(select(FundamentalScore).where(FundamentalScore.run_id == 701))
            assert inspect(unloaded).attrs.calculation_evidence.loaded_value is NO_VALUE
            event.listen(engine, "before_cursor_execute", capture_sql)
            try:
                active_result = _refresh_in_mode(
                    db,
                    active,
                    mode=FundamentalAdmissionMode.ACTIVE,
                    monkeypatch=monkeypatch,
                )
            finally:
                event.remove(engine, "before_cursor_execute", capture_sql)
            assert inspect(unloaded).attrs.calculation_evidence.loaded_value is not NO_VALUE
            active_result_id = active_result.id
            active_evidence_id = active_result.evidence_id
        assert (
            sum(
                "FROM fundamental_scores" in statement
                and "WHERE fundamental_scores.run_id" in statement
                for statement in statements
            )
            == 1
        )
        assert (
            sum(
                "FROM technical_scores" in statement
                and "WHERE technical_scores.run_id" in statement
                for statement in statements
            )
            == 1
        )

        with Session(engine) as db:
            combined = db.scalar(select(CombinedResult).where(CombinedResult.run_id == 701))
            evidence = db.get(CoreCalculationEvidence, combined.evidence_id)
            ledger = db.scalars(
                select(ConsumerEligibilityDecisionRecord).where(
                    ConsumerEligibilityDecisionRecord.upload_run_id == 701
                )
            ).one()
            assert combined.id == active_result_id
            assert combined.evidence_id == active_evidence_id
            assert evidence.source_evidence_ids_json == active["pins"]["ACME"]
            assert ledger.authoritative is True
            assert ledger.source_evidence_id == active["pins"]["ACME"]["fundamental"]
            assert ledger.source_identity_fingerprint == db.get(
                CoreCalculationEvidence,
                active["pins"]["ACME"]["fundamental"],
            ).calculation_identity_fingerprint
            assert combined.debug_json[CONTEXTUAL_ELIGIBILITY_KEY]["fundamental"][
                "fundamental_admission_mode"
            ] == "ACTIVE"
            assert _count(db, EligibilityShadowEvaluationRecord, run_id=701) == 0

        # Explicit eager loading and a repeated combine remain idempotent.
        with Session(engine) as db:
            eager = db.scalar(
                select(FundamentalScore)
                .options(selectinload(FundamentalScore.calculation_evidence))
                .where(FundamentalScore.run_id == 701)
            )
            assert inspect(eager).attrs.calculation_evidence.loaded_value is not NO_VALUE
            repeated = _refresh_in_mode(
                db,
                active,
                mode=FundamentalAdmissionMode.ACTIVE,
                monkeypatch=monkeypatch,
            )
            assert repeated.evidence_id == active_evidence_id
            stable_result_id = repeated.id
        with Session(engine) as db:
            assert _count(db, CombinedResult, run_id=701) == 1
            assert _count(db, ConsumerEligibilityDecisionRecord, run_id=701) == 1

        # A failure after serving-row replacement is rolled back with ledger state intact.
        with Session(engine) as db:
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    "app.settings.get_settings",
                    lambda: SimpleNamespace(
                        fundamental_admission_mode=FundamentalAdmissionMode.ACTIVE
                    ),
                )
                scoped.setattr(
                    combined_decision,
                    "persist_core_evidence",
                    lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        RuntimeError("injected combined-evidence persistence failure")
                    ),
                )
                with pytest.raises(RuntimeError, match="injected combined-evidence"):
                    refresh_combined_results(
                        db,
                        701,
                        market_cutoff=active["cutoff"],
                        pipeline_run_id=active["pipeline_run_id"],
                        effective_configuration=active["configuration"],
                        source_evidence=active["pins"],
                    )
        with Session(engine) as db:
            persisted = db.scalar(select(CombinedResult).where(CombinedResult.run_id == 701))
            assert (persisted.id, persisted.evidence_id) == (
                stable_result_id,
                active_evidence_id,
            )
            assert _count(db, ConsumerEligibilityDecisionRecord, run_id=701) == 1

        # The immediate ranking stage consumes the same exact producer identities.
        with Session(engine) as db, monkeypatch.context() as scoped:
            scoped.setattr(
                "app.settings.get_settings",
                lambda: SimpleNamespace(
                    fundamental_admission_mode=FundamentalAdmissionMode.ACTIVE
                ),
            )
            ranking = refresh_ranking_profile(
                db,
                701,
                "momentum_swing",
                market_cutoff=active["cutoff"],
                pipeline_run_id=active["pipeline_run_id"],
                effective_configuration=active["ranking_configuration"],
            )[0]
            db.commit()
            ranking_evidence_id = ranking.evidence_id
        with Session(engine) as db:
            ranking = db.scalar(select(RankingResult).where(RankingResult.run_id == 701))
            ranking_evidence = db.get(CoreCalculationEvidence, ranking_evidence_id)
            ledgers = list(
                db.scalars(
                    select(ConsumerEligibilityDecisionRecord).where(
                        ConsumerEligibilityDecisionRecord.upload_run_id == 701
                    )
                )
            )
            assert ranking.evidence_id == ranking_evidence_id
            assert ranking_evidence.source_evidence_ids_json == active["pins"]["ACME"]
            assert {row.consumer_edge for row in ledgers} == {
                "FUNDAMENTAL_TO_COMBINED",
                "FUNDAMENTAL_TO_RANKING",
            }
            assert all(row.authoritative for row in ledgers)

        shadow = _seed_persisted_sources(engine, run_id=702, ticker="SHDW")
        legacy = _seed_persisted_sources(engine, run_id=703, ticker="LGCY")
        for seeded, mode in (
            (shadow, FundamentalAdmissionMode.SHADOW),
            (legacy, FundamentalAdmissionMode.LEGACY),
        ):
            with Session(engine) as db:
                unloaded = db.scalar(
                    select(FundamentalScore).where(
                        FundamentalScore.run_id == seeded["run_id"]
                    )
                )
                assert inspect(unloaded).attrs.calculation_evidence.loaded_value is NO_VALUE
                _refresh_in_mode(db, seeded, mode=mode, monkeypatch=monkeypatch)

        with Session(engine) as db:
            shadow_result = db.scalar(select(CombinedResult).where(CombinedResult.run_id == 702))
            shadow_row = db.scalars(
                select(EligibilityShadowEvaluationRecord).where(
                    EligibilityShadowEvaluationRecord.upload_run_id == 702
                )
            ).one()
            assert _count(db, ConsumerEligibilityDecisionRecord, run_id=702) == 0
            assert shadow_row.proposed_decision_json["authoritative"] is False
            assert shadow_result.debug_json[CONTEXTUAL_ELIGIBILITY_KEY]["fundamental"][
                "fundamental_admission_mode"
            ] == "SHADOW"
            legacy_result = db.scalar(select(CombinedResult).where(CombinedResult.run_id == 703))
            assert _count(db, ConsumerEligibilityDecisionRecord, run_id=703) == 0
            assert _count(db, EligibilityShadowEvaluationRecord, run_id=703) == 0
            assert "fundamental_admission_mode" not in legacy_result.debug_json[
                CONTEXTUAL_ELIGIBILITY_KEY
            ]["fundamental"]

        # Missing, wrong-kind, and cross-run pointers all fail before any combined/ledger write.
        missing = _seed_persisted_sources(engine, run_id=704, ticker="MISS")
        wrong_kind = _seed_persisted_sources(engine, run_id=705, ticker="KIND")
        cross_run = _seed_persisted_sources(engine, run_id=706, ticker="CROSS")
        foreign = _seed_persisted_sources(engine, run_id=707, ticker="OTHER")
        missing["pins"]["MISS"]["fundamental"] = None
        with Session(engine) as db:
            score = db.scalar(select(FundamentalScore).where(FundamentalScore.run_id == 704))
            score.evidence_id = None
            wrong = db.scalar(select(FundamentalScore).where(FundamentalScore.run_id == 705))
            wrong.evidence_id = wrong_kind["pins"]["KIND"]["technical"]
            cross = db.scalar(select(FundamentalScore).where(FundamentalScore.run_id == 706))
            cross.evidence_id = foreign["pins"]["OTHER"]["fundamental"]
            db.commit()
        wrong_kind["pins"]["KIND"]["fundamental"] = wrong_kind["pins"]["KIND"]["technical"]
        cross_run["pins"]["CROSS"]["fundamental"] = foreign["pins"]["OTHER"]["fundamental"]

        for seeded, expected_fragment in (
            (missing, "exact-source address"),
            (wrong_kind, "producer mismatch"),
            (cross_run, "owner mismatch"),
        ):
            with Session(engine) as db, pytest.raises(
                FundamentalAdmissionEvidenceContractError,
                match=expected_fragment,
            ):
                _refresh_in_mode(
                    db,
                    seeded,
                    mode=FundamentalAdmissionMode.ACTIVE,
                    monkeypatch=monkeypatch,
                )
            with Session(engine) as db:
                run_id = int(seeded["run_id"])
                assert _count(db, CombinedResult, run_id=run_id) == 0
                assert _count(db, ConsumerEligibilityDecisionRecord, run_id=run_id) == 0
    finally:
        engine.dispose()
