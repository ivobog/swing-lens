from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.purge_service import (
    SetupLifecyclePurgeError,
    SetupLifecyclePurgeExecuteRequest,
    SetupLifecyclePurgeService,
)
from app.services.setup_lifecycle.repository import PurgePreview, PurgeScope


def test_purge_service_rejects_execution_when_policy_disables_purge() -> None:
    repository = FakePurgeRepository()
    service = SetupLifecyclePurgeService(repository=repository)
    preview = PurgePreview(
        scope=PurgeScope(before_date=date(2026, 8, 1), ticker="MSFT"),
        token="preview-token",
        counts={"snapshots": 1},
    )

    with pytest.raises(SetupLifecyclePurgeError, match="disabled by retention policy"):
        service.execute(
            object(),  # type: ignore[arg-type]
            SetupLifecyclePurgeExecuteRequest(
                preview=preview,
                confirmation_token="preview-token",
                requester="local-admin",
                reason="retention test",
            ),
        )

    assert repository.deleted is False
    assert repository.audit_events == []


def test_purge_service_audits_and_deletes_when_policy_allows_purge() -> None:
    config = load_setup_lifecycle_config()
    enabled_config = replace(
        config,
        retention=replace(config.retention, purge_enabled=True),
    )
    repository = FakePurgeRepository()
    service = SetupLifecyclePurgeService(config=enabled_config, repository=repository)
    preview = service.preview(
        object(),  # type: ignore[arg-type]
        PurgeScope(before_date=date(2026, 8, 1), ticker="msft", evaluation_run_id=11),
    )

    deleted = service.execute(
        object(),  # type: ignore[arg-type]
        SetupLifecyclePurgeExecuteRequest(
            preview=preview,
            confirmation_token=preview.token,
            requester="local-admin",
            reason="operator-approved retention cleanup",
        ),
    )

    assert deleted == {"snapshots": 1}
    assert repository.deleted is True
    assert repository.audit_events[0]["event_type"] == "PURGE_EXECUTED"
    assert repository.audit_events[0]["requester"] == "local-admin"
    assert repository.audit_events[0]["scope"]["ticker"] == "msft"
    assert repository.audit_events[0]["affected_counts"] == {"snapshots": 1}
    assert repository.audit_events[0]["preview_token"] == preview.token


def test_purge_service_rejects_mismatched_confirmation_token() -> None:
    config = load_setup_lifecycle_config()
    enabled_config = replace(
        config,
        retention=replace(config.retention, purge_enabled=True),
    )
    repository = FakePurgeRepository()
    service = SetupLifecyclePurgeService(config=enabled_config, repository=repository)
    preview = PurgePreview(scope=PurgeScope(ticker="MSFT"), token="preview-token", counts={})

    with pytest.raises(SetupLifecyclePurgeError, match="does not match preview"):
        service.execute(
            object(),  # type: ignore[arg-type]
            SetupLifecyclePurgeExecuteRequest(
                preview=preview,
                confirmation_token="wrong",
                requester="local-admin",
                reason="retention test",
            ),
        )

    assert repository.deleted is False
    assert repository.audit_events == []


class FakePurgeRepository:
    def __init__(self) -> None:
        self.deleted = False
        self.audit_events: list[dict[str, object]] = []

    def preview_purge(self, _db, scope: PurgeScope) -> PurgePreview:
        return PurgePreview(scope=scope, token="preview-token", counts={"snapshots": 1})

    def write_admin_audit_event(self, _db, **kwargs):
        self.audit_events.append(kwargs)

    def _execute_purge_unchecked(self, _db, preview: PurgePreview, token: str) -> dict[str, int]:
        if token != preview.token:
            raise ValueError("purge preview token does not match")
        self.deleted = True
        return dict(preview.counts)
