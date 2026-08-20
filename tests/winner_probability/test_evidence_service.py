from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.cohort_definition import CohortKey
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_service import (
    EvidenceService,
    _replay_lineage_is_reproducible,
)
from app.services.winner_probability.pre11_compatibility_service import _hash


def test_replay_lineage_filter_rejects_bar_changed_after_classification() -> None:
    bars = [
        {
            "price_bar_id": 41,
            "price_bar_revision_id": None,
            "data_hash": "reviewed",
            "revision_count": 0,
        }
    ]
    replay = SimpleNamespace(
        id=7,
        horizon_sessions=1,
        bar_lineage_json={"bars": bars, "source_forward_outcome_revision": 1},
        source_bar_lineage_hash=_hash({"bars": tuple(bars)}),
        source_forward_outcome_id=31,
        source_revision_cutoff_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    forward = SimpleNamespace(id=31, revision=1)
    changed = SimpleNamespace(id=41, data_hash="changed", revision_count=1)

    assert not _replay_lineage_is_reproducible(
        replay,
        forward,
        price_bars={41: changed},
        price_bar_revisions={},
    )


def test_replay_lineage_filter_accepts_exact_unrevised_bar() -> None:
    bars = [
        {
            "price_bar_id": 41,
            "price_bar_revision_id": None,
            "data_hash": "reviewed",
            "revision_count": 0,
        }
    ]
    replay = SimpleNamespace(
        id=7,
        horizon_sessions=1,
        bar_lineage_json={"bars": bars, "source_forward_outcome_revision": 1},
        source_bar_lineage_hash=_hash({"bars": tuple(bars)}),
        source_forward_outcome_id=31,
        source_revision_cutoff_at=datetime(2026, 8, 14, tzinfo=UTC),
    )
    forward = SimpleNamespace(id=31, revision=1)
    reviewed = SimpleNamespace(id=41, data_hash="reviewed", revision_count=0)

    assert _replay_lineage_is_reproducible(
        replay,
        forward,
        price_bars={41: reviewed},
        price_bar_revisions={},
    )


def test_evidence_excludes_future_current_dependent_and_reconstructed_rows() -> None:
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)
    current = _prediction(99, cutoff=cutoff)
    valid = _row(1, cutoff=cutoff - timedelta(days=30))
    future = _row(2, cutoff=cutoff + timedelta(days=1))
    dependent = _row(3, cutoff=cutoff - timedelta(days=30), dependent=True)
    reconstructed = _row(4, cutoff=cutoff - timedelta(days=30), reconstructed=True)
    db = EvidenceFakeDb([valid, future, dependent, reconstructed])

    result = EvidenceService().load_evidence(
        db,
        prediction=current,
        outcome_definition=_definition(),
        cohort_key=CohortKey(level="L5", dimensions={"global": "all"}, key="L5:test"),
        training_cutoff_at=cutoff,
        config=load_winner_probability_config(),
    )

    assert [row.prediction.id for row in result] == [1]


def test_evidence_uses_revision_visible_at_training_cutoff() -> None:
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)
    current = _prediction(99, cutoff=cutoff)
    visible_old_revision = _row(
        1,
        cutoff=cutoff - timedelta(days=30),
        superseded_at=cutoff + timedelta(days=1),
    )
    already_superseded = _row(
        2,
        cutoff=cutoff - timedelta(days=30),
        superseded_at=cutoff - timedelta(seconds=1),
    )
    db = EvidenceFakeDb([visible_old_revision, already_superseded])

    result = EvidenceService().load_evidence(
        db,
        prediction=current,
        outcome_definition=_definition(),
        cohort_key=CohortKey(level="L5", dimensions={"global": "all"}, key="L5:test"),
        training_cutoff_at=cutoff,
        config=load_winner_probability_config(),
    )

    assert [row.prediction.id for row in result] == [1]


def test_positive_funnel_reaches_authoritative_minimum_independent_sample() -> None:
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)
    current = _prediction(999, cutoff=cutoff)
    rows = [_row(index, cutoff=cutoff - timedelta(days=30)) for index in range(1, 16)]

    funnel = EvidenceService().diagnostic_funnel(
        EvidenceFakeDb(rows),
        prediction=current,
        outcome_definition=_definition(),
        cohort_key=CohortKey(level="L5", dimensions={"global": "all"}, key="L5:test"),
        training_cutoff_at=cutoff,
        config=load_winner_probability_config(),
    )

    assert len(funnel.evidence) == 15
    assert all(stage.after_count == 15 for stage in funnel.stages)


def test_global_funnel_is_reused_for_same_immutable_cutoff_contract() -> None:
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)
    db = EvidenceFakeDb([_row(1, cutoff=cutoff - timedelta(days=30))])
    service = EvidenceService()
    kwargs = {
        "outcome_definition": _definition(),
        "cohort_key": CohortKey(
            level="L5", dimensions={"global": "all"}, key="L5:test"
        ),
        "training_cutoff_at": cutoff,
        "config": load_winner_probability_config(),
    }

    first = service.diagnostic_funnel(db, prediction=_prediction(998, cutoff=cutoff), **kwargs)
    second = service.diagnostic_funnel(db, prediction=_prediction(999, cutoff=cutoff), **kwargs)

    assert first is second
    assert db.execute_count == 1


