from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.tables import PriceBar, PriceBarRevision
from app.services.ceri.artifact_lineage import CeriArtifactOwnership
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.price_response_service import CeriPriceResponseService
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.price_bar_evidence import (
    PRICE_BAR_FIELD_CLASSIFICATION,
    price_bar_full_row_diagnostic_hash,
    price_bar_immutable_evidence_hash,
    price_bar_immutable_evidence_set_hash,
)
from app.services.price_bar_repository import project_price_bar_rows_as_of
from app.services.setup_lifecycle.decision_manifest import (
    build_transition_decision_manifest,
)
from scripts.winner_candidate_estimates import _protected_integrity_state

CUTOFF = datetime(2026, 9, 10, 8, 13, 4, 357207, tzinfo=UTC)


def test_identical_reobservation_changes_only_diagnostic_bar_hash() -> None:
    bar = _bar()
    immutable_before = price_bar_immutable_evidence_hash(bar)
    diagnostic_before = price_bar_full_row_diagnostic_hash(bar)

    bar.last_seen_at = bar.last_seen_at + timedelta(days=1)

    assert PRICE_BAR_FIELD_CLASSIFICATION["last_seen_at"] == "MUTABLE_OPERATIONAL_METADATA"
    assert price_bar_immutable_evidence_hash(bar) == immutable_before
    assert price_bar_full_row_diagnostic_hash(bar) != diagnostic_before


def test_immutable_bar_set_hash_is_order_invariant() -> None:
    first = _bar(1, date(2026, 8, 3))
    second = _bar(2, date(2026, 7, 31))
    assert price_bar_immutable_evidence_set_hash([first, second]) == (
        price_bar_immutable_evidence_set_hash([second, first])
    )


def test_winner_protected_gate_reports_but_ignores_full_row_diagnostic_drift() -> None:
    before = {
        "prices": {"count": 2, "sha256": "immutable"},
        "prices_full_row_diagnostic": {"count": 2, "sha256": "before"},
    }
    after = {
        "prices": {"count": 2, "sha256": "immutable"},
        "prices_full_row_diagnostic": {"count": 2, "sha256": "after"},
    }
    assert _protected_integrity_state(before) == _protected_integrity_state(after)


def test_market_revision_changes_immutable_bar_hash() -> None:
    bar = _bar()
    before = price_bar_immutable_evidence_hash(bar)
    bar.volume = Decimal("3069033")
    bar.revision_count = 1
    bar.revised_at = CUTOFF + timedelta(hours=1)
    bar.data_hash = "new-data-hash"
    assert price_bar_immutable_evidence_hash(bar) != before


def test_post_cutoff_revision_projects_pre_revision_market_evidence() -> None:
    bar = _bar()
    bar.volume = Decimal("3069033")
    bar.revision_count = 1
    bar.revised_at = CUTOFF + timedelta(hours=1)
    bar.data_hash = "new-data-hash"
    revision = PriceBarRevision(
        id=99,
        price_bar_id=bar.id,
        ticker=bar.ticker,
        bar_date=bar.bar_date,
        timeframe=bar.timeframe,
        what_to_show=bar.what_to_show,
        revision_number=1,
        previous_data_hash="old-data-hash",
        new_data_hash="new-data-hash",
        previous_values_json={
            "open": "5.1",
            "high": "5.26",
            "low": "5.06",
            "close": "5.19",
            "volume": "3067219",
            "source": "IB",
            "what_to_show": "TRADES",
            "adjustment_type": None,
        },
        new_values_json={},
        observed_at=CUTOFF + timedelta(hours=1),
    )

    projected = project_price_bar_rows_as_of(_RevisionDb([revision]), [bar], as_of=CUTOFF)[0]

    assert projected.id == bar.id
    assert projected.volume == Decimal("3067219")
    assert projected.revision_count == 0
    assert projected.revised_at is None
    assert projected.data_hash == "old-data-hash"


def test_post_cutoff_revision_without_history_is_conservatively_excluded() -> None:
    bar = _bar()
    bar.revised_at = CUTOFF + timedelta(hours=1)
    assert project_price_bar_rows_as_of(_RevisionDb([]), [bar], as_of=CUTOFF) == []


def test_decision_manifest_is_order_and_timezone_invariant() -> None:
    first = _decision_manifest([_bar(1, date(2026, 8, 3)), _bar(2, date(2026, 7, 31))])
    zurich_cutoff = _cutoff(CUTOFF.astimezone(ZoneInfo("Europe/Zurich")))
    second = _decision_manifest(
        [_bar(2, date(2026, 7, 31)), _bar(1, date(2026, 8, 3))],
        cutoff=zurich_cutoff,
    )
    assert first.payload == second.payload
    assert first.fingerprint == second.fingerprint


def test_decision_manifest_changes_for_material_bar_revision() -> None:
    bar = _bar()
    before = _decision_manifest([bar])
    bar.volume = Decimal("3069033")
    bar.revision_count = 1
    bar.revised_at = CUTOFF - timedelta(seconds=1)
    bar.data_hash = "new-data-hash"
    after = _decision_manifest([bar])
    assert after.fingerprint != before.fingerprint


def test_decision_manifest_ignores_provider_reobservation_metadata() -> None:
    bar = _bar()
    before = _decision_manifest([bar])
    bar.last_seen_at += timedelta(days=1)
    after = _decision_manifest([bar])
    assert after.payload == before.payload
    assert after.fingerprint == before.fingerprint


def test_decision_manifest_changes_when_expected_pointer_changes() -> None:
    before = _decision_manifest([_bar()])
    after = _decision_manifest([_bar()], current_pointer_snapshot_id=18886)
    assert after.fingerprint != before.fingerprint


