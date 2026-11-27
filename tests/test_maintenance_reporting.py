from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alembic import command
from app.models.ceri_tables import CeriScoreSnapshot
from app.models.tables import (
    PipelineRun,
    TransitionDecisionHandoffManifest,
    UploadRun,
    WinnerAcquisitionDecisionRecord,
)
from app.services import market_calculation_context_service
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri import score_population
from app.services.ceri.evidence_state_service import CeriEvidenceLedgerService
from app.services.market_clock_service import MarketClockService
from app.services.pipeline_executor import _validate_ceri_completion_barrier
from app.services.winner_probability import capture_service
from app.services.winner_probability.calculation_identity import WINNER_HANDOFF_CONTRACT
from app.services.winner_probability.consumer_eligibility import WinnerSourceEligibilityError


def test_certified_ceri_continuation_reports_snapshot_and_unrated_totals(monkeypatch):
    pipeline = PipelineRun(
        id=70,
        upload_run_id=66,
        result_json={
            "ceri_completion_state": "CERTIFIED",
            "ceri_provider_workflow_key": "ceri:pipeline:70:reporting-test",
            "ceri_calculation_context_id": 123,
            "ceri_certified_score_ids": [1, 2, 3],
            "ceri_score_snapshots": 0,
            "ceri_unrated": 0,
        },
    )
    scores = [
        CeriScoreSnapshot(id=1, ticker="AAA", opportunity_score=Decimal("0")),
        CeriScoreSnapshot(id=2, ticker="BBB", opportunity_score=Decimal("73.5")),
        CeriScoreSnapshot(id=3, ticker="CCC", opportunity_score=None),
    ]

    def context(db, *, calculation_context_id, upload_run_id, pipeline_run_id):
        assert (calculation_context_id, upload_run_id, pipeline_run_id) == (123, 66, 70)
        return SimpleNamespace(context_id=123)

    def certified(db, pipeline_id, context_id, *, expected_ids):
        assert (pipeline_id, context_id, expected_ids) == (70, 123, [1, 2, 3])
        return scores

    monkeypatch.setattr(
        market_calculation_context_service, "resolve_pipeline_ceri_context", context
    )
    monkeypatch.setattr(score_population, "certified_scores", certified)
    with Session() as db:
        result = _validate_ceri_completion_barrier(db, pipeline, tickers=["aaa", "BBB", "CCC"])
        assert result == {"ceri_score_snapshots": 3, "ceri_unrated": 1}
        with pytest.raises(ValueError, match="CERI_CERTIFIED_CAPTURE_INCOMPLETE"):
            _validate_ceri_completion_barrier(db, pipeline, tickers=["AAA", "BBB"])


def test_catalyst_diagnostics_distinguish_persisted_and_considered_events():
    service = CeriEvidenceLedgerService()
    lineage = {
        "catalyst_event_ids": [10, 11, 12, 13],
        "catalyst_considered_event_ids": [10, 11, 12],
        "catalyst_selected_event_ids": [10],
        "catalyst_rejected": [{"event_id": 11, "reason": "ISSUER_MISMATCH"}],
    }
    evidence = service.enrich(
        lineage, source_ids=[], opportunity_selected_ids=[], risk_selected_ids=[10]
    )
    rows = {row["evidence_id"]: row for row in evidence["evidence_states"]}
    assert rows[10]["states"] == [
        "PERSISTED",
        "CONSIDERED",
        "ACCEPTED",
        "SELECTED_FOR_COMPONENT",
        "SCORED",
    ]
    assert rows[11]["states"] == ["PERSISTED", "CONSIDERED", "REJECTED"]
    assert rows[11]["reason"] == "ISSUER_MISMATCH"
    assert rows[12]["states"] == ["PERSISTED", "CONSIDERED", "ACCEPTED"]
    assert rows[13]["states"] == ["PERSISTED"]
    assert evidence["evidence_counts"]["ACCEPTED"] == 2

    empty = service.enrich(
        {"catalyst_event_ids": [13], "catalyst_considered_event_ids": []},
        source_ids=[],
        opportunity_selected_ids=[],
        risk_selected_ids=[],
    )
    assert empty["evidence_states"][0]["states"] == ["PERSISTED"]


def test_early_winner_exclusion_persists_validated_pipeline_identity(
    disposable_postgres_database, monkeypatch
):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    config.attributes["database_safety_context"] = "DISPOSABLE_TEST"
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine, expire_on_commit=False) as db:
            upload = UploadRun(filename="maintenance-report.csv", status="COMPLETED", row_count=1)
            db.add(upload)
            db.flush()
            pipeline = PipelineRun(upload_run_id=upload.id, status="RUNNING")
            db.add(pipeline)
            db.commit()

            cutoff = replace(
                MarketClockService().cutoff_for(
                    datetime(2026, 11, 25, 22, tzinfo=UTC), reason="MAINTENANCE_REPORTING_TEST"
                ),
                context_id=123,
            )
            anchor = "maintenance-reporting-anchor"
            payload = CanonicalEvidenceSerializer.canonicalize(
                {
                    "contract": WINNER_HANDOFF_CONTRACT,
                    "binding": {"run_start_anchor_fingerprint": anchor},
                    "run": {"upload_run_id": upload.id, "pipeline_run_id": pipeline.id},
                    "market_context": {**asdict(cutoff), "id": cutoff.context_id},
                }
            )
            handoff = TransitionDecisionHandoffManifest(
                pipeline_run_id=pipeline.id,
                upload_run_id=upload.id,
                market_calculation_context_id=cutoff.context_id,
                run_start_anchor_fingerprint=anchor,
                manifest_json=payload,
                manifest_fingerprint=CanonicalEvidenceSerializer.fingerprint(payload),
            )
            context = SimpleNamespace(
                decision_handoff_manifest=handoff,
                tickers=[SimpleNamespace(raw_row=SimpleNamespace(ticker="AAA", id=None))],
            )
            repository = SimpleNamespace(
                load_run_context=lambda *_args, **_kwargs: context,
                get_outcome_definition=lambda *_args, **_kwargs: None,
            )

            def reject_source(*_args, **_kwargs):
                decisions = {
                    "technical": {
                        "requirement": "MANDATORY",
                        "included": False,
                        "producer_readiness": {"blocking_reasons": ["TECH_INSUFFICIENT_HISTORY"]},
                    }
                }
                raise WinnerSourceEligibilityError(
                    SimpleNamespace(canonical_payload=lambda: decisions)
                )

            monkeypatch.setattr(capture_service, "acquire_winner_sources", reject_source)
            service = capture_service.WinnerPredictionCaptureService(repository=repository)
            result = service.capture_run(db, run_id=upload.id, market_cutoff=cutoff)
            assert (result.excluded, result.inserted, result.failed) == (1, 0, 0)
            record = db.scalars(select(WinnerAcquisitionDecisionRecord)).one()
            assert record.upload_run_id == upload.id
            assert record.pipeline_run_id == pipeline.id
            assert record.ticker == "AAA"
            assert record.acquisition_outcome == "EXCLUDED"
            assert record.eligibility_reason == "insufficient_completed_bars"
            assert record.prediction_id is None
    finally:
        engine.dispose()
