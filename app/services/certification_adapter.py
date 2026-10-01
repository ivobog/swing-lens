from __future__ import annotations

import importlib
import os

from app.settings import RuntimeMode, Settings

CERTIFICATION_ADAPTER_ENV = "SWINGLENS_CERTIFICATION_ADAPTER_MODULE"
ALLOWED_CERTIFICATION_ADAPTERS = {"certification_server"}


def install_certification_adapter(settings: Settings) -> str | None:
    """Install deterministic external boundaries inside production children.

    The supervisor, web entrypoint, worker loop, queue, transactions, and all
    orchestration remain production code. Only an allowlisted adapter may
    replace external provider I/O, and only in an isolated CERTIFICATION epoch.
    """

    module_name = str(os.environ.get(CERTIFICATION_ADAPTER_ENV) or "").strip()
    if not module_name:
        return None
    if settings.runtime_mode is not RuntimeMode.CERTIFICATION:
        raise RuntimeError("CERTIFICATION_ADAPTER_REQUIRES_CERTIFICATION_RUNTIME")
    if module_name not in ALLOWED_CERTIFICATION_ADAPTERS:
        raise RuntimeError("CERTIFICATION_ADAPTER_NOT_ALLOWLISTED")
    importlib.import_module(module_name)
    return module_name
