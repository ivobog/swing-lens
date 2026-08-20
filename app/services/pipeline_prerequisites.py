from __future__ import annotations

from typing import Any


class PipelineBlockedError(RuntimeError):
    """A deterministic prerequisite prevents execution and must not be retried."""

    reason_code = "PIPELINE_PREREQUISITE_BLOCKED"

    def __init__(
        self,
        message: str,
        *,
        reason_code: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code or type(self).reason_code
        self.diagnostics = diagnostics or {}


class CeriBootstrapRequiredError(PipelineBlockedError):
    """The ACTIVE SEC signature is not ready for the requested ticker universe."""

    reason_code = "SEC_BOOTSTRAP_REQUIRED"


class CeriProviderConfigurationError(PipelineBlockedError):
    """Required deterministic CERI/SEC provider configuration is invalid."""

    reason_code = "CERI_PROVIDER_CONFIGURATION_INVALID"


class CeriUpstreamStageBlockedError(PipelineBlockedError):
    """A terminal upstream CERI batch prevents a dependent stage from running."""

    reason_code = "CERI_UPSTREAM_STAGE_UNSUCCESSFUL"


class SecProcessorPromotionRequiredError(PipelineBlockedError):
    """The deployed SEC processor is not the explicitly promoted ACTIVE release."""

    reason_code = "SEC_PROCESSOR_PROMOTION_REQUIRED"


class WorkerProcessorDriftError(PipelineBlockedError):
    """A worker's loaded SEC processor is incompatible with the ACTIVE release."""

    reason_code = "WORKER_PROCESSOR_SIGNATURE_DRIFT"
