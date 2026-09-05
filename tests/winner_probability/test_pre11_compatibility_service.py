from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_service import (
    _select_latest_compatibility_replays,
)
from app.services.winner_probability.pre11_compatibility_service import (
    BRIDGE_VERSION,
    OPTIONAL_MISSING,
    PRE11_SOURCE_CALCULATION,
    PRE11_SOURCE_CONFIG_HASH,
    PRE11_SOURCE_FEATURE_SCHEMA,
    TRAINING_FAMILY,
    Pre11CompatibilityScope,
    Pre11CompatibilityService,
    Pre11CompatibilityWriteService,
    ReplayPreview,
)

CUTOFF = datetime(2026, 8, 14, 20, 34, 33, tzinfo=UTC)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda row: setattr(row, "reconstruction_method", "LEGACY_REBUILD"),
            "SNAPSHOT_RECONSTRUCTED",
        ),
        (
            lambda row: row.lineage_json.update(point_in_time_validated=False),
            "PIT_NOT_VALIDATED",
        ),
        (
            lambda row: row.feature_json.update(technical_score=None),
            "FEATURE_INCOMPATIBLE:technical_score",
        ),
        (lambda row: setattr(row, "config_hash", "unknown"), "CONFIG_SEMANTICS_INCOMPATIBLE"),
        (
            lambda row: setattr(row, "warning_flags_json", ["quality_blocking"]),
            "QUALITY_BLOCKING",
        ),
        (
            lambda row: row.lineage_json.update(dependent_episode=True),
            "DEPENDENT_EPISODE",
        ),
    ],
)
def test_classifier_rejects_each_production_integrity_reason(mutation, reason) -> None:
    prediction = _prediction(1)
    mutation(prediction)
    result = _dry_run([prediction])

    assert result.final_training_eligible == 0
    assert result.reason_frequencies == {reason: 1}


def test_compatible_native_snapshot_is_accepted_despite_literal_hash_mismatch() -> None:
    result = _dry_run([_prediction(1)])
    classified = result.classifications[0]

    assert result.final_training_eligible == 1
    assert classified.training_allowed is True
    assert classified.config_compatibility["bridge_version"] == BRIDGE_VERSION
    assert classified.config_compatibility["literal_hash_equal"] is False
    assert classified.feature_compatibility["ranking_profile"] == OPTIONAL_MISSING
    assert result.manifest_payload()["write_count"] == 0


def test_compatibility_replay_cannot_readmit_retroactive_next_open() -> None:
    prediction = _prediction(1)
    prediction.captured_at = datetime(2026, 8, 5, 15, 28, tzinfo=UTC)

    result = _dry_run([prediction])

    assert result.final_training_eligible == 0
    assert result.reason_frequencies == {"TEMPORAL_EXECUTION_INELIGIBLE": 1}


def test_missing_optional_ranking_does_not_block_global_training() -> None:
    prediction = _prediction(1)
    prediction.ranking_profile = None
    prediction.feature_json["ranking_profile"] = None

    result = _dry_run([prediction])

    assert result.final_training_eligible == 1


def test_classifier_selects_only_one_independent_representative_per_episode() -> None:
    first = _prediction(1)
    second = _prediction(2)
    second.episode_id = first.episode_id

    result = _dry_run([first, second])

    assert result.final_training_eligible == 1
    assert result.reason_frequencies == {"DUPLICATE_EPISODE_REPRESENTATIVE": 1}


def test_dry_run_is_deterministic_and_performs_zero_writes() -> None:
    db = _NoWriteDb()
    service = Pre11CompatibilityService(replay_resolver=_replay)
    scope = _scope()
    outcome = _definition()
    config = load_winner_probability_config()

    first = service.dry_run(
        db,
        scope=scope,
        outcome_definition=outcome,
        config=config,
        predictions=[_prediction(1)],
    )
    second = service.dry_run(
        db,
        scope=scope,
        outcome_definition=outcome,
        config=config,
        predictions=[_prediction(1)],
    )

    assert first.manifest_hash == second.manifest_hash
    assert first.scope.request_key == second.scope.request_key
    assert db.add_calls == 0


