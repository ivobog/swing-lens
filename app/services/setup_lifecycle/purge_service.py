from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.setup_lifecycle.config import (
    SetupLifecycleConfig,
    load_setup_lifecycle_config,
)
from app.services.setup_lifecycle.repository import (
    PurgePreview,
    PurgeScope,
    SetupLifecycleRepository,
)


class SetupLifecyclePurgeError(ValueError):
    pass


@dataclass(frozen=True)
class SetupLifecyclePurgeExecuteRequest:
    preview: PurgePreview
    confirmation_token: str
    requester: str
    reason: str


class SetupLifecyclePurgeService:
    def __init__(
        self,
        *,
        config: SetupLifecycleConfig | None = None,
        repository: SetupLifecycleRepository | None = None,
    ) -> None:
        self.config = config or load_setup_lifecycle_config()
        self.repository = repository or SetupLifecycleRepository(config=self.config)

    def preview(self, db: Session, scope: PurgeScope) -> PurgePreview:
        return self.repository.preview_purge(db, scope)

    def execute(
        self,
        db: Session,
        request: SetupLifecyclePurgeExecuteRequest,
    ) -> dict[str, int]:
        self._validate_execute(request)
        if self.config.retention.purge_audit_required:
            self.repository.write_admin_audit_event(
                db,
                event_type="PURGE_EXECUTED",
                requester=request.requester,
                reason=request.reason,
                scope=_scope_payload(request.preview.scope),
                affected_counts=request.preview.counts,
                preview_token=request.preview.token,
            )
        return self.repository._execute_purge_unchecked(
            db,
            request.preview,
            request.confirmation_token,
        )

    def _validate_execute(self, request: SetupLifecyclePurgeExecuteRequest) -> None:
        if not self.config.retention.purge_enabled:
            raise SetupLifecyclePurgeError(
                "setup lifecycle purge is disabled by retention policy"
            )
        if self.config.retention.purge_preview_required and request.preview is None:
            raise SetupLifecyclePurgeError("setup lifecycle purge requires a preview")
        if self.config.retention.purge_confirmation_required:
            if not request.confirmation_token:
                raise SetupLifecyclePurgeError(
                    "setup lifecycle purge requires a confirmation token"
                )
            if request.confirmation_token != request.preview.token:
                raise SetupLifecyclePurgeError(
                    "setup lifecycle purge confirmation token does not match preview"
                )
        if self.config.retention.purge_audit_required:
            if not request.requester.strip():
                raise SetupLifecyclePurgeError("setup lifecycle purge requires requester")
            if not request.reason.strip():
                raise SetupLifecyclePurgeError("setup lifecycle purge requires reason")


def _scope_payload(scope: PurgeScope) -> dict[str, object]:
    return {
        "before_date": scope.before_date.isoformat() if scope.before_date else None,
        "ticker": scope.ticker,
        "evaluation_run_id": scope.evaluation_run_id,
    }
