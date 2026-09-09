from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.tables import SetupSignalSnapshot
from app.services.market_clock_service import MarketClockService
from app.services.setup_lifecycle.repository import SetupSignalSnapshotWrite
from app.services.setup_lifecycle.snapshot_builder import BuiltSnapshot
from app.services.setup_lifecycle.transition_candidate_service import (
    TransitionCandidateDiscoveryService,
)


def test_drs_regression_older_prospective_key_is_not_high_transition() -> None:
    service = TransitionCandidateDiscoveryService()
    current = _snapshot(19017, date(2026, 8, 3), datetime(2026, 8, 4, tzinfo=UTC))
    built = _built(date(2026, 7, 30))

    result = service.assess(
        built,
        prospective=_cutoff(),
        latest_pointer=current,
        exact_pointer=None,
        all_required_pit_inputs=True,
    )

    assert result.predicted_pointer_advance is False
    assert result.confidence != "HIGH"
    assert result.would_initialize_new_key is True
    assert result.current_pointer_target_session == date(2026, 8, 3)
    assert result.latest_reconstructable_session == date(2026, 7, 30)


def test_genuine_same_key_transition_is_high() -> None:
    service = TransitionCandidateDiscoveryService()
    current = _snapshot(10, date(2026, 8, 3), datetime(2026, 8, 4, tzinfo=UTC))
    built = _built(date(2026, 8, 3), calculated_at=datetime(2026, 9, 9, tzinfo=UTC))

    result = service.assess(
        built,
        prospective=_cutoff(),
        latest_pointer=current,
        exact_pointer=current,
        all_required_pit_inputs=True,
    )

    assert result.predicted_pointer_advance is True
    assert result.confidence == "HIGH"
    assert result.can_compete_with_existing_pointer is True
    assert result.would_initialize_new_key is False


def test_new_key_initialization_never_counts_as_transition() -> None:
    service = TransitionCandidateDiscoveryService()
    result = service.assess(
        _built(date(2026, 8, 4)),
        prospective=_cutoff(),
        latest_pointer=_snapshot(10, date(2026, 8, 3), datetime(2026, 8, 4, tzinfo=UTC)),
        exact_pointer=None,
        all_required_pit_inputs=True,
    )

    assert result.reason == "NEW_KEY_INITIALIZATION_NOT_TRANSITION_COVERAGE"
    assert result.confidence == "LOW"
    assert not result.predicted_pointer_advance


def test_post_cutoff_revision_state_cannot_produce_high_confidence() -> None:
    current = _snapshot(10, date(2026, 8, 3), datetime(2026, 8, 4, tzinfo=UTC))
    result = TransitionCandidateDiscoveryService().assess(
        _built(date(2026, 8, 3)),
        prospective=_cutoff(),
        latest_pointer=current,
        exact_pointer=current,
        all_required_pit_inputs=False,
    )

    assert not result.predicted_pointer_advance
    assert result.confidence == "LOW"
    assert "PIT_INPUTS_INCOMPLETE" in result.reason


def _cutoff():
    return MarketClockService().cutoff_for(
        datetime(2026, 9, 9, 9, 30, 53, tzinfo=UTC),
        reason="FULL_PIPELINE_FROZEN_AT_ENQUEUE",
    )


def _built(
    as_of: date, *, calculated_at: datetime = datetime(2026, 9, 9, tzinfo=UTC)
) -> BuiltSnapshot:
    dto = SetupSignalSnapshotWrite(
        ticker="DRS",
        timeframe="1d",
        data_as_of_date=as_of,
        calculated_at=calculated_at,
        origin_type="LIVE_RUN",
        engine_version="test",
        config_version="test",
        config_hash="hash",
        source_data_hash="source-hash",
        schema_version="test",
        data_quality_label="HIGH",
        promoted_fields={
            "required_feature_coverage": Decimal("1"),
            "market_regime_snapshot_id": 1,
            "sector_rotation_snapshot_id": 2,
        },
        source_lineage={"latest_bar": {"bar_date": as_of.isoformat()}},
    )
    return BuiltSnapshot(dto, (), 1.0, "FRESH", "source-hash")


def _snapshot(snapshot_id: int, as_of: date, calculated_at: datetime) -> SetupSignalSnapshot:
    return SetupSignalSnapshot(
        id=snapshot_id,
        ticker="DRS",
        timeframe="1d",
        data_as_of_date=as_of,
        calculated_at=calculated_at,
        origin_type="LIVE_RUN",
        engine_version="test",
        config_version="test",
        config_hash="hash",
        source_data_hash=f"source-{snapshot_id}",
        schema_version="test",
        data_quality_label="HIGH",
        required_feature_coverage=Decimal("1"),
        market_regime_snapshot_id=1,
        sector_rotation_snapshot_id=2,
        source_lineage_json={"latest_bar": {"bar_date": as_of.isoformat()}},
        warning_flags_json=[],
    )
