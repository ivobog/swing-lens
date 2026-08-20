from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.services.ceri.config import CeriConfigError, load_ceri_config
from app.services.ceri.enums import CeriDataset, CeriProvider, CeriProviderCapability
from app.services.ceri.sec.processor_lifecycle import require_deployed_processor_active
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness
from app.services.pipeline_prerequisites import (
    CeriBootstrapRequiredError,
    CeriProviderConfigurationError,
)


def validate_sec_pipeline_preflight(
    db: Session,
    *,
    tickers: Iterable[str],
) -> dict[str, Any]:
    try:
        config = load_ceri_config()
    except (CeriConfigError, OSError) as exc:
        raise CeriProviderConfigurationError(
            f"CERI provider configuration cannot be loaded: {exc}",
            diagnostics={"config_error": str(exc)},
        ) from exc
    guidance_policy = config.datasets.get(CeriDataset.GUIDANCE)
    sec_capabilities = config.providers.capabilities.get(CeriProvider.SEC, ())
    config_diagnostics = {
        "config_hash": config.config_hash,
        "guidance_dataset_enabled": bool(guidance_policy and guidance_policy.enabled),
        "sec_guidance_capability": (
            CeriProviderCapability.GUIDANCE in sec_capabilities
        ),
    }
    if not all(
        (
            config_diagnostics["guidance_dataset_enabled"],
            config_diagnostics["sec_guidance_capability"],
        )
    ):
        raise CeriProviderConfigurationError(
            "CERI SEC guidance provider configuration is not enabled and capable.",
            diagnostics={"provider_config": config_diagnostics},
        )
    lifecycle = require_deployed_processor_active(db)
    readiness = diagnose_sec_readiness(
        db,
        tickers=tickers,
        processor_signature=lifecycle.active_signature or lifecycle.deployed_signature,
    )
    diagnostics = {
        "provider_config": config_diagnostics,
        "processor": lifecycle.as_dict(),
        "readiness": readiness.as_dict(),
    }
    if not readiness.complete:
        preview = ", ".join(readiness.blocking_tickers[:25])
        suffix = "..." if len(readiness.blocking_tickers) > 25 else ""
        raise CeriBootstrapRequiredError(
            "SEC ACTIVE pipeline preflight blocked execution: "
            f"{readiness.ready_tickers}/{readiness.requested_tickers} tickers are accepted "
            f"for processor signature {readiness.processor_signature}; bootstrap or mapping "
            f"repair required for {preview}{suffix}.",
            diagnostics=diagnostics,
        )
    return diagnostics