def test_write_requires_explicit_approval_before_any_database_action(tmp_path: Path) -> None:
    result = _dry_run([_prediction(1)])

    with pytest.raises(PermissionError, match="approve_write"):
        Pre11CompatibilityWriteService().persist_decisions_and_replays(
            _NoWriteDb(),
            dry_run=result,
            reviewed_manifest_path=tmp_path / "reviewed.json",
            request_key=result.scope.request_key,
            approve_write=False,
            actor="test",
            outcome_definition=_definition(),
            config=load_winner_probability_config(),
        )


def test_scope_rejects_broad_or_wrong_training_family() -> None:
    scope = Pre11CompatibilityScope(
        training_family="UNSCOPED",
        outcome_definition_id=3,
        cutoff_at=CUTOFF,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 14),
    )

    with pytest.raises(ValueError, match="unsupported training family"):
        scope.validate()


def test_replay_uses_exact_next_open_five_sessions_and_conservative_same_bar() -> None:
    prediction = _prediction(1)
    prediction.prediction_as_of_date = date(2026, 7, 2)  # July 3 holiday.
    prediction.planned_entry_session = date(2026, 7, 6)
    forward = WinnerForwardOutcome(
        id=91,
        prediction_id=1,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        entry_session=date(2026, 7, 6),
        due_session=date(2026, 7, 10),
        status="PENDING",
        revision=1,
        is_current_revision=True,
    )
    bars = [
        _bar(index, day, high="103", low="97" if index == 0 else "99")
        for index, day in enumerate(
            (
                date(2026, 7, 6),
                date(2026, 7, 7),
                date(2026, 7, 8),
                date(2026, 7, 9),
                date(2026, 7, 10),
            )
        )
    ]
    service = Pre11CompatibilityService(replay_resolver=lambda *_: None)
    service._forward_cache = {1: forward}
    service._bar_cache = {(prediction.ticker, "ADJUSTED_LAST"): bars}

    replay = service._replay_preview(_NoWriteDb(), prediction, _definition(), CUTOFF)

    assert replay is not None
    assert replay.entry_session == date(2026, 7, 6)
    assert replay.due_session == date(2026, 7, 10)
    assert replay.same_bar_conflict is True
    assert replay.primary_winner is False
    assert [item["price_bar_id"] for item in replay.bar_lineage] == [1, 2, 3, 4, 5]


def test_source_snapshot_identity_is_not_modified_by_classification() -> None:
    prediction = _prediction(1)
    before = (
        prediction.config_hash,
        prediction.calculation_version,
        prediction.feature_vector_hash,
        prediction.lineage_json.copy(),
    )

    _dry_run([prediction])

    assert (
        prediction.config_hash,
        prediction.calculation_version,
        prediction.feature_vector_hash,
        prediction.lineage_json,
    ) == before


def test_latest_append_only_rejection_supersedes_older_approval() -> None:
    prediction = _prediction(1)
    forward = SimpleNamespace(id=10)
    replay = SimpleNamespace(
        id=20,
        status="MATURED",
        replayed_at=CUTOFF.replace(year=2026, month=8, day=13),
    )
    latest_rejected = SimpleNamespace(id=31, revision=2, training_allowed=False)
    older_allowed = SimpleNamespace(id=30, revision=1, training_allowed=True)

    selected = _select_latest_compatibility_replays(
        [
            (prediction, None, None, latest_rejected),
            (prediction, forward, replay, older_allowed),
        ],
        CUTOFF,
    )

    assert selected == ()


def _dry_run(predictions):
    return Pre11CompatibilityService(replay_resolver=_replay).dry_run(
        _NoWriteDb(),
        scope=_scope(),
        outcome_definition=_definition(),
        config=load_winner_probability_config(),
        predictions=predictions,
    )


def _scope() -> Pre11CompatibilityScope:
    return Pre11CompatibilityScope(
        training_family=TRAINING_FAMILY,
        outcome_definition_id=3,
        cutoff_at=CUTOFF,
        start_date=date(2021, 8, 14),
        end_date=date(2026, 8, 14),
    )


