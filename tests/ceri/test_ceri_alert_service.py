from __future__ import annotations

from app.models.ceri_tables import CeriAlertEvent, CeriAlertRule, CeriChangeEvent
from app.services.ceri.alert_service import CeriAlertService


def test_alert_rebuild_emits_one_alert_for_same_change_under_rerun() -> None:
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


class FakeDb:
    def __init__(self, scalar_queue=None) -> None:
        self.scalar_queue = list(scalar_queue or [])
        self.added = []
        self.next_id = 1

    def scalar(self, _statement):
        if self.scalar_queue:
            return self.scalar_queue.pop(0)
        return None

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1
