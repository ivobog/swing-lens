from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriRevisionFeature,
    CeriScoreSnapshot,
)
from app.services.ceri.change_detection_service import CeriChangeDetectionService
from app.services.ceri.change_semantics import (
    CHANGE_GROUP_BY_TYPE,
    ChangeGroup,
    ComparisonState,
    Importance,
    SignalClass,
    classify_snapshot_comparison,
)
from app.services.ceri.enums import CeriChangeType
from app.services.ceri.legacy_alert_audit import AlertValidity, classify_legacy_alert
from app.services.ceri.query_service import (
    CeriQueryFilters,
    _change_matches_filters,
    _change_payload,
)


def test_every_change_type_has_one_explicit_production_group() -> None:
    assert set(CHANGE_GROUP_BY_TYPE) == set(CeriChangeType)
    assert CHANGE_GROUP_BY_TYPE[CeriChangeType.BECAME_RATED] is ChangeGroup.OPPORTUNITY
    assert CHANGE_GROUP_BY_TYPE[CeriChangeType.BECAME_UNRATED] is ChangeGroup.OPPORTUNITY


@pytest.mark.parametrize(
    ("field", "old", "new", "expected"),
    [
        (
            "calculation_version",
            "ceri-1.1.0",
            "ceri-1.2.0",
            ComparisonState.MODEL_VERSION_TRANSITION,
        ),
        ("config_hash", "old", "new", ComparisonState.CONFIG_TRANSITION),
        (
            "evidence_contract_version",
            "ceri-evidence-contract-v1",
            "ceri-evidence-contract-v2",
            ComparisonState.EVIDENCE_CONTRACT_TRANSITION,
        ),
    ],
)
def test_snapshot_comparison_classifies_semantic_transitions(
    field: str, old: str, new: str, expected: ComparisonState
) -> None:
    prior = _snapshot(1, opportunity=None, posture="Unrated")
    current = _snapshot(2, opportunity=8.0, posture="Positive")
    setattr(prior, field, old)
    setattr(current, field, new)

    assert classify_snapshot_comparison(prior, current) is expected


def test_no_prior_snapshot_has_explicit_non_comparable_state() -> None:
    assert (
        classify_snapshot_comparison(None, _snapshot(1, opportunity=None, posture="Unrated"))
        is ComparisonState.NO_PRIOR_COMPARABLE_SNAPSHOT
    )


def test_non_comparable_unrated_to_rated_never_emits_market_change() -> None:
    prior = _snapshot(1, opportunity=None, posture="Unrated")
    current = _snapshot(2, opportunity=8.0, posture="Positive")
    prior.evidence_contract_version = "ceri-evidence-contract-v1"
    current.evidence_contract_version = "ceri-evidence-contract-v2"
    db = FakeDb()

    result = CeriChangeDetectionService().detect_score_changes(
        db,
        current=current,
        prior=prior,
        comparison_state=ComparisonState.EVIDENCE_CONTRACT_TRANSITION,
    )

    assert result.changes == 0
    assert result.comparison_state == ComparisonState.EVIDENCE_CONTRACT_TRANSITION.value
    assert not db.added


@pytest.mark.parametrize(
    ("old", "new", "old_posture", "new_posture", "expected"),
    [
        (5.0, 5.5, "Mixed", "Mixed", None),
        (5.0, 6.2, "Mixed", "Mixed", CeriChangeType.OPPORTUNITY_CHANGED),
        (7.0, 7.6, "Improving", "Positive", CeriChangeType.OPPORTUNITY_UPGRADED),
        (8.0, 7.4, "Positive", "Improving", CeriChangeType.OPPORTUNITY_DOWNGRADED),
        (5.0, 5.6, "Mixed", "Improving", CeriChangeType.POSTURE_CHANGED),
    ],
)
def test_opportunity_taxonomy_is_deterministic_and_boundary_aware(
    old: float,
    new: float,
    old_posture: str,
    new_posture: str,
    expected: CeriChangeType | None,
) -> None:
    db = FakeDb()
    result = CeriChangeDetectionService().detect_score_changes(
        db,
        current=_snapshot(2, opportunity=new, posture=new_posture),
        prior=_snapshot(1, opportunity=old, posture=old_posture),
        comparison_state=ComparisonState.COMPARABLE,
    )
    opportunity_changes = [
        row
        for row in db.added
        if isinstance(row, CeriChangeEvent)
        and row.change_type.startswith(("OPPORTUNITY", "POSTURE"))
    ]

    if expected is None:
        assert not opportunity_changes
    else:
        assert [row.change_type for row in opportunity_changes] == [expected.value]
        assert result.changes >= 1