def test_pipeline_owned_ceri_price_feature_requires_and_persists_context() -> None:
    service = CeriPriceResponseService()
    result = service.unavailable(
        company_id=1,
        event_type="NONE",
        reason="NO_ACCEPTED_EVENT",
        cutoff_at=CUTOFF,
    )
    with pytest.raises(ValueError, match="calculation_context_id"):
        service.build_feature(
            result=result,
            company_id=1,
            ticker="TBLA",
            event_id=None,
            event_effective_at=None,
            event_effective_session=None,
            feature_as_of_session=date(2026, 9, 9),
            cutoff_at=CUTOFF,
            calendar_version="calendar-v1",
            ownership_mode=CeriArtifactOwnership.PIPELINE.value,
        )

    feature = service.build_feature(
        result=result,
        company_id=1,
        ticker="TBLA",
        event_id=None,
        event_effective_at=None,
        event_effective_session=None,
        feature_as_of_session=date(2026, 9, 9),
        cutoff_at=CUTOFF,
        calculation_context_id=6,
        calendar_version="calendar-v1",
        ownership_mode=CeriArtifactOwnership.PIPELINE.value,
    )
    assert feature.calculation_context_id == 6
    assert feature.ownership_mode == "PIPELINE"
    assert feature.event_key.endswith(":context:6")


def test_pipeline_feature_batch_rejects_optional_by_accident_context() -> None:
    with pytest.raises(ValueError, match="calculation_context_id"):
        CeriFeatureRebuildService().prepare_batch(
            object(),
            CeriFeatureRebuildRequest(
                ticker="TBLA",
                cutoff_at=CUTOFF,
                as_of_session=date(2026, 9, 9),
                calendar_version="calendar-v1",
                ownership_mode=CeriArtifactOwnership.PIPELINE.value,
            ),
        )


def test_standalone_ceri_lineage_is_explicit() -> None:
    request = CeriFeatureRebuildRequest(ticker="TBLA")
    assert request.ownership_mode == CeriArtifactOwnership.STANDALONE.value
    with pytest.raises(ValueError, match="Unsupported CERI artifact ownership mode"):
        CeriPriceResponseService().build_feature(
            result=CeriPriceResponseService().unavailable(
                company_id=1,
                event_type="NONE",
                reason="NO_ACCEPTED_EVENT",
                cutoff_at=CUTOFF,
            ),
            company_id=1,
            ticker="TBLA",
            event_id=None,
            event_effective_at=None,
            event_effective_session=None,
            feature_as_of_session=date(2026, 9, 9),
            ownership_mode="UNKNOWN",
        )


class _RevisionDb:
    def __init__(self, revisions):
        self.revisions = revisions

    def scalars(self, _statement):
        return self.revisions


def _bar(identifier: int = 1786650, session: date = date(2026, 8, 3)) -> PriceBar:
    return PriceBar(
        id=identifier,
        ticker="TBLA",
        bar_date=session,
        timeframe="1 day",
        open=Decimal("5.1"),
        high=Decimal("5.26"),
        low=Decimal("5.06"),
        close=Decimal("5.19"),
        volume=Decimal("3067219"),
        source="IB",
        what_to_show="TRADES",
        adjustment_type=None,
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
        first_seen_at=datetime(2026, 8, 4, tzinfo=UTC),
        last_seen_at=datetime(2026, 9, 9, tzinfo=UTC),
        revised_at=None,
        revision_count=0,
        data_hash="old-data-hash",
    )


def _cutoff(value=CUTOFF) -> MarketCalculationCutoff:
    return MarketCalculationCutoff(
        cutoff_at=value,
        exchange_timezone="America/New_York",
        latest_completed_session=date(2026, 9, 9),
        daily_bar_ready_at=datetime(2026, 9, 9, 20, 15, tzinfo=UTC),
        calendar_version="calendar-v1",
        bar_readiness_version="bar-ready-v1",
        cutoff_reason="TEST",
        context_id=6,
    )


def _decision_manifest(bars, *, cutoff=None, current_pointer_snapshot_id=18885):
    dto = SimpleNamespace(
        ticker="TBLA",
        timeframe="1d",
        data_as_of_date=date(2026, 8, 3),
        source_ids={"raw_row_id": 10, "technical_score_id": None},
        engine_version="engine-v1",
        config_version="config-v1",
        config_hash="config-hash",
        schema_version="schema-v1",
        promoted_fields={"dual_score": Decimal("6.7869"), "classification": "No trade"},
        signals={"technical_score": {"value": "6.7869"}},
        feature_flags={},
        warning_flags=[],
        missing_data={},
        data_quality_label="HIGH",
    )
    built = SimpleNamespace(
        dto=dto,
        required_feature_coverage=1.0,
        freshness_status="FRESH",
    )
    technical = SimpleNamespace(
        input_as_of_session=date(2026, 9, 9),
        calculation_cutoff_at=CUTOFF,
        calculation_context_id=6,
        technical_engine_version="technical-v1",
    )
    return build_transition_decision_manifest(
        built=built,
        context=SimpleNamespace(technical_score=technical, price_bars=tuple(bars)),
        market_cutoff=cutoff or _cutoff(),
        technical_reconstruction_fingerprint="technical-fingerprint",
        current_pointer_snapshot_id=current_pointer_snapshot_id,
        current_pointer_revision=1,
        exact_pointer_snapshot_id=18885,
        exact_pointer_revision=1,
        candidate_type="SAME_SESSION_REPLACEMENT",
        candidate_reason="EXISTING_POINTER_ADVANCE_DETERMINISTIC_UNDER_FROZEN_CONTEXT",
        candidate_confidence="HIGH",
        predicted_pointer_advance=True,
        predicted_current_state_advance=False,
    )
