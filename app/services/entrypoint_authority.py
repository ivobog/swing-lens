"""Intentional caller errors for operations without a supported authority contract."""

from __future__ import annotations


class EntryPointAuthorityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def reject_unbound_standalone(operation: str) -> None:
    raise EntryPointAuthorityError(
        "STANDALONE_MUTATION_RETIRED",
        f"Unbound {operation} mutation is retired. Start a new Full Pipeline calculation "
        "to freeze source, session, configuration and execution authority. "
        "This endpoint cannot continue an existing calculation.",
    )


def reject_reduced_pipeline() -> None:
    raise EntryPointAuthorityError(
        "REDUCED_PIPELINE_RETIRED",
        "The non-durable business pipeline is retired. Enable the durable pipeline and "
        "its worker to start a new calculation.",
    )


def reject_legacy_mutation(operation: str) -> None:
    raise EntryPointAuthorityError(
        "LEGACY_MUTATION_RETIRED",
        f"The historical {operation} write operation is retired. Its schema-specific "
        "approval manifest does not grant current mutation authority. Use the certified "
        "calculation, maturation or publication service for new work.",
    )


def require_disposable_tool_target(database_url) -> None:
    """Bound historical QA tools before a connection or provider request is made."""
    import os

    from sqlalchemy.engine import make_url

    target = make_url(database_url)
    if (
        os.environ.get("SWINGLENS_DATABASE_SAFETY_CONTEXT") != "DISPOSABLE_TEST"
        or not target.drivername.startswith("postgresql")
        or target.host not in {"localhost", "127.0.0.1", "::1"}
        or not (target.database or "").startswith(
            ("swinglens_pytest_", "swinglens_qa_", "swinglens_obs_cert_", "swinglens_ci_")
        )
    ):
        raise EntryPointAuthorityError(
            "LEGACY_TOOL_DISPOSABLE_DATABASE_REQUIRED",
            "This historical QA tool is noncertified and requires an explicitly named "
            "local disposable test database and DISPOSABLE_TEST safety context.",
        )