def test_importance_and_signal_class_are_independent() -> None:
    db = FakeDb()
    CeriChangeDetectionService().detect_score_changes(
        db,
        current=_snapshot(2, opportunity=8.2, posture="Positive"),
        prior=_snapshot(1, opportunity=7.0, posture="Improving"),
        comparison_state=ComparisonState.COMPARABLE,
    )
    change = next(row for row in db.added if row.change_type == "OPPORTUNITY_UPGRADED")

    assert change.importance == Importance.NOTABLE.value
    assert change.signal_class == SignalClass.POSITIVE.value
    assert change.severity == Importance.NOTABLE.value


def test_source_arrival_without_materiality_cannot_emit_new_catalyst() -> None:
    revision = _catalyst_revision(materiality=None, status="ANNOUNCED")
    db = FakeDb()

    result = CeriChangeDetectionService().detect_catalyst_revision(
        db, revision=revision, company_id=42
    )

    assert result.changes == 0
    assert not db.added


def test_accepted_material_canonical_catalyst_emits_business_payload() -> None:
    revision = _catalyst_revision(materiality=7.2, status="ANNOUNCED")
    db = FakeDb()

    result = CeriChangeDetectionService().detect_catalyst_revision(
        db, revision=revision, company_id=42
    )

    assert result.changes == 1
    change = db.added[0]
    assert change.change_type == CeriChangeType.NEW_CATALYST.value
    assert change.delta_json["canonical_event_id"] == 9
    assert change.delta_json["materiality"] == 7.2
    assert change.signal_class == SignalClass.NEUTRAL.value


def test_completed_source_arrival_is_not_resolution_without_prior_lifecycle() -> None:
    revision = _catalyst_revision(materiality=7.2, status="COMPLETED")
    result = CeriChangeDetectionService().detect_catalyst_revision(
        FakeDb(), revision=revision, company_id=42
    )
    assert result.changes == 0


def test_legacy_alert_validity_is_lineage_and_comparability_driven() -> None:
    orphan = _alert(1, source_change_event_id=None)
    assert classify_legacy_alert(orphan, change=None) is AlertValidity.ORPHANED

    transition = _change("OPPORTUNITY_UPGRADED")
    transition.comparison_state = "EVIDENCE_CONTRACT_TRANSITION"
    assert classify_legacy_alert(_alert(2), change=transition) is AlertValidity.INVALID_LEGACY

    valid = _change("RISK_ESCALATED")
    valid.to_snapshot_id = 20
    assert (
        classify_legacy_alert(_alert(3), change=valid, latest_snapshot_ids={20})
        is AlertValidity.VALID_CURRENT
    )
    assert (
        classify_legacy_alert(_alert(4), change=valid, latest_snapshot_ids={21})
        is AlertValidity.VALID_HISTORICAL
    )


def test_legacy_alert_duplicate_identity_is_invalidated_without_deletion() -> None:
    assert (
        classify_legacy_alert(_alert(5), change=_change("RISK_ESCALATED"), duplicate=True)
        is AlertValidity.DUPLICATE
    )


def test_default_trader_feed_excludes_transition_and_source_arrival_rows() -> None:
    filters = CeriQueryFilters()
    transition = {
        "comparison_state": "EVIDENCE_CONTRACT_TRANSITION",
        "change_type": "BECAME_RATED",
        "group": "Opportunity",
    }
    source_arrival = {
        "comparison_state": "COMPARABLE",
        "change_type": "NEW_CATALYST",
        "group": "Catalysts",
        "event": {"trader_eligible": False},
    }
    rejected_guidance = {
        "comparison_state": "COMPARABLE",
        "change_type": "GUIDANCE_RAISED",
        "group": "Guidance",
        "current": {"accepted_for_scoring": False},
    }

    assert not _change_matches_filters(transition, filters)
    assert not _change_matches_filters(source_arrival, filters)
    assert not _change_matches_filters(rejected_guidance, filters)
    assert _change_matches_filters(transition, CeriQueryFilters(include_non_comparable=True))
    assert _change_matches_filters(source_arrival, CeriQueryFilters(include_ineligible=True))


def test_opportunity_dto_uses_business_values_and_keeps_ids_technical() -> None:
    prior = _snapshot(1, opportunity=None, posture="Unrated")
    prior.id = 2362
    prior.run_id = 102
    current = _snapshot(2, opportunity=9.423, posture="Positive")
    current.id = 2970
    current.run_id = 104
    change = _change("BECAME_RATED")
    change.from_snapshot_id = prior.id
    change.to_snapshot_id = current.id

    payload = _change_payload(
        change,
        ticker="AEIS",
        snapshots={prior.id: prior, current.id: current},
    )

    assert payload["summary"] == "Unrated -> 9.42 Positive"
    assert payload["previous"]["coverage_pct"] == 0.0
    assert payload["current"]["coverage_pct"] == 70.0
    assert payload["technical"]["from_snapshot_id"] == 2362
    assert "2362" not in payload["summary"]
    assert "2970" not in payload["summary"]


