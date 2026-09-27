from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.settings import RuntimeMode


class MutationCapability(StrEnum):
    """The complete runtime classification vocabulary for mutation entrypoints."""

    NORMAL_ONLY = "NORMAL_ONLY"
    CERTIFICATION_CONTROL = "CERTIFICATION_CONTROL"
    CERTIFICATION_SESSION_SCOPED = "CERTIFICATION_SESSION_SCOPED"
    READ_ONLY = "READ_ONLY"


class RuntimeMutationAuthorityError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# These operations mutate runtime control state, not financial/domain state. The
# names are deliberately shared by HTTP, worker startup and supervisor recovery.
CERTIFICATION_CONTROL_MUTATIONS = frozenset(
    {
        "web_runtime",
        "worker_registration",
        "worker_heartbeat",
        "worker_control_loop_heartbeat",
        "supervisor_heartbeat",
        "transition_preflight_read_and_lock",
    }
)


@dataclass(frozen=True)
class RuntimeMutationAuthority:
    mode: RuntimeMode
    operation: str
    certification_session_id: str | None = None
    root_job_id: int | None = None
    root_correlation_id: str | None = None

    def __post_init__(self) -> None:
        operation = self.operation.strip()
        if not operation:
            raise RuntimeMutationAuthorityError(
                "RUNTIME_MUTATION_OPERATION_REQUIRED",
                "runtime mutation authority requires an explicit operation",
            )
        object.__setattr__(self, "operation", operation)
        if self.mode is RuntimeMode.NORMAL:
            if self.certification_session_id is not None:
                raise RuntimeMutationAuthorityError(
                    "NORMAL_RUNTIME_SESSION_FORBIDDEN",
                    "normal runtime authority cannot carry a certification session",
                )
            return
        session_id = str(self.certification_session_id or "").strip()
        if not session_id:
            raise RuntimeMutationAuthorityError(
                "CERTIFICATION_SESSION_REQUIRED",
                "certification mutation authority requires an explicit session",
            )
        object.__setattr__(self, "certification_session_id", session_id)

    @classmethod
    def normal(cls, operation: str) -> RuntimeMutationAuthority:
        return cls(mode=RuntimeMode.NORMAL, operation=operation)

    @classmethod
    def certification(
        cls,
        operation: str,
        *,
        session_id: str,
        root_job_id: int | None = None,
        root_correlation_id: str | None = None,
    ) -> RuntimeMutationAuthority:
        return cls(
            mode=RuntimeMode.CERTIFICATION,
            operation=operation,
            certification_session_id=session_id,
            root_job_id=root_job_id,
            root_correlation_id=root_correlation_id,
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        operation: str,
        supplied_certification_session_id: str | None = None,
        root_job_id: int | None = None,
        root_correlation_id: str | None = None,
    ) -> RuntimeMutationAuthority:
        mode = getattr(settings, "runtime_mode", RuntimeMode.NORMAL)
        if mode is RuntimeMode.NORMAL:
            return cls.normal(operation)
        expected = str(getattr(settings, "runtime_instance_id", None) or "").strip()
        supplied = str(supplied_certification_session_id or "").strip()
        if not supplied:
            raise RuntimeMutationAuthorityError(
                "CERTIFICATION_SESSION_REQUIRED",
                "the mutation request did not supply its certification session",
            )
        if not expected or supplied != expected:
            raise RuntimeMutationAuthorityError(
                "CERTIFICATION_SESSION_MISMATCH",
                "the mutation request does not belong to the active certification session",
            )
        return cls.certification(
            operation,
            session_id=supplied,
            root_job_id=root_job_id,
            root_correlation_id=root_correlation_id,
        )

    def require_capability(
        self,
        capability: MutationCapability,
        *,
        certification_root_creation: bool = False,
    ) -> None:
        if capability is MutationCapability.READ_ONLY:
            return
        if self.mode is RuntimeMode.NORMAL:
            return
        if capability is MutationCapability.NORMAL_ONLY:
            raise RuntimeMutationAuthorityError(
                "CERTIFICATION_MUTATION_FORBIDDEN",
                f"{self.operation} is not authorized during certification",
            )
        if capability is MutationCapability.CERTIFICATION_CONTROL:
            if self.operation not in CERTIFICATION_CONTROL_MUTATIONS:
                raise RuntimeMutationAuthorityError(
                    "CERTIFICATION_CONTROL_OPERATION_FORBIDDEN",
                    f"{self.operation} is not an approved certification control mutation",
                )
            return
        if capability is MutationCapability.CERTIFICATION_SESSION_SCOPED:
            if certification_root_creation:
                return
            if self.root_job_id is None and not self.root_correlation_id:
                raise RuntimeMutationAuthorityError(
                    "CERTIFICATION_LINEAGE_REQUIRED",
                    f"{self.operation} requires explicit certification root lineage",
                )
            return
        raise RuntimeMutationAuthorityError(
            "RUNTIME_MUTATION_CAPABILITY_UNKNOWN",
            f"{self.operation} has no recognized mutation capability",
        )


def require_runtime_mutation_authority(authority: object) -> RuntimeMutationAuthority:
    if not isinstance(authority, RuntimeMutationAuthority):
        raise RuntimeMutationAuthorityError(
            "RUNTIME_MUTATION_AUTHORITY_REQUIRED",
            "runtime mutation requires an explicit typed authority",
        )
    return authority


@dataclass(frozen=True)
class RecoveryAuthority:
    runtime: RuntimeMutationAuthority

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, RuntimeMutationAuthority):
            raise RuntimeMutationAuthorityError(
                "RECOVERY_AUTHORITY_REQUIRED",
                "recovery requires a typed runtime mutation authority",
            )

    @classmethod
    def normal(cls, operation: str) -> RecoveryAuthority:
        return cls(runtime=RuntimeMutationAuthority.normal(operation))

    @classmethod
    def certification(cls, operation: str, *, session_id: str) -> RecoveryAuthority:
        return cls(
            runtime=RuntimeMutationAuthority.certification(operation, session_id=session_id)
        )

    @property
    def is_certification(self) -> bool:
        return self.runtime.mode is RuntimeMode.CERTIFICATION

    @property
    def certification_session_id(self) -> str | None:
        return self.runtime.certification_session_id

    def require_normal_worker_recovery(self) -> None:
        if self.is_certification:
            raise RuntimeMutationAuthorityError(
                "CERTIFICATION_WORKER_RECOVERY_FORBIDDEN",
                "stale and abandoned worker recovery are disabled during certification",
            )


def recovery_authority(
    *,
    operation: str,
    certification_session_id: str | None,
) -> RecoveryAuthority:
    if certification_session_id is None:
        return RecoveryAuthority.normal(operation)
    return RecoveryAuthority.certification(operation, session_id=certification_session_id)


def require_recovery_authority(authority: object) -> RecoveryAuthority:
    if not isinstance(authority, RecoveryAuthority):
        raise RuntimeMutationAuthorityError(
            "RECOVERY_AUTHORITY_REQUIRED",
            "recovery requires an explicit typed authority",
        )
    return authority
