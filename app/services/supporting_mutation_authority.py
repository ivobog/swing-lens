"""Declarations for T14D supporting operations with exact incoming native scope."""

from __future__ import annotations

from functools import wraps
from inspect import signature

from sqlalchemy.orm import Session

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.domain_mutation import (
    DomainMutationContext,
    MutationEntryPointDescriptor,
    MutationEvidenceReference,
    MutationWriterDescriptor,
    fence_mutation_transaction,
)
from app.services.domain_write_fence import current_domain_write_ownership
from app.services.source_mutation_authority import _source_value


def supporting_mutation_operation(domain, mode, roles):
    """Bind explicit request/retained targets without inventing financial identity.

    ORM arguments are compared to their exact SQL bodies before execution.
    The manifest is an operation declaration, never original financial evidence.
    Native scope, review, policy and monotonicity checks remain in the operation.
    """

    def adopt(operation):
        parameters = signature(operation)
        address = operation.__module__ + ":" + operation.__qualname__

        @wraps(operation)
        def guarded(*args, **kwargs):
            arguments = parameters.bind(*args, **kwargs)
            arguments.apply_defaults()
            db = arguments.arguments.get("db")
            if not isinstance(db, Session):
                return operation(*args, **kwargs)
            try:
                with db.no_autoflush:
                    manifest = {
                        "operation": address,
                        "mode": mode.value,
                        "native_scope": {
                            key: _source_value(db, value)
                            for key, value in arguments.arguments.items()
                            if key not in {"db", "self", "provider", "request"}
                        },
                    }
                    digest = Canonical.fingerprint(manifest)
                    ownership = current_domain_write_ownership()
                    context = DomainMutationContext(
                        domain=domain,
                        semantic_mode=mode,
                        entrypoint=MutationEntryPointDescriptor(address, "SUPPORTING_OPERATION"),
                        writer=MutationWriterDescriptor(address, "phase5-supporting-v1", domain),
                        reason="Explicit supporting operation; no original financial identity",
                        evidence=tuple(
                            MutationEvidenceReference(
                                role, "native_request_manifest", digest, digest
                            )
                            for role in roles
                        ),
                        execution=ownership,
                        durable=ownership is not None,
                    )
                    fence_mutation_transaction(db, context)
                return operation(*args, **kwargs)
            except Exception:
                db.rollback()
                raise

        return guarded

    return adopt
