from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import engine, get_db
from app.security import require_local_admin
from app.services.operations_service import OperationsService
from app.services.redaction import redact_sensitive
from app.templates import templates

router = APIRouter(tags=["operations"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/system/operations", response_class=HTMLResponse)
def operations_page(request: Request, db: DbSession) -> HTMLResponse:
    _require_operations_admin(request)
    snapshot = _operations_service(request).snapshot(db)
    return templates.TemplateResponse(
        request, "system_operations.html", {"active_nav": "operations", "snapshot": snapshot}
    )


@router.get("/api/system/operations")
def operations_api(request: Request, db: DbSession) -> dict:
    _require_operations_admin(request)
    return redact_sensitive(_operations_service(request).snapshot(db))


@router.get("/ops/system/causality/{root_correlation_id}")
@router.get("/api/system/operations/causality/{root_correlation_id}")
def causality_api(root_correlation_id: str, request: Request, db: DbSession) -> dict:
    _require_operations_admin(request)
    if len(root_correlation_id) > 128:
        raise HTTPException(status_code=400, detail="invalid root correlation ID")
    return redact_sensitive(_operations_service(request).causality_tree(db, root_correlation_id))


def _require_operations_admin(request: Request) -> None:
    require_local_admin(
        request,
        enabled=True,
        disabled_message="System Operations is disabled.",
        local_only_message="System Operations is available only to the local administrator.",
    )


def _operations_service(request: Request) -> OperationsService:
    service = getattr(request.app.state, "operations_service", None)
    if service is None:
        service = OperationsService(engine=engine, settings=request.app.state.settings)
        request.app.state.operations_service = service
    return service