def test_event_dto_never_uses_empty_previous_current_as_primary_values() -> None:
    change = _change("NEW_CATALYST")
    change.from_snapshot_id = None
    change.to_snapshot_id = None
    change.delta_json = {
        "canonical_event_id": 9,
        "category": "REGULATORY",
        "subtype": "review_date",
        "subject": "Review date",
        "status": "SCHEDULED",
        "materiality": 7.2,
        "issuer_relevance": True,
    }

    payload = _change_payload(change, ticker="AIZ")

    assert payload["previous"] is None
    assert payload["current"] is None
    assert payload["semantic"]["display_previous_current"] is False
    assert "N/A" not in payload["summary"]


def test_revision_dto_exposes_window_values_breadth_acceleration_and_threshold() -> None:
    prior = _snapshot(1, opportunity=6.0, posture="Mixed")
    current = _snapshot(2, opportunity=6.0, posture="Mixed")
    prior.component_json = {
        "components": [{"name": "revision_magnitude", "value": 4.0, "evidence_ids": [71]}]
    }
    current.component_json = {
        "components": [{"name": "revision_magnitude", "value": 7.0, "evidence_ids": [72]}]
    }
    old_feature = CeriRevisionFeature(
        id=71,
        company_id=42,
        metric="EPS_DILUTED",
        period_key="CURRENT_QUARTER",
        as_of_session=prior.as_of_session,
        window_days=30,
        pct_change=1.2,
        net_breadth=0.2,
        acceleration=0.01,
        config_version="test",
        config_hash="same-config",
        calculation_version="ceri-1.2.0",
    )
    new_feature = CeriRevisionFeature(
        id=72,
        company_id=42,
        metric="EPS_DILUTED",
        period_key="CURRENT_QUARTER",
        as_of_session=current.as_of_session,
        window_days=30,
        pct_change=4.6,
        net_breadth=0.6,
        acceleration=0.04,
        config_version="test",
        config_hash="same-config",
        calculation_version="ceri-1.2.0",
    )
    change = _change("REVISION_UP")
    change.from_snapshot_id = 1
    change.to_snapshot_id = 2

    payload = _change_payload(
        change,
        ticker="NVDA",
        snapshots={1: prior, 2: current},
        revision_features={71: old_feature, 72: new_feature},
    )

    assert payload["previous"]["revision_windows"][0]["pct_change"] == 1.2
    assert payload["current"]["revision_windows"][0]["pct_change"] == 4.6
    assert payload["current"]["revision_windows"][0]["net_breadth"] == 0.6
    assert payload["current"]["revision_windows"][0]["acceleration"] == 0.04
    assert payload["semantic"]["threshold"] == {"revision_pct_points": 2.0}


def _snapshot(
    snapshot_id: int,
    *,
    opportunity: float | None,
    posture: str,
) -> CeriScoreSnapshot:
    utc = ZoneInfo("UTC")
    snapshot = CeriScoreSnapshot(
        id=snapshot_id,
        company_id=42,
        ticker="TEST",
        as_of_session=date(2026, 8, 10 + snapshot_id),
        cutoff_at=datetime(2026, 8, 10 + snapshot_id, 21, tzinfo=utc),
        opportunity_score=opportunity,
        opportunity_coverage_pct=70.0 if opportunity is not None else 0.0,
        event_risk_score=0.0,
        data_confidence="Normal" if opportunity is not None else "Insufficient",
        coverage_pct=70.0 if opportunity is not None else 0.0,
        posture=posture,
        component_json={"components": []},
        event_risk_ledger_json={"accepted_evidence": True},
        config_version="test",
        config_hash="same-config",
        calculation_version="ceri-1.2.0",
        evidence_hash=f"evidence-{snapshot_id}",
    )
    snapshot.evidence_contract_version = "ceri-evidence-contract-v2"
    return snapshot


def _catalyst_revision(*, materiality: float | None, status: str) -> CeriCatalystEventRevision:
    return CeriCatalystEventRevision(
        id=11,
        catalyst_event_id=9,
        revision_number=1,
        is_current=True,
        status=status,
        direction="UNKNOWN",
        materiality=materiality,
        source_confidence="Normal",
        issuer_relevance=True,
        relevance_reason="PROVIDER_RELATED_TICKER_MATCH",
        effective_session=date(2026, 8, 14),
    )


def _change(change_type: str) -> CeriChangeEvent:
    return CeriChangeEvent(
        id=10,
        company_id=42,
        from_snapshot_id=19,
        to_snapshot_id=20,
        change_type=change_type,
        severity="NOTABLE",
        comparison_state="COMPARABLE",
        dedup_key=f"change-{change_type}",
    )


def _alert(alert_id: int, *, source_change_event_id: int | None = 10) -> CeriAlertEvent:
    return CeriAlertEvent(
        id=alert_id,
        source_change_event_id=source_change_event_id,
        event_key=f"alert-{alert_id}",
        ticker="TEST",
        severity="NOTABLE",
        status="UNREAD",
    )


class FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def scalar(self, _statement):
        return None

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        for index, row in enumerate(self.added, start=1):
            if getattr(row, "id", None) is None:
                row.id = index
