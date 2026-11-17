from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.ceri.score_population import validate_native_snapshot_fields
from app.services.contextual_calculation_identity import (
    build_contextual_result_identity,
    consumer_context_identity,
    identity_metadata,
)
from app.services.contextual_effective_configuration import resolve_ceri_configuration
from app.services.market_clock_service import MarketClockService


def native_row():
    frozen = resolve_ceri_configuration()
    config = frozen.ceri_config()
    cutoff = (
        MarketClockService()
        .cutoff_for(datetime(2026, 11, 15, 22, 57, tzinfo=UTC), reason="CERI_NATIVE_IDENTITY_TEST")
        .with_context_id(116)
    )
    identity = frozen.bind(
        build_contextual_result_identity(
            base=consumer_context_identity(
                market_cutoff=cutoff, run_id=62, pipeline_id=66, ticker="MSFT", company_id=1
            ),
            namespace="ceri-context",
            config_hash=config.config_hash,
            calculation_version=config.engine.calculation_version,
            engine_version=config.engine.calculation_version,
            source_artifacts=(),
            source_payload={"explicit_unavailable_inputs": True},
            company_id=1,
        )
    )
    row = SimpleNamespace(
        run_id=62,
        company_id=1,
        ticker="MSFT",
        calculation_context_id=116,
        as_of_session=cutoff.latest_completed_session,
        cutoff_at=cutoff.cutoff_at,
        calendar_version=cutoff.calendar_version,
        calculation_version=config.engine.calculation_version,
        evidence_lineage_json=identity_metadata(identity, policy="NATIVE_FIELD_TEST"),
    )
    return row, frozen


def test_native_physical_key_matches_the_frozen_identity():
    row, frozen = native_row()
    validate_native_snapshot_fields(row, frozen)


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", 61),
        ("company_id", 2),
        ("ticker", "OTHER"),
        ("calculation_context_id", 113),
        ("calendar_version", "other-calendar"),
        ("calculation_version", "other-version"),
    ],
)
def test_native_physical_key_cannot_disagree_with_sealed_identity(field, value):
    row, frozen = native_row()
    setattr(row, field, value)
    with pytest.raises(ValueError, match="CERI_NATIVE_SNAPSHOT_IDENTITY_MISMATCH"):
        validate_native_snapshot_fields(row, frozen)


def test_native_temporal_key_cannot_drift_within_a_context():
    row, frozen = native_row()
    row.cutoff_at += timedelta(microseconds=1)
    with pytest.raises(ValueError, match="CERI_NATIVE_SNAPSHOT_IDENTITY_MISMATCH"):
        validate_native_snapshot_fields(row, frozen)
