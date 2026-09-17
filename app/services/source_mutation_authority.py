"""Native acquisition/normalization declarations at reviewed source writers.

Source observations are newly acquired facts, not financial Calculation Identity.
Existing stored row arguments are checked against their exact database addresses.
Operational ownership remains separate from provider and request semantics.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import wraps
from inspect import signature

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, object_session

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.domain_mutation import (
    DomainMutationContext,
    MutationEntryPointDescriptor,
    MutationEvidenceReference,
    MutationSemanticMode,
    MutationWriterDescriptor,
    fence_mutation_transaction,
)
from app.services.domain_write_fence import (
    DomainWriteOwnership,
    current_domain_write_ownership,
    fence_domain_commits,
)

_OPERATIONAL_ARGUMENTS = {
    "db",
    "self",
    "execution_token",
    "job_id",
    "calculated_at",
    "now",
    "settings",
    "config",
    "client",
    "connection",
    "ib",
    "ib_factory",
    "provider",
    "progress_callback",
    "point_in_time_query",  # Algorithm object; the context already pins its rows and cutoff.
}

_active_source_writers = ContextVar("source_mutation_transactions", default=())


def source_writer_member(owner):
    """Private mechanisms inherit the declaring source transaction."""

    def adopt(mechanism):
        parameters = signature(mechanism)

        @wraps(mechanism)
        def guarded(*args, **kwargs):
            arguments = parameters.bind(*args, **kwargs).arguments
            db = arguments.get("db")
            if db is None:
                for value in arguments.values():
                    state = inspect(value, raiseerr=False)
                    if state is not None and hasattr(state, "mapper"):
                        db = object_session(value)
                        if db is not None:
                            break
            if isinstance(db, Session) and owner not in _active_source_writers.get():
                db.rollback()
                raise ValueError("MUTATION_SOURCE_SEMANTIC_TRANSACTION_REQUIRED")
            return mechanism(*args, **kwargs)

        return guarded

    return adopt


def _source_value(db, value):
    state = inspect(value, raiseerr=False)
    if state is not None and hasattr(state, "mapper"):
        columns = list(state.mapper.columns)
        primary = list(state.mapper.primary_key)
        addresses = [getattr(value, column.key) for column in primary]

        def source_columns(values):
            # SQLite drops offsets from explicitly UTC timezone-aware columns.
            return {
                column.key: (
                    item.replace(tzinfo=UTC)
                    if isinstance(item, datetime)
                    and item.tzinfo is None
                    and getattr(column.type, "timezone", False)
                    else item
                )
                for column in columns
                for item in (values[column.key],)
            }

        native = source_columns({column.key: getattr(value, column.key) for column in columns})
        if addresses and all(address is not None for address in addresses):
            stored = (
                db.execute(
                    select(*columns)
                    .where(
                        *[
                            column == address
                            for column, address in zip(primary, addresses, strict=True)
                        ]
                    )
                    .with_for_update()
                    .execution_options(t14b_source_authority=True)
                )
                .mappings()
                .one_or_none()
            )
            if stored is None:
                raise ValueError("MUTATION_SOURCE_RECORD_MISSING: " + state.mapper.local_table.name)
            # A caller's unflushed source changes are not authoritative input.
            if not _active_source_writers.get() and Canonical.fingerprint(
                source_columns(stored)
            ) != Canonical.fingerprint(native):
                raise ValueError(
                    "MUTATION_SOURCE_RECORD_ARGUMENT_MISMATCH: " + state.mapper.local_table.name
                )
        if state.mapper.local_table.name == "background_jobs":
            native = {
                key: native[key] for key in ("id", "job_type", "related_run_id", "payload_json")
            }
        return {"table": state.mapper.local_table.name, "state": native}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _source_value(db, getattr(value, field.name))
            for field in fields(value)
            if field.name not in _OPERATIONAL_ARGUMENTS
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _source_value(db, item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_source_value(db, item) for item in value]
    if isinstance(value, set):
        return sorted((_source_value(db, item) for item in value), key=Canonical.dumps)
    if hasattr(value, "columns") and hasattr(value, "to_dict"):
        return {"frame": value.to_dict(orient="records")}
    return value


def source_mutation_writer(domain, role, *, mode=MutationSemanticMode.CANONICAL_CALCULATION):
    """Use the existing domain policy, binding the writer's concrete native input.

    The optional declaration is checked rather than trusted. With no declaration,
    the source writer declares its own explicit incoming observation/request. It
    never fills missing financial authority or selects a current/latest record.
    """

    def adopt(writer):
        parameters = signature(writer)
        owner = writer.__module__ + ":" + writer.__qualname__

        @wraps(writer)
        def guarded(*args, **kwargs):
            supplied = kwargs.pop("mutation_context", None)
            arguments = parameters.bind(*args, **kwargs)
            arguments.apply_defaults()
            db = arguments.arguments.get("db")
            if db is None:
                for value in arguments.arguments.values():
                    state = inspect(value, raiseerr=False)
                    if state is not None and hasattr(state, "mapper"):
                        db = object_session(value)
                        if db is not None:
                            break
            if not isinstance(db, Session):
                return writer(*args, **kwargs)
            try:
                with db.no_autoflush:
                    manifest = {
                        "writer": owner,
                        "native_source": {
                            key: _source_value(db, value)
                            for key, value in arguments.arguments.items()
                            if key not in _OPERATIONAL_ARGUMENTS
                        },
                    }
                    digest = Canonical.fingerprint(manifest)
                    ownership = current_domain_write_ownership()
                    job = arguments.arguments.get("job")
                    if job is not None and getattr(job, "execution_token", None):
                        explicit = DomainWriteOwnership(job.id, job.execution_token)
                        if ownership is not None and ownership != explicit:
                            raise ValueError("MUTATION_SOURCE_JOB_ATTEMPT_MISMATCH")
                        ownership = explicit
                    expected = DomainMutationContext(
                        domain=domain,
                        semantic_mode=mode,
                        entrypoint=MutationEntryPointDescriptor(owner, "NATIVE_SOURCE_WRITER"),
                        writer=MutationWriterDescriptor(owner, "phase5-source-writer-v1", domain),
                        reason="Persist explicit acquisition/normalization inputs",
                        evidence=(
                            MutationEvidenceReference(
                                role, "native_request_manifest", digest, digest
                            ),
                        ),
                        execution=ownership,
                        durable=ownership is not None,
                    )
                    context = expected if supplied is None else supplied
                    if (
                        not isinstance(context, DomainMutationContext)
                        or context.domain is not domain
                        or context.semantic_mode is not mode
                        or context.writer != expected.writer
                        or context.evidence != expected.evidence
                    ):
                        raise ValueError("MUTATION_SOURCE_DECLARATION_MISMATCH")
                    if ownership is not None and context.execution != ownership:
                        raise ValueError("MUTATION_SOURCE_OWNERSHIP_MISMATCH")
                    fence_mutation_transaction(db, context)
            except Exception:
                db.rollback()
                raise
            execution = context.execution
            token = _active_source_writers.set(_active_source_writers.get() + (owner,))
            try:
                with fence_domain_commits(
                    job_id=execution.job_id if execution else None,
                    execution_token=execution.execution_token if execution else None,
                ):
                    return writer(*args, **kwargs)
            except Exception:
                db.rollback()
                raise
            finally:
                _active_source_writers.reset(token)

        return guarded

    return adopt
