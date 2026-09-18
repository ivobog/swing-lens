"""Actual native financial capture fixtures, without authority replacements."""

from dataclasses import replace
from datetime import UTC, datetime

from native_mutation_support import seed_native_core
from sqlalchemy import select
from test_t14c_decision_writer_postgresql import _native_liquidity_source

from app.models.tables import IBContract, WinnerForwardOutcome, WinnerPredictionSnapshot
from app.services.decision_effective_configuration import (
    resolve_setup_configuration,
    resolve_winner_configuration,
)
from app.services.transition_preflight_plan_service import (
    freeze_transition_decision_handoff_manifest,
)
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.config import load_winner_probability_config


def native_pending_prediction(db):
    config = load_winner_probability_config()
    config = replace(config, engine=replace(config.engine, enabled=True))
    configurations = tuple(
        resolve_winner_configuration(config, family=family)
        for family in ("prediction", "outcome", "cohort", "generation")
    ) + (resolve_setup_configuration(),)
    cutoff, _, _ = seed_native_core(
        db,
        cutoff_at=datetime(2026, 8, 19, 21, 30, tzinfo=UTC),
        extra_configurations=configurations,
        raw_values={**_native_liquidity_source(), "upcoming_earnings_date": "2026-11-10"},
    )
    db.commit()
    handoff = freeze_transition_decision_handoff_manifest(db, upload_run_id=7, market_cutoff=cutoff)
    handoff_id = handoff.id
    db.commit()
    db.add(
        IBContract(
            ticker="ACME",
            ib_conid=81234,
            symbol="ACME",
            local_symbol="ACME",
            exchange="SMART",
            primary_exchange="NASDAQ",
            currency="USD",
            sec_type="STK",
            trading_class="NMS",
            resolution_status="RESOLVED",
        )
    )
    db.commit()
    result = WinnerPredictionCaptureService().capture_run(
        db,
        run_id=7,
        config=config,
        market_cutoff=cutoff,
        decision_handoff_manifest_id=handoff_id,
        decision_at=datetime.now(UTC),
    )
    assert result.inserted == 1 and result.failed == 0, result.as_dict()
    prediction = db.scalar(
        select(WinnerPredictionSnapshot).where(WinnerPredictionSnapshot.run_id == 7)
    )
    outcome = db.scalar(
        select(WinnerForwardOutcome).where(
            WinnerForwardOutcome.prediction_id == prediction.id,
            WinnerForwardOutcome.entry_model == "NEXT_OPEN",
            WinnerForwardOutcome.horizon_sessions == 5,
            WinnerForwardOutcome.is_current_revision.is_(True),
        )
    )
    db.commit()
    return prediction, outcome
