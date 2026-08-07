from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from app.services.ceri.provider_registry import CeriProviderRegistry, CeriProviderRegistryError

router = APIRouter(tags=["ceri-providers"])


@router.get("/api/ceri/providers/health")
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
