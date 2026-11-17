"""A new full-universe admission publishes unavailable-source diagnostics safely."""

from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from alembic.config import Config
from native_mutation_support import seed_price_frame
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_fundamental_ranker_v2 import _quality_values
from test_technical_work import _synthetic_frame

from app.database_safety import run_guarded_alembic_upgrade
from app.models.tables import (
    CoreCalculationEvidence,
    MarketDataSessionDisposition,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services import combined_decision, fundamental_score_service, technical_score_service
from app.services.configuration_delivery import (
    binding_reference,
    configuration_delivery_scope,
    load_configuration_delivery,
)
from app.services.core_effective_configuration import CoreEffectiveConfiguration
from app.services.market_calculation_context_service import market_context_for_pipeline
from app.services.market_data_availability_policy import pipeline_availability_policy
from app.services.market_data_session_readiness import classify_session_readiness
from app.services.pipeline_service import start_pipeline
from app.services.transition_preflight_plan_service import _pipeline_downstream_tickers
from app.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_new_admission_keeps_unavailable_and_history_gapped_rows_without_certifying_technicals(
    disposable_postgres_database,
    monkeypatch,
):
    run_guarded_alembic_upgrade(Config("alembic.ini"), disposable_postgres_database)
    settings = Settings(
        _env_file=None,
        database_url=disposable_postgres_database,
        ceri_enabled=False,
        ceri_run_capture_enabled=False,
        ceri_provider_ingest_enabled=False,
        setup_lifecycle_pipeline_step_enabled=False,
        winner_probability_capture_in_pipeline=False,
        market_data_prewarm_enabled=False,
        technical_v5_enabled=False,
        technical_v5_shadow_compare_enabled=False,
        technical_process_pool_enabled=False,
        technical_artifact_cache_writes_enabled=False,
    )
    for target in (
        "app.settings.get_settings",
        "app.services.pipeline_service.get_settings",
        "app.services.technical_score_service.get_settings",
    ):
        monkeypatch.setattr(target, lambda: settings)
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            upload = UploadRun(filename="availability.csv", status="COMPLETED", row_count=3)
            db.add(upload)
            db.flush()
            db.add_all(
                [
                    RawCompanyRow(
                        run_id=upload.id,
                        row_number=index,
                        ticker=ticker,
                        sector="Technology",
                        sector_canonical="Information Technology",
                        raw_json={"Symbol": ticker, **_quality_values()},
                    )
                    for index, ticker in enumerate(("CLBK", "HLT", "LIVE"), 1)
                ]
            )
            db.flush()
            pipeline = start_pipeline(db, upload.id)
            pipeline.status = "RUNNING"
            db.commit()
            cutoff = market_context_for_pipeline(db, pipeline)
            delivery = load_configuration_delivery(
                db, binding_reference(db, pipeline_run_id=pipeline.id)
            )
            with configuration_delivery_scope(delivery):
                assert pipeline_availability_policy(pipeline).allow_history_gaps
                assert (
                    delivery.configurations["execution.market_data"].values
                    == (pipeline.result_json["market_data_availability_policy"])
                )
                frame = _synthetic_frame()
                frame["date"] = pd.bdate_range(
                    end=cutoff.latest_completed_session, periods=len(frame)
                )
                gap_session = frame.iloc[-4]["date"].date()
                for ticker in ("LIVE", "SPY", "QQQ", "XLK"):
                    seed_price_frame(db, ticker, frame, cutoff.cutoff_at)
                gap_frame = frame[frame["date"].dt.date != gap_session]
                seed_price_frame(db, "HLT", gap_frame, cutoff.cutoff_at)
                for ticker in ("CLBK", "HLT", "LIVE"):
                    failure = SimpleNamespace(
                        id=1,
                        what_to_show="TRADES",
                        status="FAILED",
                        error_message="IB no data",
                        decision_metadata_json={
                            "provider_error_category": "HISTORICAL_DATA_UNAVAILABLE",
                            "provider_result": "HISTORICAL_DATA_UNAVAILABLE",
                            "retryable": False,
                        },
                    )
                    values = classify_session_readiness(
                        ticker=ticker,
                        expected_session=cutoff.latest_completed_session,
                        latest_bar_session=None
                        if ticker == "CLBK"
                        else cutoff.latest_completed_session,
                        lifecycle=None,
                        fetch_items=(failure,) if ticker == "CLBK" else (),
                        enforce_response_integrity=True,
                    )
                    db.add(
                        MarketDataSessionDisposition(
                            pipeline_run_id=pipeline.id,
                            upload_run_id=upload.id,
                            market_calculation_context_id=cutoff.context_id,
                            ticker=ticker,
                            expected_session=cutoff.latest_completed_session,
                            latest_bar_session=None
                            if ticker == "CLBK"
                            else cutoff.latest_completed_session,
                            **values,
                        )
                    )
                db.commit()
                assert _pipeline_downstream_tickers(db, pipeline_run_id=pipeline.id) == {
                    "CLBK",
                    "HLT",
                    "LIVE",
                }
                # Even a stale numeric result is replaced before publication and ranking.
                attempted = TechnicalScore(
                    run_id=upload.id, ticker="CLBK", dual_score=Decimal("99")
                )
                masked = technical_score_service._availability_score_results(
                    db,
                    upload.id,
                    pipeline.id,
                    [attempted],
                    v4_params={},
                )[0]
                assert masked.dual_score is None and masked.insufficient_data
                args = dict(market_cutoff=cutoff, pipeline_run_id=pipeline.id)
                fundamentals = fundamental_score_service.recalculate_run_fundamentals(
                    db,
                    upload.id,
                    **args,
                    effective_configuration=CoreEffectiveConfiguration(
                        delivery.configurations["core.fundamental"].snapshot
                    ),
                )
                fundamental_ids = {row.ticker: row.evidence_id for row in fundamentals}
                technicals = technical_score_service.score_run_technicals(
                    db,
                    upload.id,
                    **args,
                    frozen_tickers=("CLBK", "HLT", "LIVE"),
                    effective_configuration=CoreEffectiveConfiguration(
                        delivery.configurations["core.technical"].snapshot
                    ),
                )
                technical = {row.ticker: row for row in technicals}
                assert technical["CLBK"].dual_score is None
                assert technical["CLBK"].missing_data_json["market_data_unavailable"] is True
                assert technical["HLT"].insufficient_data is True
                assert technical["HLT"].evidence_id is not None
                combined = combined_decision.refresh_combined_results(
                    db,
                    upload.id,
                    **args,
                    effective_configuration=CoreEffectiveConfiguration(
                        delivery.configurations["core.combined"].snapshot
                    ),
                    source_evidence={
                        row.ticker: {
                            "fundamental": fundamental_ids[row.ticker],
                            "technical": row.evidence_id,
                        }
                        for row in technicals
                    },
                )
                assert {row.ticker for row in combined} == {"CLBK", "HLT", "LIVE"}
                for row in combined:
                    if row.ticker in {"CLBK", "HLT"}:
                        decision = row.debug_json["technical_consumer_eligibility"]["decision"]
                        assert decision["status"] == "INELIGIBLE"
                        readiness = row.debug_json["technical_consumer_eligibility"][
                            "producer_readiness"
                        ]
                        assert "TECH_INSUFFICIENT_HISTORY" in readiness["blocking_reasons"]
                for row in fundamentals:
                    assert row.evidence_id == fundamental_ids[row.ticker]
                assert (
                    len(
                        list(
                            db.scalars(
                                select(CoreCalculationEvidence).where(
                                    CoreCalculationEvidence.id.in_(
                                        [row.evidence_id for row in technicals]
                                    )
                                )
                            )
                        )
                    )
                    == 3
                )
                db.commit()
    finally:
        engine.dispose()