def _definition() -> WinnerOutcomeDefinition:
    config = load_winner_probability_config()
    primary = config.primary_outcome_definition
    return WinnerOutcomeDefinition(
        id=3,
        definition_id=primary.id,
        label=primary.label,
        entry_model=primary.entry_model,
        horizon_sessions=primary.horizon_sessions,
        target_pct=Decimal(str(primary.target_pct)),
        stop_pct=Decimal(str(primary.stop_pct)),
        same_bar_conflict_policy=primary.same_bar_conflict_policy,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        is_active=True,
    )


def _prediction(row_id: int) -> WinnerPredictionSnapshot:
    features = {
        "setup_family": "Candidate",
        "technical_score": "7.5",
        "combined_score": "8.0",
        "market_regime": "Confirmed Uptrend",
        "market_risk_state": "Green",
        "technical_data_quality": "high",
        "universe_provenance": "historical.csv",
        "ranking_profile": None,
        "fundamental_score": "8.0",
        "fundamental_coverage": "10",
        "sector_state": None,
        "sector_rank": None,
        "sector_leadership_bucket": None,
        "earnings_risk": "clear",
        "reward_risk": "2",
        "screener_provenance": "native",
        "score_band": "8_plus",
        "dual_score_band": "8_plus",
        "market_regime_family": "Confirmed Uptrend",
    }
    source_cutoff = datetime(2026, 8, 4, 21, 0, tzinfo=UTC)
    return WinnerPredictionSnapshot(
        id=row_id,
        run_id=row_id,
        ticker=f"T{row_id}",
        prediction_as_of_date=date(2026, 8, 4),
        source_data_cutoff_at=source_cutoff,
        planned_entry_session=date(2026, 8, 5),
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        setup_family="Candidate",
        episode_id=row_id,
        feature_schema_version=PRE11_SOURCE_FEATURE_SCHEMA,
        feature_vector_hash=f"feature-{row_id}",
        config_hash=PRE11_SOURCE_CONFIG_HASH,
        calculation_version=PRE11_SOURCE_CALCULATION,
        revision=1,
        feature_json=features,
        source_ids_json={
            "raw_row_id": row_id,
            "technical_score_id": row_id,
            "fundamental_score_id": row_id,
            "combined_result_id": row_id,
            "market_regime_snapshot_id": row_id,
        },
        warning_flags_json=[],
        lineage_json={
            "point_in_time_validated": True,
            "dependent_episode": False,
            "source_quality_flags": [],
            "feature_cutoff_audit_hash": f"audit-{row_id}",
            "feature_cutoff_audit": {
                "technical_score": {
                    "status": "available",
                    "source_available_at": source_cutoff.isoformat(),
                }
            },
        },
        reconstruction_method=None,
    )


def _replay(_db, prediction, _outcome, _cutoff) -> ReplayPreview:
    return ReplayPreview(
        prediction_id=prediction.id,
        source_forward_outcome_id=prediction.id + 100,
        source_forward_outcome_revision=1,
        entry_session=date(2026, 8, 5),
        due_session=date(2026, 8, 11),
        entry_price=Decimal("100"),
        exit_price=Decimal("102"),
        close_return_pct=Decimal("2"),
        mfe_pct=Decimal("3"),
        mae_pct=Decimal("-1"),
        target_hit=True,
        stop_hit=False,
        first_event="TARGET_FIRST",
        event_session=date(2026, 8, 6),
        same_bar_conflict=False,
        primary_winner=True,
        optimistic_winner=True,
        conservative_winner=True,
        bar_lineage=(),
        bar_lineage_hash=f"bars-{prediction.id}",
        source_revision_cutoff_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


def _bar(index: int, day: date, *, high: str, low: str):
    observed = datetime(2026, 7, 11, tzinfo=UTC)
    return SimpleNamespace(
        id=index + 1,
        bar_date=day,
        what_to_show="ADJUSTED_LAST",
        adjustment_type="adjusted",
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        revision_count=0,
        data_hash=f"bar-{index}",
        revised_at=None,
        first_seen_at=observed,
        created_at=observed,
    )


class _NoWriteDb:
    def __init__(self) -> None:
        self.add_calls = 0

    def add(self, _row) -> None:
        self.add_calls += 1
