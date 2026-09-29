"""Native acquisition/normalization declarations at reviewed source writers.

Source observations are newly acquired facts, not financial Calculation Identity.
Existing stored row arguments are checked against their exact database addresses.
Operational ownership remains separate from provider and request semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import wraps
from inspect import signature
from types import MappingProxyType
from weakref import WeakKeyDictionary, WeakSet

from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, object_session
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import (
    ReleaseSavepointClause,
    RollbackToSavepointClause,
    SavepointClause,
    TextClause,
)
from sqlalchemy.sql.selectable import Select

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
    deferred_execution_ownership_lock,
    fence_domain_commits,
    retained_execution_ownership_scope,
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
_prefetched_source_bodies = ContextVar("prefetched_source_bodies", default=None)
_locked_source_bundles = WeakKeyDictionary()

# Psycopg's extended-query protocol rejects more than 65,535 bind parameters.
# Keep substantial headroom for future non-identity predicates instead of
# treating the protocol ceiling as an available identity budget.
SOURCE_REFRESH_QUERY_PARAMETER_BUDGET = 50_000
_MUTABLE_SUPPORTING_SOURCE_COLUMNS = {
    "ceri_score_snapshots": frozenset({"comparison_state", "comparison_snapshot_id"}),
}


def source_refresh_identity_chunk_size(
    primary_key_width: int,
    *,
    additional_parameter_count: int = 0,
) -> int:
    """Return a safe deterministic identity count for one refresh statement."""

    if primary_key_width <= 0:
        raise ValueError("MUTATION_SOURCE_PRIMARY_KEY_REQUIRED")
    if additional_parameter_count < 0:
        raise ValueError("MUTATION_SOURCE_PARAMETER_COUNT_INVALID")
    available = SOURCE_REFRESH_QUERY_PARAMETER_BUDGET - additional_parameter_count
    if available < primary_key_width:
        raise ValueError("MUTATION_SOURCE_PARAMETER_BUDGET_EXHAUSTED")
    return available // primary_key_width


@event.listens_for(Engine, "after_cursor_execute")
def _invalidate_changed_source_bundles(conn, cursor, statement, parameters, context, executemany):
    bundles = _locked_source_bundles.get(conn)
    if not bundles:
        return
    expression = getattr(getattr(context, "compiled", None), "statement", None)
    if isinstance(expression, (SavepointClause, RollbackToSavepointClause, ReleaseSavepointClause)):
        return
    if isinstance(expression, TextClause) and expression.text in {
        "SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))",
        "SELECT pg_advisory_xact_lock(:scope)",
    }:
        # The native decision-scope lock neither changes a source body nor ends
        # its SQL transaction. Other raw SQL remains conservatively invalidating.
        return
    if isinstance(expression, Select) and not any(
        word in statement.upper() for word in ("UPDATE ", "INSERT ", "DELETE ")
    ):
        return
    for bundle in list(bundles):
        if isinstance(expression, (Update, Insert, Delete)):
            target = getattr(expression, "table", None)
            target_tables = {
                name
                for name in (
                    getattr(target, "name", None),
                    getattr(getattr(target, "original", None), "name", None),
                )
                if name is not None
            }
            if target_tables.isdisjoint(bundle.models):
                # SQLAlchemy ORM DML commonly uses an AnnotatedTable rather than
                # ``Table`` itself.  The compiled expression still names the
                # exact mutation target, so unrelated writes cannot invalidate
                # locks retained for a source table merely because a table name
                # appears elsewhere in generated SQL.
                continue
        if (
            isinstance(expression, Update)
            and getattr(expression.table, "name", None) == "ceri_score_snapshots"
            and not getattr(expression, "_independent_ctes", ())
            and not getattr(context.compiled.compile_state, "is_multitable", False)
            and statement.lstrip().upper().split(None, 1)[0] == "UPDATE"
        ):
            values = getattr(expression, "_values", None)
            ordered = getattr(expression, "_ordered_values", None)
            columns = (
                [getattr(key, "key", key) for key in values]
                if values
                else [getattr(key, "key", key) for key, _ in ordered]
                if ordered
                else [
                    key
                    for key in (getattr(context.compiled, "column_keys", None) or ())
                    if key in expression.table.c
                ]
            )
            columns = list(columns or ()) + [
                column.key
                for column in (
                    *getattr(context.compiled, "update_prefetch", ()),
                    *getattr(context.compiled, "postfetch", ()),
                )
            ]
            if columns and set(columns) <= _MUTABLE_SUPPORTING_SOURCE_COLUMNS[
                "ceri_score_snapshots"
            ]:
                # Native CERI change detection advances only these supporting
                # pointers. It cannot change the locked financial score/evidence
                # bodies; raw SQL, aliases and every other column invalidate.
                continue
        bundle._requires_revalidation = True


class _SourceBodyView(Mapping):
    """Read-only addresses; nested SQL values cannot alter the retained witness."""

    def __init__(self, bodies):
        self._bodies = bodies

    def __getitem__(self, key):
        return deepcopy(self._bodies[key])

    def __iter__(self):
        return iter(self._bodies)

    def __len__(self):
        return len(self._bodies)


class PrefetchedSourceBodies:
    """Exact SQL bodies locked by the batch's existing SELECTs, never a latest cache.

    A bundle belongs to one Session transaction. Commit/rollback invalidates its
    locks; reuse then resolves exact IDs again in bounded table batches. Native
    argument comparison still occurs in _source_value before admitting writes.
    """

    def __init__(self, db, *, expected_manifest=None):
        self._db = db
        self._transaction = None
        self._bodies = {}
        self._rows = {}
        self._models = {}
        self._sealed = False
        self._connection = None
        self._sql_transaction = None
        self._requires_revalidation = False
        self._body_fingerprint = None
        self._expected_manifest = expected_manifest
        self._expected_tables = set((expected_manifest or {}).get("tables") or [])
        self._expected_addresses = {}
        for entry in (expected_manifest or {}).get("entries") or []:
            table = str(entry["table"])
            self._expected_addresses.setdefault(table, set()).add(tuple(entry["address"]))

    @property
    def db(self):
        return self._db

    @property
    def transaction(self):
        return self._transaction

    @property
    def bodies(self):
        return _SourceBodyView(self._bodies)

    @property
    def models(self):
        return MappingProxyType(self._models)

    @property
    def body_fingerprint(self):
        if not self._sealed:
            raise ValueError("MUTATION_SOURCE_BUNDLE_NOT_SEALED")
        return self._body_fingerprint

    def durable_manifest(self):
        """Return the canonical, persistence-safe authority for this sealed bundle."""

        if not self._sealed:
            raise ValueError("MUTATION_SOURCE_BUNDLE_NOT_SEALED")
        entries = [
            {
                "table": table,
                "address": list(address),
                "row_fingerprint": Canonical.fingerprint(body),
            }
            for (table, address), body in sorted(
                self._bodies.items(), key=lambda item: str(item[0])
            )
        ]
        return {
            "manifest_version": "ceri-feature-source-manifest-v1",
            "tables": sorted(self._models),
            "entries": entries,
            "source_count": len(entries),
            "bundle_fingerprint": self._body_fingerprint,
        }

    def verify_durable_manifest(self, manifest):
        """Verify retry inputs before any calculation output can be produced."""

        current = self.durable_manifest()
        if Canonical.fingerprint(current) != Canonical.fingerprint(manifest):
            raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")

    def seal(self):
        self._body_fingerprint = Canonical.fingerprint(
            [
                {"table": table, "address": address, "body": body}
                for (table, address), body in sorted(self._bodies.items(), key=lambda x: str(x[0]))
            ]
        )
        self._sealed = True

    def assert_unchanged_in_memory(self):
        """Reject calculation code that dirties any sealed source ORM entity."""

        if not self._sealed:
            raise ValueError("MUTATION_SOURCE_BUNDLE_NOT_SEALED")
        for key, row in self._rows.items():
            excluded = _MUTABLE_SUPPORTING_SOURCE_COLUMNS.get(key[0], frozenset())
            columns = [
                column for column in inspect(type(row)).columns if column.key not in excluded
            ]
            current = {column.key: getattr(row, column.key) for column in columns}
            retained = {
                field: value for field, value in self._bodies[key].items() if field not in excluded
            }
            if Canonical.fingerprint(current) != Canonical.fingerprint(retained):
                raise ValueError("MUTATION_SOURCE_BUNDLE_CHANGED_IN_MEMORY: " + key[0])

    def load(self, model, statement):
        if self._sealed:
            raise ValueError("MUTATION_SOURCE_BUNDLE_FROZEN")
        columns = list(inspect(model).columns)
        primary = list(inspect(model).primary_key)
        table = inspect(model).local_table.name
        self._models[table] = model
        if self._expected_manifest is not None:
            if table not in self._expected_tables:
                raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")
            expected_addresses = self._expected_addresses.get(table, set())
            with self.db.no_autoflush:
                membership = self.db.execute(
                    statement.with_only_columns(*primary, maintain_column_froms=True)
                ).all()
            observed_addresses = {tuple(row) for row in membership}
            if observed_addresses != expected_addresses:
                raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")
            from sqlalchemy import tuple_

            statement = statement.where(tuple_(*primary).in_(tuple(expected_addresses)))
            with self.db.no_autoflush:
                result = self.db.execute(statement.add_columns(*columns).with_for_update()).all()
        else:
            with self.db.no_autoflush:
                result = self.db.execute(statement.add_columns(*columns).with_for_update()).all()
        connection = self.db.connection(bind_arguments={"mapper": model})
        if self._connection is not None and self._connection is not connection:
            raise ValueError("MUTATION_SOURCE_BUNDLE_CONNECTION_MISMATCH")
        self._connection = connection
        _locked_source_bundles.setdefault(connection, WeakSet()).add(self)
        self._sql_transaction = connection.get_transaction()
        self._transaction = self.db.get_transaction()
        rows = []
        for row in result:
            body = dict(zip((c.key for c in columns), row[1:], strict=True))
            address = tuple(body[c.key] for c in primary)
            self._bodies[(table, address)] = deepcopy(body)
            self._rows[(table, address)] = row[0]
            rows.append(row[0])
        return rows

    def refresh(self):
        if (
            not self._requires_revalidation
            and self.transaction is self.db.get_transaction()
            and self.transaction is not None
            and self._connection is not None
            and not self._connection.closed
            and not self._connection.invalidated
            and self._connection.get_transaction() is self._sql_transaction
            and self._sql_transaction is not None
            and self._sql_transaction.is_active
        ):
            return
        from sqlalchemy import tuple_

        # No ambient selectors: retain precisely the addresses admitted earlier.
        # All chunks use this Session's current transaction, preserving FOR UPDATE
        # ownership while keeping every statement below the driver parameter budget.
        expected_by_table = {}
        refreshed_by_table = {}
        with self.db.no_autoflush:
            for table, model in self.models.items():
                columns = list(inspect(model).columns)
                primary = list(inspect(model).primary_key)
                addresses = tuple(
                    sorted(
                        (key[1] for key in self.bodies if key[0] == table),
                        key=str,
                    )
                )
                if not addresses:
                    continue
                chunk_size = source_refresh_identity_chunk_size(len(primary))
                found = {}
                for start in range(0, len(addresses), chunk_size):
                    address_chunk = addresses[start : start + chunk_size]
                    retained = (
                        self.db.execute(
                            select(*columns)
                            .where(tuple_(*primary).in_(address_chunk))
                            .with_for_update()
                        )
                        .mappings()
                        .all()
                    )
                    for row in retained:
                        address = tuple(row[c.key] for c in primary)
                        found[address] = dict(row)
                expected_by_table[table] = addresses
                refreshed_by_table[table] = found

            # Validate only after every table and chunk was read successfully.
            # Until this completes the bundle remains marked for revalidation.
            for table, addresses in expected_by_table.items():
                found = refreshed_by_table[table]
                expected = set(addresses)
                observed = set(found)
                if expected - observed:
                    raise ValueError("MUTATION_SOURCE_RECORD_MISSING: " + table)
                if observed - expected:
                    raise ValueError("MUTATION_SOURCE_RECORD_UNEXPECTED: " + table)
                for address in addresses:
                    if Canonical.fingerprint(found[address]) != Canonical.fingerprint(
                        self.bodies[(table, address)]
                    ):
                        raise ValueError("MUTATION_SOURCE_BUNDLE_CHANGED: " + table)
        self._transaction = self.db.get_transaction()
        self._requires_revalidation = False
        if self.models:
            model = next(iter(self.models.values()))
            self._connection = self.db.connection(bind_arguments={"mapper": model})
            self._sql_transaction = self._connection.get_transaction()
            _locked_source_bundles.setdefault(self._connection, WeakSet()).add(self)


@contextmanager
def prefetched_source_scope(db, bundle, *, retain_execution_ownership: bool = True):
    if bundle is None or not isinstance(db, Session):
        yield
        return
    if not isinstance(bundle, PrefetchedSourceBodies) or bundle.db is not db:
        db.rollback()
        raise ValueError("MUTATION_SOURCE_BUNDLE_SESSION_MISMATCH")
    try:
        bundle.refresh()
        token = _prefetched_source_bodies.set(bundle)
        try:
            ownership_scope = (
                retained_execution_ownership_scope(db)
                if retain_execution_ownership
                else deferred_execution_ownership_lock()
            )
            with ownership_scope:
                yield
                bundle.assert_unchanged_in_memory()
                # A direct SQL write can leave ORM instances looking clean.
                # Revalidate the exact sealed addresses before the caller is
                # allowed to cross its durable commit boundary.
                bundle.refresh()
        finally:
            _prefetched_source_bodies.reset(token)
    except Exception:
        db.rollback()
        raise


def prefetched_source_body(db, model, source_id):
    """Use SQL truth only inside its admitted Session/transaction scope."""
    bundle = _prefetched_source_bodies.get()
    if bundle is None:
        return None
    if bundle.db is not db:
        raise ValueError("MUTATION_SOURCE_BUNDLE_SESSION_MISMATCH")
    bundle.refresh()
    table = inspect(model).local_table.name
    if table not in bundle.models:
        return None
    address = (table, (source_id,))
    if bundle.models.get(table) is not model or address not in bundle.bodies:
        raise ValueError("MUTATION_SOURCE_RECORD_MISSING: " + table)
    return bundle.bodies[address]


def prefetched_source_rows(db, model, source_ids):
    """Exact locked IDs; validate mutable ORM arguments against the SQL witness."""
    bundle = _prefetched_source_bodies.get()
    if bundle is None or inspect(model).local_table.name not in bundle.models:
        return None
    if bundle.db is not db:
        raise ValueError("MUTATION_SOURCE_BUNDLE_SESSION_MISMATCH")
    bundle.refresh()
    table = inspect(model).local_table.name
    rows = []
    for source_id in sorted(set(source_ids)):
        key = (table, (source_id,))
        if key not in bundle._rows:
            raise ValueError("MUTATION_SOURCE_RECORD_MISSING: " + table)
        row = bundle._rows[key]
        _source_value(db, row)
        rows.append(row)
    return rows


def prefetched_source_related_rows(db, model, field, values):
    """A narrow retained-column membership predicate, not a SQL evaluator."""
    bundle = _prefetched_source_bodies.get()
    table = inspect(model).local_table.name
    if bundle is None or table not in bundle.models:
        return None
    if field not in inspect(model).columns:
        raise ValueError("MUTATION_SOURCE_BUNDLE_UNKNOWN_FIELD")
    if bundle.db is not db:
        raise ValueError("MUTATION_SOURCE_BUNDLE_SESSION_MISMATCH")
    bundle.refresh()
    wanted = set(values)
    source_ids = [
        address[0]
        for (row_table, address), body in bundle._bodies.items()
        if row_table == table and body[field] in wanted
    ]
    return prefetched_source_rows(db, model, source_ids)


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
    if isinstance(value, PrefetchedSourceBodies):
        if value.db is not db or _prefetched_source_bodies.get() is not value:
            raise ValueError("MUTATION_SOURCE_BUNDLE_SCOPE_REQUIRED")
        value.refresh()
        if not value._sealed:
            raise ValueError("MUTATION_SOURCE_BUNDLE_NOT_SEALED")
        return {
            "exact_prefetched_source_body_fingerprint": value._body_fingerprint,
            "exact_prefetched_source_count": len(value._bodies),
        }
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
            bundle = _prefetched_source_bodies.get()
            key = (state.mapper.local_table.name, tuple(addresses))
            stored = None
            if bundle is not None and bundle.db is db:
                bundle.refresh()
                stored = bundle.bodies.get(key)
            if stored is None:
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
            if state.mapper.local_table.name == "price_bars" and hasattr(
                value, "_pit_projection_as_of"
            ):
                from app.models.tables import PriceBar
                from app.services.price_bar_repository import project_price_bar_rows_as_of

                as_of = value._pit_projection_as_of
                revision_id = getattr(value, "_pit_projection_revision_id", None)
                if (
                    not state.transient
                    or not isinstance(as_of, datetime)
                    or as_of.tzinfo is None
                    or not isinstance(revision_id, int)
                    or stored["created_at"] > as_of
                    or stored["first_seen_at"] > as_of
                ):
                    raise ValueError("MUTATION_SOURCE_PIT_PROJECTION_REQUIRED")
                replay = project_price_bar_rows_as_of(db, [PriceBar(**dict(stored))], as_of=as_of)
                if (
                    len(replay) != 1
                    or getattr(replay[0], "_pit_projection_revision_id", None) != revision_id
                    or Canonical.fingerprint(
                        source_columns(
                            {column.key: getattr(replay[0], column.key) for column in columns}
                        )
                    )
                    != Canonical.fingerprint(native)
                ):
                    raise ValueError("MUTATION_SOURCE_PIT_PROJECTION_MISMATCH")
                return {
                    "table": "price_bars",
                    "state": native,
                    "pit_projection": {"as_of": as_of, "revision_id": revision_id},
                }
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
                    db,
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
