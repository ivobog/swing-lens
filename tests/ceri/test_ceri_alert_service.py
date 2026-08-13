from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriAlertRule,
    CeriChangeEvent,
    CeriScoreSnapshot,
)
from app.services.ceri.alert_service import CeriAlertService
from app.services.ceri.feature_flags import CeriFeatureFlags


def test_alert_rebuild_emits_one_alert_for_same_change_under_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.ceri.alert_service.ceri_flags",
        lambda: CeriFeatureFlags(True, True, True, True, True, True, True),
    )
    change = CeriChangeEvent(
        id=3,
        company_id=42,
        change_type="NEW_BINARY_EVENT",
        severity="RISK",
        dedup_key="change-key",
        catalyst_revision_id=7,
        delta_json={"status": "SCHEDULED"},
    )
    db = FakeDb(scalar_queue=[None, None])
    service = CeriAlertService(alerts_enabled=True)

    first = service.rebuild_alerts(db, changes=[change], ticker_by_company={42: "MSFT"})
    rule = next(row for row in db.added if isinstance(row, CeriAlertRule))
    alert = next(row for row in db.added if isinstance(row, CeriAlertEvent))
    db.scalar_queue = [rule, alert]
    second = service.rebuild_alerts(db, changes=[change], ticker_by_company={42: "MSFT"})

    assert first.alerts == 1
    assert second.duplicates == 1
    assert alert.status == "UNREAD"
    assert change.delta_json == {"status": "SCHEDULED"}


def test_alert_state_changes_do_not_mutate_source_change() -> None:
    alert = CeriAlertEvent(
        id=5,
        event_key="event-key",
        ticker="MSFT",
        severity="RISK",
        status="UNREAD",
    )
    change = CeriChangeEvent(
        id=3,
        company_id=42,
        change_type="NEW_BINARY_EVENT",
        severity="RISK",
        dedup_key="change-key",
        delta_json={"status": "SCHEDULED"},
    )
    db = FakeDb()

    CeriAlertService(alerts_enabled=True).acknowledge(db, alert)

    assert alert.status == "ACKNOWLEDGED"
    assert change.delta_json == {"status": "SCHEDULED"}


def test_unrated_insufficient_opportunity_change_cannot_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_alerts(monkeypatch)
    snapshot = _snapshot(opportunity=None, coverage=0.0, confidence="Insufficient")
    change = CeriChangeEvent(
        id=3,
        company_id=42,
        from_snapshot_id=1,
        to_snapshot_id=snapshot.id,
        change_type="OPPORTUNITY_UPGRADED",
        severity="NOTABLE",
        dedup_key="false-upgrade",
        delta_json={"from": None, "to": None},
    )
    db = FakeDb(objects={(CeriScoreSnapshot, snapshot.id): snapshot})

    result = CeriAlertService(alerts_enabled=True).rebuild_alerts(
        db, changes=[change], ticker_by_company={42: "TEST"}
    )

    assert result.alerts == 0
    assert result.skipped == 1
    assert not any(isinstance(row, CeriAlertEvent) for row in db.added)


def test_accepted_risk_change_can_alert_when_opportunity_is_unrated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_alerts(monkeypatch)
    snapshot = _snapshot(opportunity=None, coverage=0.0, confidence="Insufficient")
    snapshot.event_risk_score = 5.0
    snapshot.event_risk_ledger_json = {
        "accepted_evidence": True,
        "components": [
            {
                "component": "earnings_proximity_risk",
                "score": 5.0,
                "reason": "accepted_report_date",
            }
        ]
    }
    change = CeriChangeEvent(
        id=4,
        company_id=42,
        from_snapshot_id=1,
        to_snapshot_id=snapshot.id,
        change_type="RISK_ESCALATED",
        severity="RISK",
        dedup_key="valid-risk-change",
        delta_json={"delta": 5.0, "prior_comparable": True, "accepted_evidence": True},
    )
    db = FakeDb(
        scalar_queue=[None, None],
        objects={(CeriScoreSnapshot, snapshot.id): snapshot},
    )

    result = CeriAlertService(alerts_enabled=True).rebuild_alerts(
        db, changes=[change], ticker_by_company={42: "TEST"}
    )

    assert result.alerts == 1


def _enable_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.ceri.alert_service.ceri_flags",
        lambda: CeriFeatureFlags(True, True, True, True, True, True, True),
    )


def _snapshot(
    *, opportunity: float | None, coverage: float, confidence: str
) -> CeriScoreSnapshot:
    utc = ZoneInfo("UTC")
    return CeriScoreSnapshot(
        id=2,
        company_id=42,
        ticker="TEST",
        as_of_session=date(2026, 8, 13),
        cutoff_at=datetime(2026, 8, 13, 21, tzinfo=utc),
        opportunity_score=opportunity,
        opportunity_coverage_pct=coverage,
        event_risk_score=0.0,
        data_confidence=confidence,
        coverage_pct=0.0,
        posture="Unrated" if opportunity is None else "Improving",
        component_json={"components": []},
        config_version="test",
        config_hash="hash",
        calculation_version="ceri-1.2.0",
        evidence_hash="fixture",
    )


class FakeDb:
    def __init__(self, scalar_queue=None, objects=None) -> None:
        self.scalar_queue = list(scalar_queue or [])
        self.objects = dict(objects or {})
        self.added = []
        self.next_id = 1

    def scalar(self, _statement):
        if self.scalar_queue:
            return self.scalar_queue.pop(0)
        return None

    def add(self, row) -> None:
        self.added.append(row)

    def get(self, model, identifier):
        return self.objects.get((model, identifier))

    def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1
