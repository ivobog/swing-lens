from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.security import ROUTE_CLASS_LOCAL_ADMIN, require_local_admin, unsafe_route
from app.services.ceri.feature_flags import ceri_flags
from app.services.ceri.provider_registry import CeriProviderRegistry, CeriProviderRegistryError
from app.services.ceri.validation_service import (
    DEFAULT_VALIDATION_SAMPLE,
    CeriProviderValidationService,
)


def _require_ceri_provider_ui(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None)
    if not ceri_flags(settings).ui:
        raise HTTPException(status_code=404, detail="CERI UI is disabled.")


router = APIRouter(tags=["ceri-providers"])


@router.get("/api/ceri/providers/health", dependencies=[Depends(_require_ceri_provider_ui)])
def ceri_provider_health() -> dict[str, Any]:
    registry = CeriProviderRegistry()
    providers = []
    for provider in registry.priority_order():
        try:
            health = asdict(registry.health(provider))
            capabilities = asdict(registry.capabilities(provider))
        except CeriProviderRegistryError as exc:
            providers.append(
                {
                    "provider": provider,
                    "healthy": False,
                    "checked_at": None,
                    "quota_status": None,
                    "message": str(exc),
                    "capabilities": [],
                    "datasets": [],
                    "error": {
                        "code": "PROVIDER_CAPABILITY_UNAVAILABLE",
                        "message": str(exc),
                    },
                }
            )
            continue
        health["capabilities"] = sorted(
            str(capability) for capability in capabilities["capabilities"]
        )
        health["datasets"] = sorted(str(dataset) for dataset in capabilities["datasets"])
        metadata = getattr(registry.get(provider), "safe_metadata", None)
        if callable(metadata):
            health["metadata"] = metadata()
        providers.append(health)
    return {"items": providers, "total": len(providers)}


@router.post("/api/ceri/providers/validate")
@unsafe_route(
    ROUTE_CLASS_LOCAL_ADMIN,
    reason="runs an explicit provider validation sample before live alert activation",
    csrf_required=True,
    local_admin_required=True,
)
def ceri_provider_validate(
    request: Request,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = getattr(request.app.state, "settings", None)
    flags = ceri_flags(settings)
    require_local_admin(
        request,
        enabled=flags.admin,
        disabled_message="CERI administration is disabled.",
        local_only_message="CERI administration is local-only.",
        csrf_message="CERI administration requires CSRF protection.",
        structured_code="ADMIN_FORBIDDEN",
        csrf_required=True,
    )
    payload = dict(payload or {})
    provider_name = str(payload.get("provider") or "eodhd").strip().lower()
    registry = CeriProviderRegistry()
    try:
        provider = registry.get(provider_name)
    except CeriProviderRegistryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    requested = payload.get("tickers")
    if requested is None:
        tickers = DEFAULT_VALIDATION_SAMPLE
    elif isinstance(requested, list):
        tickers = tuple(str(value) for value in requested)
    elif isinstance(requested, str):
        try:
            parsed = json.loads(requested)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="tickers must be a JSON array.") from exc
        if not isinstance(parsed, list):
            raise HTTPException(status_code=400, detail="tickers must be a JSON array.")
        tickers = tuple(str(value) for value in parsed)
    else:
        raise HTTPException(status_code=400, detail="tickers must be a JSON array.")
    summary = CeriProviderValidationService().validate(provider, tickers)
    return {
        "status": "READY" if summary.ready else "BLOCKED",
        "alerts_may_be_enabled": False,
        "summary": summary.as_dict(),
    }
