from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from time import perf_counter
from types import SimpleNamespace

import pytest

from app.services.winner_probability.cohort_materialization_service import (
    CohortMaterializationService,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_service import EvidenceOutcome


@pytest.mark.performance
def test_large_generation_scales_with_evidence_and_unique_cohorts() -> None:
    historical_prediction_count = 15_000
    current_run_prediction_count = 200
    config = load_winner_probability_config()
    evidence = tuple(_evidence(index) for index in range(historical_prediction_count))
    materializer = CohortMaterializationService()

    started = perf_counter()
    groups = materializer._groups(evidence, config)
    ordered = materializer._ordered_groups(groups, config)
    elapsed = perf_counter() - started

    statistic_rows = len(ordered)
    manifest_member_rows = sum(len(group_evidence) for _, group_evidence in ordered)
    previous_design_memberships = historical_prediction_count * historical_prediction_count

    assert ordered[0][0].level == "L5"
    assert statistic_rows < 500
    assert manifest_member_rows <= historical_prediction_count * len(config.cohort.hierarchy)
    assert current_run_prediction_count == 200
    assert manifest_member_rows < previous_design_memberships // 1000
    assert elapsed < 8.0


def _evidence(index: int) -> EvidenceOutcome:
    setup = f"setup-{index % 12}"
    score = f"score-{index % 5}"
    prediction = SimpleNamespace(
        id=index + 1,
        episode_id=index + 1,
        prediction_as_of_date=date(2026, 1, 1),
        feature_json={
            "setup_family": setup,
            "dual_score_band": score,
            "score_band": score,
            "market_risk_state": f"risk-{index % 3}",
            "sector_state": f"sector-{index % 4}",
            "ranking_profile": f"profile-{index % 2}",
            "sector_leadership_bucket": f"lead-{index % 4}",
            "market_regime_family": f"regime-{index % 3}",
        },
    )
    forward = SimpleNamespace(
        id=index + 100_000,
        revision=1,
        close_return_pct=Decimal(str((index % 11) - 5)),
        mfe_pct=Decimal(str((index % 9) + 1)),
        mae_pct=Decimal(str(-((index % 7) + 1))),
        matured_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    target = SimpleNamespace(
        id=index + 200_000,
        revision=1,
        primary_winner=index % 2 == 0,
        first_event="TARGET_FIRST" if index % 2 == 0 else "STOP_FIRST",
    )
    return EvidenceOutcome(
        prediction=prediction,
        forward_outcome=forward,
        target_stop_outcome=target,
    )