@pytest.mark.parametrize(
    ("stage", "mutation"),
    [
        ("prediction_eligible", lambda p, f, t, c: setattr(p, "eligibility_status", "EXCLUDED")),
        (
            "point_in_time_validated",
            lambda p, f, t, c: p.lineage_json.update(point_in_time_validated=False),
        ),
        (
            "production_training_eligible",
            lambda p, f, t, c: p.lineage_json.update(capture_training_candidate=False),
        ),
        (
            "feature_schema_compatible",
            lambda p, f, t, c: setattr(p, "feature_schema_version", "old-schema"),
        ),
        (
            "calculation_version_compatible",
            lambda p, f, t, c: setattr(p, "calculation_version", "old-calc"),
        ),
        ("config_compatible", lambda p, f, t, c: setattr(p, "config_hash", "old-config")),
        (
            "quality_gates",
            lambda p, f, t, c: setattr(p, "warning_flags_json", ["quality_blocking"]),
        ),
        (
            "rolling_window_eligible",
            lambda p, f, t, c: setattr(p, "prediction_as_of_date", date(2010, 1, 1)),
        ),
        (
            "no_revised_after_cutoff_leakage",
            lambda p, f, t, c: setattr(f, "source_revision_cutoff_at", c + timedelta(seconds=1)),
        ),
        ("independent_episode", lambda p, f, t, c: p.lineage_json.update(dependent_episode=True)),
    ],
)
def test_each_production_gate_can_remove_otherwise_valid_observation(stage, mutation) -> None:
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)
    row = _row(1, cutoff=cutoff - timedelta(days=30))
    mutation(*row, cutoff)

    funnel = EvidenceService().diagnostic_funnel(
        EvidenceFakeDb([row]),
        prediction=_prediction(999, cutoff=cutoff),
        outcome_definition=_definition(),
        cohort_key=CohortKey(level="L5", dimensions={"global": "all"}, key="L5:test"),
        training_cutoff_at=cutoff,
        config=load_winner_probability_config(),
    )

    target_stage = next(item for item in funnel.stages if item.predicate == stage)
    assert target_stage.before_count == 1
    assert target_stage.after_count == 0


class EvidenceFakeDb:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.execute_count = 0

    def execute(self, _statement):
        self.execute_count += 1
        return self.rows


def _row(
    id: int,
    *,
    cutoff: datetime,
    dependent: bool = False,
    reconstructed: bool = False,
    superseded_at: datetime | None = None,
):
    prediction = _prediction(id, cutoff=cutoff)
    prediction.episode_id = id
    prediction.lineage_json = {
        "dependent_episode": dependent,
        "point_in_time_validated": True,
        "capture_training_candidate": not dependent and not reconstructed,
        "evidence_training_eligible": not dependent and not reconstructed,
        "training_rejection_reasons": [],
    }
    prediction.reconstruction_method = "AS_OF_REPLAY" if reconstructed else None
    forward = WinnerForwardOutcome(
        id=id + 100,
        prediction_id=id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        due_session=date(2026, 2, 1),
        status="MATURED",
        revision=1,
        is_current_revision=True,
        matured_at=cutoff + timedelta(days=1),
        source_revision_cutoff_at=cutoff + timedelta(days=1),
        superseded_at=superseded_at,
    )
    target = WinnerTargetStopOutcome(
        id=id + 200,
        prediction_id=id,
        outcome_definition_id=1,
        forward_outcome_id=forward.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        primary_winner=True,
        evaluated_at=cutoff + timedelta(days=1),
        superseded_at=superseded_at,
    )
    return prediction, forward, target


def _prediction(id: int, *, cutoff: datetime) -> WinnerPredictionSnapshot:
    config = load_winner_probability_config()
    return WinnerPredictionSnapshot(
        id=id,
        run_id=id,
        ticker=f"T{id}",
        prediction_as_of_date=date(2026, 1, 1),
        source_data_cutoff_at=cutoff,
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash=f"hash-{id}",
        config_hash=config.config_hash,
        calculation_version=config.engine.calculation_version,
        feature_json={},
        lineage_json={
            "point_in_time_validated": True,
            "capture_training_candidate": True,
            "evidence_training_eligible": True,
        },
    )


def _definition():
    config = load_winner_probability_config()
    raw = config.primary_outcome_definition
    return type(
        "Definition",
        (),
        {
            "id": 1,
            "definition_id": raw.id,
            "entry_model": raw.entry_model,
            "horizon_sessions": raw.horizon_sessions,
            "target_pct": raw.target_pct,
            "stop_pct": raw.stop_pct,
            "calculation_version": config.engine.calculation_version,
            "config_hash": config.config_hash,
            "is_active": True,
        },
    )()
