from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SecReadinessState(StrEnum):
    NO_CIK = "NO_CIK"
    BOOTSTRAP_REQUIRED = "BOOTSTRAP_REQUIRED"
    BOOTSTRAPPING = "BOOTSTRAPPING"
    READY = "READY"
    READY_TERMINAL_REUSE = "READY_TERMINAL_REUSE"
    FAILED = "FAILED"


class SecReadinessPolicy(StrEnum):
    REQUIRE_READY = "REQUIRE_READY"
    ALLOW_DEGRADED = "ALLOW_DEGRADED"


@dataclass(frozen=True)
class SecReadinessFacts:
    cik: str | None
    active: bool = False
    bootstrap_certified: bool = False
    bootstrap_in_progress: bool = False
    failed: bool = False
    prior_signature_certified: bool = False
    terminal_documents_reused: int = 0
    documents_downloaded: int = 0
    documents_extracted: int = 0


@dataclass(frozen=True)
class SecReadinessResult:
    state: SecReadinessState
    run_evidence_status: str
    may_ingest: bool
    reason: str


class SecReadinessService:
    """Maps durable SEC facts to explicit preflight and run evidence states."""

    def assess(
        self,
        facts: SecReadinessFacts,
        *,
        policy: SecReadinessPolicy = SecReadinessPolicy.REQUIRE_READY,
    ) -> SecReadinessResult:
        if not facts.cik:
            return self._unready(SecReadinessState.NO_CIK, policy, "CIK_UNRESOLVED")
        if facts.failed:
            return self._unready(SecReadinessState.FAILED, policy, "SEC_BOOTSTRAP_FAILED")
        if facts.bootstrap_in_progress:
            return self._unready(
                SecReadinessState.BOOTSTRAPPING, policy, "SEC_BOOTSTRAP_IN_PROGRESS"
            )
        if not facts.bootstrap_certified:
            reason = (
                "PROCESSOR_SIGNATURE_CHANGED"
                if facts.prior_signature_certified
                else "SEC_BOOTSTRAP_REQUIRED"
            )
            return self._unready(SecReadinessState.BOOTSTRAP_REQUIRED, policy, reason)
        if (
            facts.terminal_documents_reused > 0
            and facts.documents_downloaded == 0
            and facts.documents_extracted == 0
        ):
            return SecReadinessResult(
                SecReadinessState.READY_TERMINAL_REUSE,
                "READY",
                True,
                "TERMINAL_EXTRACTION_REUSED",
            )
        return SecReadinessResult(
            SecReadinessState.READY, "READY", True, "BOOTSTRAP_CERTIFIED"
        )

    @staticmethod
    def _unready(
        state: SecReadinessState,
        policy: SecReadinessPolicy,
        reason: str,
    ) -> SecReadinessResult:
        degraded = policy is SecReadinessPolicy.ALLOW_DEGRADED
        return SecReadinessResult(
            state=state,
            run_evidence_status="DEGRADED" if degraded else "REJECTED",
            may_ingest=degraded,
            reason=reason,
        )

    @staticmethod
    def aggregate_run_status(
        provider_statuses: list[str],
        *,
        dominant_provider_status: str | None,
        strict: bool,
    ) -> str:
        statuses = {str(value).upper() for value in provider_statuses}
        dominant = str(dominant_provider_status or "").upper()
        if dominant in {"DEGRADED", "REJECTED", "FAILED", "PARTIAL"}:
            return "REJECTED" if strict else "DEGRADED"
        if statuses & {"REJECTED", "FAILED"}:
            return "REJECTED"
        if statuses & {"DEGRADED", "PARTIAL"}:
            return "DEGRADED"
        return "READY"
