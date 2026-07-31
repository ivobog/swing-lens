from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models.tables import (
    FirstEvent,
    OutcomeStatus,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerSimilarityLink,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.evidence_service import EvidenceOutcome
from app.services.winner_probability.similarity_service import (
    SIMILARITY_EVIDENCE_ROLE,
    SimilarityService,
    distance_between,
)


def test_weighted_gower_distance_mixes_numeric_and_categorical_features() -> None:
    result = distance_between(
        {
            "combined_score": Decimal("8"),
            "setup_family": "Breakout",
            "market_risk_state": "Green",
        },
        {
            "combined_score": Decimal("6"),
            "setup_family": "Pullback",
            "market_risk_state": "Green",
        },
        feature_names=("combined_score", "setup_family", "market_risk_state"),
        feature_weights={"combined_score": Decimal("2"), "setup_family": Decimal("1")},
    )

    assert result.distance == Decimal("0.35000000")
    assert result.coverage == Decimal("1.000000")
    assert result.contributions[0].feature_name == "setup_family"
    assert result.contributions[0].weighted_distance == Decimal("1.000000")


def test_missingness_reduces_coverage_without_inventing_distance() -> None:
    result = distance_between(
        {
            "combined_score": Decimal("8"),
            "setup_family": "Breakout",
            "market_risk_state": "Green",
        },
        {
            "combined_score": None,
            "setup_family": "Breakout",
            "market_risk_state": "Red",
        },
        feature_names=("combined_score", "setup_family", "market_risk_state"),
        feature_weights={"combined_score": Decimal("2")},
    )

    assert result.coverage == Decimal("0.500000")
    assert result.distance == Decimal("0.50000000")
    assert {item.feature_name for item in result.contributions} == {
        "setup_family",
        "market_risk_state",
    }


def test_similarity_excludes_future_unmatured_and_same_episode_neighbors() -> None:
    current = _prediction(id=1, days=20, episode_id=11, combined_score=Decimal("8"))
    good = _evidence(_prediction(id=2, days=5, episode_id=12, combined_score=Decimal("7.5")))
    duplicate_episode = _evidence(
        _prediction(id=3, days=6, episode_id=12, combined_score=Decimal("8"))
    )
    same_episode = _evidence(
        _prediction(id=4, days=4, episode_id=11, combined_score=Decimal("8"))
    )
    future = _evidence(_prediction(id=5, days=22, episode_id=15, combined_score=Decimal("8")))
    unmatured = _evidence(
        _prediction(id=6, days=3, episode_id=16, combined_score=Decimal("8")),
        status=OutcomeStatus.PENDING,
    )

    neighbors = SimilarityService().rank_neighbors(
        prediction=current,
        evidence=(good, duplicate_episode, same_episode, future, unmatured),
        feature_names=("combined_score", "setup_family"),
        limit=10,
    )

    assert [neighbor.neighbor_prediction_id for neighbor in neighbors] == [2]
    assert neighbors[0].rank == 1
    assert neighbors[0].outcome_id == good.forward_outcome.id
    assert neighbors[0].outcome_revision == 1
    assert neighbors[0].evidence_role == SIMILARITY_EVIDENCE_ROLE
    assert neighbors[0].outcome_summary.primary_winner is True


def test_similarity_persists_cache_with_exact_neighbor_ids_and_supporting_label() -> None:
    db = SimilarityFakeDb()
    current = _prediction(id=1, days=20, episode_id=11, combined_score=Decimal("8"))
    outcome_definition = WinnerOutcomeDefinition(
        id=3,
        definition_id="T2_5_S2_0_H5_NEXT_OPEN",
        label="target before stop",
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        same_bar_conflict_policy="CONSERVATIVE_STOP_FIRST",
        calculation_version="owpe-calc-1.0.0",
        config_hash="config",
    )
    neighbors = SimilarityService().rank_neighbors(
        prediction=current,
        evidence=(
            _evidence(_prediction(id=2, days=5, episode_id=12, combined_score=Decimal("7.5"))),
        ),
        feature_names=("combined_score", "setup_family"),
    )

    rows = SimilarityService().persist_neighbors(
        db,
        prediction=current,
        outcome_definition=outcome_definition,
        neighbors=neighbors,
    )

    assert len(rows) == 1
    assert isinstance(rows[0], WinnerSimilarityLink)
    assert rows[0].neighbor_prediction_id == 2
    assert rows[0].contribution_json["evidence_role"] == SIMILARITY_EVIDENCE_ROLE
    assert rows[0].source_cutoff_at == current.source_data_cutoff_at


class SimilarityFakeDb:
    def __init__(self) -> None:
        self.rows = []
        self.flushes = 0
        self._next_id = 1

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.rows.append(row)

    def flush(self) -> None:
        self.flushes += 1


def _prediction(
    *,
    id: int,
    days: int,
    episode_id: int,
    combined_score: Decimal,
) -> WinnerPredictionSnapshot:
    cutoff = datetime(2026, 7, 1, 21, 0, tzinfo=UTC) + timedelta(days=days)
    return WinnerPredictionSnapshot(
        id=id,
        run_id=1,
        episode_id=episode_id,
        ticker=f"T{id}",
        prediction_as_of_date=cutoff.date(),
        source_data_cutoff_at=cutoff,
        planned_entry_session=date(2026, 7, 2),
        entry_schedule_status="AVAILABLE",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        setup_family="Breakout",
        ranking_profile="default",
        combined_score=combined_score,
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash=f"hash-{id}",
        config_hash="config",
        calculation_version="owpe-calc-1.0.0",
        feature_json={
            "combined_score": combined_score,
            "setup_family": "Breakout",
        },
    )


def _evidence(
    prediction: WinnerPredictionSnapshot,
    *,
    status: str = OutcomeStatus.MATURED,
) -> EvidenceOutcome:
    matured_at = prediction.source_data_cutoff_at + timedelta(days=1)
    forward = WinnerForwardOutcome(
        id=prediction.id * 10,
        prediction_id=prediction.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status=status,
        revision=1,
        is_current_revision=True,
        close_return_pct=Decimal("4.0"),
        mfe_pct=Decimal("5.0"),
        mae_pct=Decimal("-1.0"),
        matured_at=matured_at if status == OutcomeStatus.MATURED else None,
    )
    target_stop = WinnerTargetStopOutcome(
        id=prediction.id * 100,
        prediction_id=prediction.id,
        outcome_definition_id=3,
        forward_outcome_id=forward.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status=status,
        revision=1,
        is_current_revision=True,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        target_hit=True,
        stop_hit=False,
        first_event=FirstEvent.TARGET_FIRST,
        same_bar_conflict=False,
        primary_winner=True,
        optimistic_winner=True,
        conservative_winner=True,
        evaluated_at=matured_at if status == OutcomeStatus.MATURED else None,
    )
    return EvidenceOutcome(
        prediction=prediction,
        forward_outcome=forward,
        target_stop_outcome=target_stop,
    )
