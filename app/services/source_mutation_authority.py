"""Native acquisition/normalization declarations at reviewed source writers.

Source observations are newly acquired facts, not financial Calculation Identity.
Existing stored row arguments are checked against their exact database addresses.
Operational ownership remains separate from provider and request semantics.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import cache, wraps
from inspect import signature
from time import perf_counter_ns, process_time_ns
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
_writer_telemetry_frames = ContextVar("source_mutation_writer_telemetry_frames", default=())
_source_value_telemetry_frames = ContextVar("source_value_telemetry_frames", default=())
_reuse_sealed_writer_values = ContextVar("reuse_sealed_writer_values", default=True)
_compare_writer_manifest_paths = ContextVar("compare_writer_manifest_paths", default=False)
_compare_writer_fingerprint_paths = ContextVar("compare_writer_fingerprint_paths", default=False)
_stream_writer_fingerprints = ContextVar("stream_writer_fingerprints", default=True)
_reuse_stable_writer_containers = ContextVar("reuse_stable_writer_containers", default=True)
_prefetched_source_bodies = ContextVar("prefetched_source_bodies", default=None)
_locked_source_bundles = WeakKeyDictionary()

# Psycopg's extended-query protocol rejects more than 65,535 bind parameters.
# Keep substantial headroom for future non-identity predicates instead of
# treating the protocol ceiling as an available identity budget.
SOURCE_REFRESH_QUERY_PARAMETER_BUDGET = 50_000
_MUTABLE_SUPPORTING_SOURCE_COLUMNS = {
    "ceri_score_snapshots": frozenset({"comparison_state", "comparison_snapshot_id"}),
}


@event.listens_for(Engine, "before_cursor_execute")
def _time_source_writer_sql(conn, cursor, statement, parameters, context, executemany):
    if _writer_telemetry_frames.get():
        context._source_writer_sql_started = (perf_counter_ns(), process_time_ns())


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
    sql_started = getattr(context, "_source_writer_sql_started", None)
    if sql_started is not None:
        wall_started, cpu_started = sql_started
        _observe_active_writer("writer_sql_count")
        _observe_active_writer(
            "writer_sql_ms", (perf_counter_ns() - wall_started) / 1_000_000
        )
        _observe_active_writer(
            "writer_sql_cpu_ms", (process_time_ns() - cpu_started) / 1_000_000
        )
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
            if (
                columns
                and set(columns) <= _MUTABLE_SUPPORTING_SOURCE_COLUMNS["ceri_score_snapshots"]
            ):
                # Native CERI change detection advances only these supporting
                # pointers. It cannot change the locked financial score/evidence
                # bodies; raw SQL, aliases and every other column invalidate.
                continue
        if isinstance(expression, (Update, Insert, Delete)):
            cause = "RETAINED_SOURCE_TABLE_DML"
        elif isinstance(expression, TextClause):
            cause = "UNKNOWN_RAW_SQL"
        elif isinstance(expression, Select):
            cause = "AMBIGUOUS_SELECT_DML"
        else:
            cause = "UNKNOWN_SQL"
        bundle._require_revalidation(cause)


@event.listens_for(Session, "before_flush")
def _time_source_writer_flush_start(session, flush_context, instances):
    if _writer_telemetry_frames.get():
        session.info["_source_writer_flush_started"] = (
            perf_counter_ns(),
            process_time_ns(),
        )


@event.listens_for(Session, "after_flush")
def _time_source_writer_flush_end(session, flush_context):
    started = session.info.pop("_source_writer_flush_started", None)
    if started is not None:
        wall_started, cpu_started = started
        _observe_active_writer("writer_flush_count")
        _observe_active_writer(
            "writer_flush_ms", (perf_counter_ns() - wall_started) / 1_000_000
        )
        _observe_active_writer(
            "writer_flush_cpu_ms", (process_time_ns() - cpu_started) / 1_000_000
        )


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
    """Exact SQL bodies protected by compatible reader locks, never a latest cache.

    A bundle belongs to one Session transaction. Commit/rollback invalidates its
    locks; reuse then resolves exact IDs again in bounded table batches.  PostgreSQL
    ``FOR SHARE OF <source table>`` lets independent evidence readers coexist while
    still blocking UPDATE/DELETE of every admitted body until publication.  Native
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
        self._row_keys_by_identity = {}
        self._writer_source_values = {}
        self._writer_canonical_values = {}
        self._writer_canonical_fragments = {}
        self._writer_stable_container_values = {}
        self._invalidation_causes = Counter()
        self._writer_by_owner = {}
        self._writer_top_invocations = []
        self._writer_invocations = []
        self._telemetry = {
            "loaded_rows": 0,
            "load_calls": 0,
            "load_ms": 0.0,
            "seal_calls": 0,
            "seal_ms": 0.0,
            "manifest_calls": 0,
            "manifest_ms": 0.0,
            "assert_calls": 0,
            "assert_rows": 0,
            "assert_ms": 0.0,
            "assert_dirty_rows": 0,
            "assert_clean_noop_calls": 0,
            "full_audit_calls": 0,
            "full_audit_rows": 0,
            "full_audit_ms": 0.0,
            "invalidation_total": 0,
            "refresh_calls": 0,
            "refresh_noop_calls": 0,
            "refresh_executed_calls": 0,
            "refresh_rows": 0,
            "refresh_sql_ms": 0.0,
            "refresh_validation_ms": 0.0,
            "refresh_total_ms": 0.0,
            "scope_calls": 0,
            "scope_entry_refresh_ms": 0.0,
            "scope_body_ms": 0.0,
            "scope_exit_assert_ms": 0.0,
            "scope_exit_refresh_ms": 0.0,
            "writer_calls": 0,
            "writer_max_nesting_depth": 0,
            "writer_wrapper_inclusive_ms": 0.0,
            "writer_wrapper_exclusive_ms": 0.0,
            "writer_wrapper_inclusive_cpu_ms": 0.0,
            "writer_wrapper_exclusive_cpu_ms": 0.0,
            "writer_native_source_ms": 0.0,
            "writer_native_source_cpu_ms": 0.0,
            "writer_fingerprint_ms": 0.0,
            "writer_fingerprint_cpu_ms": 0.0,
            "writer_canonical_bytes": 0,
            "writer_values_visited": 0,
            "writer_source_value_exclusive_ms": 0.0,
            "writer_source_value_exclusive_cpu_ms": 0.0,
            "writer_authority_guard_calls": 0,
            "writer_authority_guard_ms": 0.0,
            "writer_authority_guard_cpu_ms": 0.0,
            "writer_source_body_lookups": 0,
            "writer_retained_fingerprint_calls": 0,
            "writer_manifest_ms": 0.0,
            "writer_manifest_cpu_ms": 0.0,
            "writer_fence_ms": 0.0,
            "writer_fence_cpu_ms": 0.0,
            "writer_declaration_ms": 0.0,
            "writer_declaration_cpu_ms": 0.0,
            "writer_body_ms": 0.0,
            "writer_body_exclusive_ms": 0.0,
            "writer_body_cpu_ms": 0.0,
            "writer_body_exclusive_cpu_ms": 0.0,
            "writer_sql_count": 0,
            "writer_sql_ms": 0.0,
            "writer_sql_cpu_ms": 0.0,
            "writer_flush_count": 0,
            "writer_flush_ms": 0.0,
            "writer_flush_cpu_ms": 0.0,
            "writer_source_value_cache_hits": 0,
            "writer_source_value_cache_misses": 0,
            "writer_canonical_subtree_hits": 0,
            "writer_canonical_subtree_misses": 0,
            "writer_source_value_dataframe_rows": 0,
            "writer_canonical_chunks_emitted": 0,
            "writer_streaming_fingerprint_calls": 0,
            "writer_legacy_fingerprint_calls": 0,
            "writer_stable_fragment_hits": 0,
            "writer_stable_fragment_misses": 0,
            "writer_stable_fragment_byte_reuse": 0,
            "writer_serialization_wall_ms": 0.0,
            "writer_serialization_cpu_ms": 0.0,
            "writer_hashing_wall_ms": 0.0,
            "writer_hashing_cpu_ms": 0.0,
            "writer_hash_update_calls": 0,
            "writer_largest_document_bytes": 0,
            "writer_stable_container_hits": 0,
            "writer_stable_container_misses": 0,
            "writer_stable_container_rows_validated": 0,
        }
        for entry in (expected_manifest or {}).get("entries") or []:
            table = str(entry["table"])
            address = tuple(entry["address"])
            addresses = self._expected_addresses.setdefault(table, set())
            if address in addresses:
                raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")
            addresses.add(address)

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

    def telemetry_snapshot(self):
        """Return low-cardinality timing totals for the owning batch/job."""

        snapshot = {
            key: round(value, 3) if isinstance(value, float) else value
            for key, value in self._telemetry.items()
        }
        snapshot["invalidation_causes"] = dict(sorted(self._invalidation_causes.items()))
        snapshot["writer_by_owner"] = {
            owner: {
                key: round(value, 3) if isinstance(value, float) else value
                for key, value in values.items()
            }
            for owner, values in sorted(self._writer_by_owner.items())
        }
        snapshot["writer_top_invocations"] = list(self._writer_top_invocations)
        snapshot["writer_invocations"] = list(self._writer_invocations)
        snapshot["writer_fragment_cache_entries"] = len(self._writer_canonical_fragments)
        snapshot["writer_fragment_cache_bytes"] = sum(
            len(fragment) for fragment in self._writer_canonical_fragments.values()
        )
        snapshot["writer_stable_container_cache_entries"] = len(
            self._writer_stable_container_values
        )
        return snapshot

    def _observe(self, key, value=1):
        self._telemetry[key] = self._telemetry.get(key, 0) + value

    def _require_revalidation(self, cause: str) -> None:
        """Enter the fail-closed SQL revalidation state with bounded telemetry."""

        self._requires_revalidation = True
        self._writer_source_values.clear()
        self._writer_canonical_values.clear()
        self._writer_canonical_fragments.clear()
        self._writer_stable_container_values.clear()
        self._invalidation_causes[cause] += 1
        self._observe("invalidation_total")

    def durable_manifest(self):
        """Return the canonical, persistence-safe authority for this sealed bundle."""

        started = perf_counter_ns()
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
        result = {
            "manifest_version": "ceri-feature-source-manifest-v1",
            "tables": sorted(self._models),
            "entries": entries,
            "source_count": len(entries),
            "bundle_fingerprint": self._body_fingerprint,
        }
        self._observe("manifest_calls")
        self._observe("manifest_ms", (perf_counter_ns() - started) / 1_000_000)
        return result

    def verify_durable_manifest(self, manifest):
        """Verify retry inputs before any calculation output can be produced."""

        current = self.durable_manifest()
        if Canonical.fingerprint(current) != Canonical.fingerprint(manifest):
            raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")

    def seal(self):
        started = perf_counter_ns()
        self._body_fingerprint = Canonical.fingerprint(
            [
                {"table": table, "address": address, "body": body}
                for (table, address), body in sorted(self._bodies.items(), key=lambda x: str(x[0]))
            ]
        )
        self._sealed = True
        self._observe("seal_calls")
        self._observe("seal_ms", (perf_counter_ns() - started) / 1_000_000)

    def _assert_rows_unchanged(self, candidates) -> int:
        checked = 0
        for key, row in candidates:
            checked += 1
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
        return checked

    def assert_unchanged_in_memory(self):
        """Validate retained ORM rows the owning Session may have changed.

        SQL/Core/bulk/unknown mutations are independently covered by the engine
        listener and exact ``refresh``. Mutable values that evade SQLAlchemy's
        unit-of-work tracking are covered by ``full_audit_unchanged_in_memory``
        once at the durable batch publication boundary.
        """

        started = perf_counter_ns()
        checked = 0
        if not self._sealed:
            raise ValueError("MUTATION_SOURCE_BUNDLE_NOT_SEALED")
        try:
            if isinstance(self.db, Session):
                retained_ids = self._row_keys_by_identity.keys()
                deleted_ids = {id(row) for row in self.db.deleted} & retained_ids
                if deleted_ids:
                    table = self._row_keys_by_identity[next(iter(deleted_ids))][0]
                    raise ValueError("MUTATION_SOURCE_BUNDLE_CHANGED_IN_MEMORY: " + table)
                dirty_ids = {id(row) for row in self.db.dirty} & retained_ids
                candidates = [
                    (
                        self._row_keys_by_identity[row_id],
                        self._rows[self._row_keys_by_identity[row_id]],
                    )
                    for row_id in sorted(dirty_ids)
                ]
                checked = len(candidates)
                self._observe("assert_dirty_rows", checked)
                self._assert_rows_unchanged(candidates)
                if checked == 0:
                    self._observe("assert_clean_noop_calls")
            else:
                # Test adapters and non-Session callers have no trustworthy UOW.
                checked = len(self._rows)
                self._assert_rows_unchanged(self._rows.items())
        finally:
            self._observe("assert_calls")
            self._observe("assert_rows", checked)
            self._observe("assert_ms", (perf_counter_ns() - started) / 1_000_000)

    def full_audit_unchanged_in_memory(self) -> None:
        """Audit every retained ORM body once before durable batch publication.

        This bounded audit catches in-place mutable-value changes that an ORM
        unit of work cannot promise to expose through ``Session.dirty``. It is
        intentionally not part of the per-ticker source scope.
        """

        started = perf_counter_ns()
        checked = 0
        if not self._sealed:
            raise ValueError("MUTATION_SOURCE_BUNDLE_NOT_SEALED")
        try:
            checked = len(self._rows)
            self._assert_rows_unchanged(self._rows.items())
        finally:
            self._observe("full_audit_calls")
            self._observe("full_audit_rows", checked)
            self._observe("full_audit_ms", (perf_counter_ns() - started) / 1_000_000)

    def load(self, model, statement):
        started = perf_counter_ns()
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
            if (
                len(membership) != len(observed_addresses)
                or observed_addresses != expected_addresses
            ):
                raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")
            from sqlalchemy import tuple_

            ordered_addresses = tuple(sorted(expected_addresses, key=str))
            compiled = statement.compile(
                dialect=self.db.get_bind().dialect,
                compile_kwargs={"render_postcompile": True},
            )
            chunk_size = source_refresh_identity_chunk_size(
                len(primary), additional_parameter_count=len(compiled.params)
            )
            result = []
            with self.db.no_autoflush:
                for start in range(0, len(ordered_addresses), chunk_size):
                    address_chunk = ordered_addresses[start : start + chunk_size]
                    result.extend(
                        self.db.execute(
                            statement.where(tuple_(*primary).in_(address_chunk))
                            .add_columns(*columns)
                            .with_for_update(read=True, of=model)
                        ).all()
                    )
        else:
            with self.db.no_autoflush:
                result = self.db.execute(
                    statement.add_columns(*columns).with_for_update(read=True, of=model)
                ).all()
        connection = self.db.connection(bind_arguments={"mapper": model})
        if self._connection is not None and self._connection is not connection:
            raise ValueError("MUTATION_SOURCE_BUNDLE_CONNECTION_MISMATCH")
        self._connection = connection
        _locked_source_bundles.setdefault(connection, WeakSet()).add(self)
        self._sql_transaction = connection.get_transaction()
        self._transaction = self.db.get_transaction()
        rows = []
        loaded_addresses = set()
        for row in result:
            body = dict(zip((c.key for c in columns), row[1:], strict=True))
            address = tuple(body[c.key] for c in primary)
            if address in loaded_addresses:
                raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")
            loaded_addresses.add(address)
            self._bodies[(table, address)] = deepcopy(body)
            self._rows[(table, address)] = row[0]
            self._row_keys_by_identity[id(row[0])] = (table, address)
            rows.append(row[0])
        if self._expected_manifest is not None and loaded_addresses != expected_addresses:
            raise ValueError("CERI_SOURCE_AUTHORITY_CHANGED_BEFORE_RETRY")
        self._observe("load_calls")
        self._observe("loaded_rows", len(rows))
        self._observe("load_ms", (perf_counter_ns() - started) / 1_000_000)
        return rows

    def refresh(self):
        started = perf_counter_ns()
        self._observe("refresh_calls")
        transaction = self.db.get_transaction()
        if not self._requires_revalidation:
            if self.transaction is not transaction or transaction is None:
                self._require_revalidation("TRANSACTION_CHANGED")
            elif (
                self._connection is None or self._connection.closed or self._connection.invalidated
            ):
                self._require_revalidation("CONNECTION_CHANGED")
            elif (
                self._connection.get_transaction() is not self._sql_transaction
                or self._sql_transaction is None
                or not self._sql_transaction.is_active
            ):
                self._require_revalidation("SQL_TRANSACTION_CHANGED")
        if (
            not self._requires_revalidation
            and self.transaction is transaction
            and self.transaction is not None
            and self._connection is not None
            and not self._connection.closed
            and not self._connection.invalidated
            and self._connection.get_transaction() is self._sql_transaction
            and self._sql_transaction is not None
            and self._sql_transaction.is_active
        ):
            self._observe("refresh_noop_calls")
            self._observe("refresh_total_ms", (perf_counter_ns() - started) / 1_000_000)
            return
        self._observe("refresh_executed_calls")
        from sqlalchemy import tuple_

        # No ambient selectors: retain precisely the addresses admitted earlier.
        # All chunks use this Session's current transaction, preserving FOR UPDATE
        # ownership while keeping every statement below the driver parameter budget.
        expected_by_table = {}
        refreshed_by_table = {}
        refreshed_rows = 0
        sql_ns = 0
        with self.db.no_autoflush:
            for table, model in self.models.items():
                columns = list(inspect(model).columns)
                primary = list(inspect(model).primary_key)
                addresses = tuple(
                    sorted(
                        (key[1] for key in self._bodies if key[0] == table),
                        key=str,
                    )
                )
                if not addresses:
                    continue
                chunk_size = source_refresh_identity_chunk_size(len(primary))
                found = {}
                for start in range(0, len(addresses), chunk_size):
                    address_chunk = addresses[start : start + chunk_size]
                    sql_started = perf_counter_ns()
                    retained = (
                        self.db.execute(
                            select(*columns)
                            .where(tuple_(*primary).in_(address_chunk))
                            .with_for_update(read=True, of=model)
                        )
                        .mappings()
                        .all()
                    )
                    sql_ns += perf_counter_ns() - sql_started
                    refreshed_rows += len(retained)
                    for row in retained:
                        address = tuple(row[c.key] for c in primary)
                        found[address] = dict(row)
                expected_by_table[table] = addresses
                refreshed_by_table[table] = found

            # Validate only after every table and chunk was read successfully.
            # Until this completes the bundle remains marked for revalidation.
            validation_started = perf_counter_ns()
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
                        self._bodies[(table, address)]
                    ):
                        raise ValueError("MUTATION_SOURCE_BUNDLE_CHANGED: " + table)
            validation_ns = perf_counter_ns() - validation_started
        self._transaction = self.db.get_transaction()
        self._requires_revalidation = False
        if self.models:
            model = next(iter(self.models.values()))
            self._connection = self.db.connection(bind_arguments={"mapper": model})
            self._sql_transaction = self._connection.get_transaction()
            _locked_source_bundles.setdefault(self._connection, WeakSet()).add(self)
        self._observe("refresh_rows", refreshed_rows)
        self._observe("refresh_sql_ms", sql_ns / 1_000_000)
        self._observe("refresh_validation_ms", validation_ns / 1_000_000)
        self._observe("refresh_total_ms", (perf_counter_ns() - started) / 1_000_000)


@contextmanager
def prefetched_source_scope(db, bundle, *, retain_execution_ownership: bool = True):
    if bundle is None or not isinstance(db, Session):
        yield
        return
    if not isinstance(bundle, PrefetchedSourceBodies) or bundle.db is not db:
        db.rollback()
        raise ValueError("MUTATION_SOURCE_BUNDLE_SESSION_MISMATCH")
    try:
        bundle._observe("scope_calls")
        phase_started = perf_counter_ns()
        bundle.refresh()
        bundle._observe("scope_entry_refresh_ms", (perf_counter_ns() - phase_started) / 1_000_000)
        token = _prefetched_source_bodies.set(bundle)
        try:
            ownership_scope = (
                retained_execution_ownership_scope(db)
                if retain_execution_ownership
                else deferred_execution_ownership_lock()
            )
            with ownership_scope:
                phase_started = perf_counter_ns()
                yield
                bundle._observe("scope_body_ms", (perf_counter_ns() - phase_started) / 1_000_000)
                phase_started = perf_counter_ns()
                bundle.assert_unchanged_in_memory()
                bundle._observe(
                    "scope_exit_assert_ms", (perf_counter_ns() - phase_started) / 1_000_000
                )
                # A direct SQL write can leave ORM instances looking clean.
                # Revalidate the exact sealed addresses before the caller is
                # allowed to cross its durable commit boundary.
                phase_started = perf_counter_ns()
                bundle.refresh()
                bundle._observe(
                    "scope_exit_refresh_ms", (perf_counter_ns() - phase_started) / 1_000_000
                )
        finally:
            _prefetched_source_bodies.reset(token)
    except Exception:
        db.rollback()
        raise


@contextmanager
def reference_writer_manifest_path():
    """Temporarily exercise the exact pre-P1 retained-row conversion path.

    This is diagnostic/test-only: declaration, fencing, writer execution and
    rollback behavior remain real while retained-value reuse is disabled.
    """

    token = _reuse_sealed_writer_values.set(False)
    try:
        yield
    finally:
        _reuse_sealed_writer_values.reset(token)


@contextmanager
def reference_writer_fingerprint_path():
    """Use the P2 materialized-byte fingerprint beneath current P1 semantics."""

    stream_token = _stream_writer_fingerprints.set(False)
    stable_token = _reuse_stable_writer_containers.set(False)
    try:
        yield
    finally:
        _reuse_stable_writer_containers.reset(stable_token)
        _stream_writer_fingerprints.reset(stream_token)


@contextmanager
def compare_writer_manifest_paths():
    """Prove reference/optimized native manifests and canonical bytes match."""

    token = _compare_writer_manifest_paths.set(True)
    try:
        yield
    finally:
        _compare_writer_manifest_paths.reset(token)


@contextmanager
def compare_writer_fingerprint_paths():
    """Compare P2 materialized bytes with P3 streaming for one native manifest."""

    token = _compare_writer_fingerprint_paths.set(True)
    try:
        yield
    finally:
        _compare_writer_fingerprint_paths.reset(token)


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


def _writer_owner_metrics(bundle, owner: str) -> dict:
    metrics = bundle._writer_by_owner.get(owner)
    if metrics is None:
        metrics = {
            "calls": 0,
            "nested_calls": 0,
            "max_depth": 0,
            "wrapper_inclusive_ms": 0.0,
            "wrapper_exclusive_ms": 0.0,
            "wrapper_inclusive_cpu_ms": 0.0,
            "wrapper_exclusive_cpu_ms": 0.0,
            "native_source_ms": 0.0,
            "native_source_cpu_ms": 0.0,
            "fingerprint_ms": 0.0,
            "fingerprint_cpu_ms": 0.0,
            "canonical_bytes": 0,
            "values_visited": 0,
            "source_value_exclusive_ms": 0.0,
            "source_value_exclusive_cpu_ms": 0.0,
            "authority_guard_calls": 0,
            "authority_guard_ms": 0.0,
            "authority_guard_cpu_ms": 0.0,
            "actual_refreshes": 0,
            "source_body_lookups": 0,
            "retained_fingerprint_calls": 0,
            "manifest_ms": 0.0,
            "manifest_cpu_ms": 0.0,
            "fence_ms": 0.0,
            "fence_cpu_ms": 0.0,
            "declaration_ms": 0.0,
            "declaration_cpu_ms": 0.0,
            "body_inclusive_ms": 0.0,
            "body_exclusive_ms": 0.0,
            "body_cpu_ms": 0.0,
            "body_exclusive_cpu_ms": 0.0,
            "sql_count": 0,
            "sql_ms": 0.0,
            "sql_cpu_ms": 0.0,
            "flush_count": 0,
            "flush_ms": 0.0,
            "flush_cpu_ms": 0.0,
            "source_value_cache_hits": 0,
            "source_value_cache_misses": 0,
            "canonical_subtree_hits": 0,
            "canonical_subtree_misses": 0,
            "source_value_dataframe_rows": 0,
            "canonical_chunks_emitted": 0,
            "streaming_fingerprint_calls": 0,
            "legacy_fingerprint_calls": 0,
            "stable_fragment_hits": 0,
            "stable_fragment_misses": 0,
            "stable_fragment_byte_reuse": 0,
            "serialization_wall_ms": 0.0,
            "serialization_cpu_ms": 0.0,
            "hashing_wall_ms": 0.0,
            "hashing_cpu_ms": 0.0,
            "hash_update_calls": 0,
            "largest_document_bytes": 0,
            "stable_container_hits": 0,
            "stable_container_misses": 0,
            "stable_container_rows_validated": 0,
        }
        for kind in (
            "retained_orm",
            "ordinary_orm",
            "dataclass",
            "mapping",
            "sequence",
            "set",
            "dataframe",
            "scalar_enum",
        ):
            metrics[f"source_value_{kind}_calls"] = 0
            metrics[f"source_value_{kind}_exclusive_ms"] = 0.0
            metrics[f"source_value_{kind}_exclusive_cpu_ms"] = 0.0
        bundle._writer_by_owner[owner] = metrics
    return metrics


def _observe_active_writer(key: str, value=1) -> None:
    frames = _writer_telemetry_frames.get()
    if not frames:
        return
    frame = frames[-1]
    bundle = frame["bundle"]
    if not isinstance(bundle, PrefetchedSourceBodies):
        return
    bundle._observe(key, value)
    owner_key = key.removeprefix("writer_")
    metrics = _writer_owner_metrics(bundle, frame["owner"])
    metrics[owner_key] = metrics.get(owner_key, 0) + value


def _source_value_kind(value) -> str:
    if isinstance(value, PrefetchedSourceBodies):
        return "scalar_enum"
    state = inspect(value, raiseerr=False)
    if state is not None and hasattr(state, "mapper"):
        bundle = _prefetched_source_bodies.get()
        if isinstance(bundle, PrefetchedSourceBodies) and id(value) in bundle._row_keys_by_identity:
            return "retained_orm"
        return "ordinary_orm"
    if is_dataclass(value) and not isinstance(value, type):
        return "dataclass"
    if isinstance(value, dict):
        return "mapping"
    if isinstance(value, (tuple, list)):
        return "sequence"
    if isinstance(value, set):
        return "set"
    if hasattr(value, "columns") and hasattr(value, "to_dict"):
        return "dataframe"
    return "scalar_enum"


@cache
def _mapper_source_metadata(model_type):
    """Cache immutable SQLAlchemy mapper metadata, never row state."""

    mapper = inspect(model_type)
    return tuple(mapper.columns), tuple(mapper.primary_key), mapper.local_table.name


def _source_value(db, value):
    """Convert one writer input while recording recursion-exclusive cost."""

    wall_started = perf_counter_ns()
    cpu_started = process_time_ns()
    frame = {"child_wall_ns": 0, "child_cpu_ns": 0}
    frames = _source_value_telemetry_frames.get()
    token = _source_value_telemetry_frames.set(frames + (frame,))
    kind = _source_value_kind(value)
    try:
        return _source_value_impl(db, value)
    finally:
        wall_ns = perf_counter_ns() - wall_started
        cpu_ns = process_time_ns() - cpu_started
        exclusive_wall_ms = max(0, wall_ns - frame["child_wall_ns"]) / 1_000_000
        exclusive_cpu_ms = max(0, cpu_ns - frame["child_cpu_ns"]) / 1_000_000
        _source_value_telemetry_frames.reset(token)
        if frames:
            frames[-1]["child_wall_ns"] += wall_ns
            frames[-1]["child_cpu_ns"] += cpu_ns
        _observe_active_writer("writer_values_visited")
        _observe_active_writer("writer_source_value_exclusive_ms", exclusive_wall_ms)
        _observe_active_writer("writer_source_value_exclusive_cpu_ms", exclusive_cpu_ms)
        _observe_active_writer(f"writer_source_value_{kind}_calls")
        _observe_active_writer(
            f"writer_source_value_{kind}_exclusive_ms", exclusive_wall_ms
        )
        _observe_active_writer(
            f"writer_source_value_{kind}_exclusive_cpu_ms", exclusive_cpu_ms
        )


def _stable_member_signature(value) -> tuple[object, ...] | None:
    if not hasattr(value, "_pit_projection_as_of"):
        return None
    state = inspect(value, raiseerr=False)
    if state is None or not hasattr(state, "mapper"):
        raise ValueError("MUTATION_SOURCE_PIT_PROJECTION_REQUIRED")
    return (
        tuple(
            (column.key, deepcopy(getattr(value, column.key)))
            for column in state.mapper.columns
        ),
        getattr(value, "_pit_projection_as_of", None),
        getattr(value, "_pit_projection_revision_id", None),
    )


def _stable_container_witness(
    value: dict,
) -> dict[str, tuple[object, tuple[tuple[int, tuple[object, ...] | None], ...]]]:
    """Capture ordered membership for a reviewed immutable batch container."""

    witness = {}
    for key, rows in value.items():
        if not isinstance(rows, (list, tuple)):
            raise TypeError("stable writer container values must be sequences")
        witness[str(key)] = (
            rows,
            tuple((id(row), _stable_member_signature(row)) for row in rows),
        )
    return witness


def _validate_stable_container_witness(
    value: dict,
    witness: dict[
        str,
        tuple[object, tuple[tuple[int, tuple[object, ...] | None], ...]],
    ],
) -> int:
    if {str(key) for key in value} != set(witness):
        raise ValueError("MUTATION_SOURCE_STABLE_CONTAINER_CHANGED")
    validated = 0
    for key, rows in value.items():
        retained_rows, retained_members = witness[str(key)]
        if rows is not retained_rows or len(rows) != len(retained_members):
            raise ValueError("MUTATION_SOURCE_STABLE_CONTAINER_CHANGED")
        for row, (retained_id, retained_signature) in zip(
            rows, retained_members, strict=True
        ):
            validated += 1
            if id(row) != retained_id or (
                retained_signature is not None
                and _stable_member_signature(row) != retained_signature
            ):
                raise ValueError("MUTATION_SOURCE_STABLE_CONTAINER_CHANGED")
    return validated


def _stable_batch_container_source_value(db, value: dict):
    """Reuse one exact native/canonical batch segment under P0/P1 authority."""

    bundle = _prefetched_source_bodies.get()
    if (
        not isinstance(bundle, PrefetchedSourceBodies)
        or bundle.db is not db
        or not _reuse_sealed_writer_values.get()
        or not _reuse_stable_writer_containers.get()
    ):
        return {str(key): _source_value(db, item) for key, item in value.items()}
    cached = bundle._writer_stable_container_values.get(id(value))
    if cached is not None:
        retained_value, witness, retained_ids, native = cached
        if retained_value is not value:
            raise ValueError("MUTATION_SOURCE_STABLE_CONTAINER_CHANGED")
        validated = _validate_stable_container_witness(value, witness)
        writer_frames = _writer_telemetry_frames.get()
        if writer_frames:
            frame = writer_frames[-1]
            if retained_ids & (
                set(frame.get("retained_dirty_ids", ()))
                | set(frame.get("retained_deleted_ids", ()))
            ):
                return {str(key): _source_value(db, item) for key, item in value.items()}
        _observe_active_writer("writer_stable_container_hits")
        _observe_active_writer("writer_stable_container_rows_validated", validated)
        return native

    witness = _stable_container_witness(value)
    native = {str(key): _source_value(db, item) for key, item in value.items()}
    retained_ids = frozenset(
        row_id
        for _rows, members in witness.values()
        for row_id, _signature in members
    )
    bundle._writer_stable_container_values[id(value)] = (
        value,
        witness,
        retained_ids,
        native,
    )
    bundle._writer_canonical_values[id(native)] = Canonical.canonicalize(
        native,
        _precomputed=bundle._writer_canonical_values,
    )
    _observe_active_writer("writer_stable_container_misses")
    return native


def _source_value_impl(db, value):
    if isinstance(value, PrefetchedSourceBodies):
        if value.db is not db or _prefetched_source_bodies.get() is not value:
            raise ValueError("MUTATION_SOURCE_BUNDLE_SCOPE_REQUIRED")
        writer_frames = _writer_telemetry_frames.get()
        if (
            not _reuse_sealed_writer_values.get()
            or not writer_frames
            or not writer_frames[-1].get("bundle_guarded")
        ):
            guard_started = perf_counter_ns()
            guard_cpu_started = process_time_ns()
            executed_before = value._telemetry["refresh_executed_calls"]
            value.refresh()
            _observe_active_writer("writer_authority_guard_calls")
            _observe_active_writer(
                "writer_authority_guard_ms", (perf_counter_ns() - guard_started) / 1_000_000
            )
            _observe_active_writer(
                "writer_authority_guard_cpu_ms",
                (process_time_ns() - guard_cpu_started) / 1_000_000,
            )
            _observe_active_writer(
                "writer_actual_refreshes",
                value._telemetry["refresh_executed_calls"] - executed_before,
            )
        if not value._sealed:
            raise ValueError("MUTATION_SOURCE_BUNDLE_NOT_SEALED")
        return {
            "exact_prefetched_source_body_fingerprint": value._body_fingerprint,
            "exact_prefetched_source_count": len(value._bodies),
        }
    state = inspect(value, raiseerr=False)
    if state is not None and hasattr(state, "mapper"):
        columns, primary, table_name = _mapper_source_metadata(type(value))
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

        native = None
        if addresses and all(address is not None for address in addresses):
            bundle = _prefetched_source_bodies.get()
            key = (table_name, tuple(addresses))
            stored = None
            retained_key = (
                bundle._row_keys_by_identity.get(id(value))
                if isinstance(bundle, PrefetchedSourceBodies)
                else None
            )
            writer_frame = (
                _writer_telemetry_frames.get()[-1]
                if _writer_telemetry_frames.get()
                else None
            )
            retained_dirty = bool(
                writer_frame and id(value) in writer_frame.get("retained_dirty_ids", ())
            )
            retained_deleted = bool(
                writer_frame and id(value) in writer_frame.get("retained_deleted_ids", ())
            )
            if retained_deleted:
                raise ValueError("MUTATION_SOURCE_RECORD_ARGUMENT_MISMATCH: " + table_name)
            if bundle is not None and bundle.db is db:
                writer_frames = _writer_telemetry_frames.get()
                if (
                    not _reuse_sealed_writer_values.get()
                    or not writer_frames
                    or not writer_frames[-1].get("bundle_guarded")
                ):
                    guard_started = perf_counter_ns()
                    guard_cpu_started = process_time_ns()
                    executed_before = bundle._telemetry["refresh_executed_calls"]
                    bundle.refresh()
                    _observe_active_writer("writer_authority_guard_calls")
                    _observe_active_writer(
                        "writer_authority_guard_ms",
                        (perf_counter_ns() - guard_started) / 1_000_000,
                    )
                    _observe_active_writer(
                        "writer_authority_guard_cpu_ms",
                        (process_time_ns() - guard_cpu_started) / 1_000_000,
                    )
                    _observe_active_writer(
                        "writer_actual_refreshes",
                        bundle._telemetry["refresh_executed_calls"] - executed_before,
                    )
                if (
                    _reuse_sealed_writer_values.get()
                    and retained_key == key
                    and not retained_dirty
                    and not hasattr(value, "_pit_projection_as_of")
                ):
                    native = bundle._writer_source_values.get(key)
                    if native is not None:
                        _observe_active_writer("writer_source_value_cache_hits")
                        _observe_active_writer("writer_canonical_subtree_hits")
                if native is None:
                    # Trusted authority-internal comparison only. Public body
                    # access continues to return defensive deep copies.
                    stored = bundle._bodies.get(key)
                    _observe_active_writer("writer_source_body_lookups")
            if native is None and stored is None:
                stored = (
                    db.execute(
                        select(*columns)
                        .where(
                            *[
                                column == address
                                for column, address in zip(primary, addresses, strict=True)
                            ]
                        )
                        .with_for_update(read=True, of=type(value))
                        .execution_options(t14b_source_authority=True)
                    )
                    .mappings()
                    .one_or_none()
                )
            if native is None and stored is None:
                raise ValueError("MUTATION_SOURCE_RECORD_MISSING: " + table_name)
            if table_name == "price_bars" and hasattr(value, "_pit_projection_as_of"):
                from app.models.tables import PriceBar
                from app.services.price_bar_repository import project_price_bar_rows_as_of

                native = source_columns(
                    {column.key: getattr(value, column.key) for column in columns}
                )
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
            if native is None:
                if (
                    _reuse_sealed_writer_values.get()
                    and retained_key == key
                    and not retained_dirty
                ):
                    native = source_columns(stored)
                    bundle._writer_source_values[key] = native
                    bundle._writer_canonical_values[id(native)] = Canonical.canonicalize(native)
                    _observe_active_writer("writer_source_value_cache_misses")
                    _observe_active_writer("writer_canonical_subtree_misses")
                else:
                    native = source_columns(
                        {column.key: getattr(value, column.key) for column in columns}
                    )
            # A caller's unflushed/unretained source changes are not authoritative
            # input. Clean rows already admitted to this exact sealed bundle are
            # covered by the per-manifest authority guard plus the bounded final
            # audit, so the historical pair of per-row fingerprints is redundant.
            if not _active_source_writers.get() and (
                not _reuse_sealed_writer_values.get()
                or retained_key != key
                or retained_dirty
            ):
                _observe_active_writer("writer_retained_fingerprint_calls", 2)
                if Canonical.fingerprint(source_columns(stored)) != Canonical.fingerprint(native):
                    raise ValueError(
                        "MUTATION_SOURCE_RECORD_ARGUMENT_MISMATCH: " + table_name
                    )
        if native is None:
            native = source_columns({column.key: getattr(value, column.key) for column in columns})
        if table_name == "background_jobs":
            native = {
                key: native[key] for key in ("id", "job_type", "related_run_id", "payload_json")
            }
        return {"table": table_name, "state": native}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: (
                _stable_batch_container_source_value(db, getattr(value, field.name))
                if type(value).__module__
                == "app.services.ceri.feature_rebuild_service"
                and type(value).__name__ == "CeriFeatureBatchContext"
                and field.name == "bars_by_ticker"
                else _source_value(db, getattr(value, field.name))
            )
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
        rows = value.to_dict(orient="records")
        _observe_active_writer("writer_source_value_dataframe_rows", len(rows))
        return {"frame": rows}
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
            wrapper_wall_started = perf_counter_ns()
            wrapper_cpu_started = process_time_ns()
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
            bundle = _prefetched_source_bodies.get()
            parent_frames = _writer_telemetry_frames.get()
            depth = len(parent_frames) + 1
            company = arguments.arguments.get("company")
            request = arguments.arguments.get("request")
            ticker = (
                getattr(company, "ticker", None)
                or getattr(request, "ticker", None)
                or (parent_frames[-1].get("ticker") if parent_frames else None)
            )
            frame = {
                "owner": owner,
                "bundle": bundle,
                "depth": depth,
                "parent": parent_frames[-1]["owner"] if parent_frames else None,
                "ticker": ticker,
                "child_wrapper_wall_ns": 0,
                "child_wrapper_cpu_ns": 0,
                "digest": None,
                "canonical_bytes": 0,
                "native_source_ms": 0.0,
                "fingerprint_ms": 0.0,
                "body_inclusive_ms": 0.0,
                "body_exclusive_ms": 0.0,
                "body_inclusive_cpu_ms": 0.0,
                "body_exclusive_cpu_ms": 0.0,
                "manifest_ms": 0.0,
                "manifest_cpu_ms": 0.0,
                "declaration_ms": 0.0,
                "declaration_cpu_ms": 0.0,
                "fence_ms": 0.0,
                "fence_cpu_ms": 0.0,
                "semantic_arguments": len(
                    [
                        key
                        for key in arguments.arguments
                        if key not in _OPERATIONAL_ARGUMENTS
                    ]
                ),
            }
            telemetry_token = _writer_telemetry_frames.set(parent_frames + (frame,))
            if isinstance(bundle, PrefetchedSourceBodies):
                bundle._observe("writer_calls")
                bundle._telemetry["writer_max_nesting_depth"] = max(
                    bundle._telemetry["writer_max_nesting_depth"], depth
                )
                metrics = _writer_owner_metrics(bundle, owner)
                metrics["calls"] += 1
                metrics["nested_calls"] += int(depth > 1)
                metrics["max_depth"] = max(metrics["max_depth"], depth)
            context = None
            reference_digest = None
            manifest_equal = None
            canonical_equal = None
            try:
                try:
                    with db.no_autoflush:
                        manifest_phase_started = perf_counter_ns()
                        manifest_phase_cpu_started = process_time_ns()
                        if isinstance(bundle, PrefetchedSourceBodies):
                            guard_started = perf_counter_ns()
                            guard_cpu_started = process_time_ns()
                            executed_before = bundle._telemetry["refresh_executed_calls"]
                            bundle.refresh()
                            frame["bundle_guarded"] = True
                            frame["retained_dirty_ids"] = {
                                id(row) for row in db.dirty
                            } & bundle._row_keys_by_identity.keys()
                            frame["retained_deleted_ids"] = {
                                id(row) for row in db.deleted
                            } & bundle._row_keys_by_identity.keys()
                            _observe_active_writer("writer_authority_guard_calls")
                            _observe_active_writer(
                                "writer_authority_guard_ms",
                                (perf_counter_ns() - guard_started) / 1_000_000,
                            )
                            _observe_active_writer(
                                "writer_authority_guard_cpu_ms",
                                (process_time_ns() - guard_cpu_started) / 1_000_000,
                            )
                            _observe_active_writer(
                                "writer_actual_refreshes",
                                bundle._telemetry["refresh_executed_calls"] - executed_before,
                            )
                        native_wall_started = perf_counter_ns()
                        native_cpu_started = process_time_ns()
                        native_source = {
                            key: _source_value(db, value)
                            for key, value in arguments.arguments.items()
                            if key not in _OPERATIONAL_ARGUMENTS
                        }
                        if _compare_writer_manifest_paths.get():
                            reuse_token = _reuse_sealed_writer_values.set(False)
                            telemetry_frames = _writer_telemetry_frames.get()
                            source_frames = _source_value_telemetry_frames.get()
                            writer_telemetry_token = _writer_telemetry_frames.set(())
                            source_telemetry_token = _source_value_telemetry_frames.set(())
                            try:
                                reference_native_source = {
                                    key: _source_value(db, value)
                                    for key, value in arguments.arguments.items()
                                    if key not in _OPERATIONAL_ARGUMENTS
                                }
                            finally:
                                _source_value_telemetry_frames.reset(source_telemetry_token)
                                _writer_telemetry_frames.reset(writer_telemetry_token)
                                _reuse_sealed_writer_values.reset(reuse_token)
                            if _writer_telemetry_frames.get() != telemetry_frames:
                                raise RuntimeError("writer telemetry scope was not restored")
                            if _source_value_telemetry_frames.get() != source_frames:
                                raise RuntimeError("source telemetry scope was not restored")
                            reference_manifest = {
                                "writer": owner,
                                "native_source": reference_native_source,
                            }
                            reference_canonical = Canonical.bytes(reference_manifest)
                            reference_digest = hashlib.sha256(reference_canonical).hexdigest()
                            manifest_equal = reference_native_source == native_source
                            _observe_active_writer("writer_legacy_fingerprint_calls")
                        native_ms = (perf_counter_ns() - native_wall_started) / 1_000_000
                        native_cpu_ms = (process_time_ns() - native_cpu_started) / 1_000_000
                        frame["native_source_ms"] = native_ms
                        _observe_active_writer("writer_native_source_ms", native_ms)
                        _observe_active_writer("writer_native_source_cpu_ms", native_cpu_ms)
                        manifest = {"writer": owner, "native_source": native_source}
                        fingerprint_wall_started = perf_counter_ns()
                        fingerprint_cpu_started = process_time_ns()
                        precomputed = (
                            bundle._writer_canonical_values
                            if isinstance(bundle, PrefetchedSourceBodies)
                            and _reuse_sealed_writer_values.get()
                            else None
                        )
                        fragments = (
                            bundle._writer_canonical_fragments
                            if isinstance(bundle, PrefetchedSourceBodies)
                            and _reuse_sealed_writer_values.get()
                            else None
                        )
                        if (
                            _compare_writer_fingerprint_paths.get()
                            and reference_digest is None
                        ):
                            reference_canonical = Canonical.bytes(
                                manifest, precomputed=precomputed
                            )
                            reference_digest = hashlib.sha256(reference_canonical).hexdigest()
                            manifest_equal = True
                            _observe_active_writer("writer_legacy_fingerprint_calls")
                        streaming_telemetry: dict[str, int | float] = {}
                        canonical = None
                        if _stream_writer_fingerprints.get():
                            if reference_digest is not None:
                                canonical = Canonical.streaming_bytes(
                                    manifest,
                                    precomputed=precomputed,
                                    fragments=fragments,
                                    telemetry=streaming_telemetry,
                                )
                                digest = hashlib.sha256(canonical).hexdigest()
                                streaming_telemetry["canonical_byte_count"] = len(canonical)
                            else:
                                digest = Canonical.fingerprint_streaming(
                                    manifest,
                                    precomputed=precomputed,
                                    fragments=fragments,
                                    telemetry=streaming_telemetry,
                                )
                            _observe_active_writer("writer_streaming_fingerprint_calls")
                        else:
                            canonical = Canonical.bytes(manifest, precomputed=precomputed)
                            digest = hashlib.sha256(canonical).hexdigest()
                            streaming_telemetry["canonical_byte_count"] = len(canonical)
                            _observe_active_writer("writer_legacy_fingerprint_calls")
                        if reference_digest is not None:
                            canonical_equal = reference_canonical == canonical
                            if (
                                not manifest_equal
                                or not canonical_equal
                                or reference_digest != digest
                            ):
                                raise ValueError(
                                    "MUTATION_SOURCE_WRITER_MANIFEST_EQUIVALENCE_FAILED"
                                )
                        fingerprint_ms = (
                            perf_counter_ns() - fingerprint_wall_started
                        ) / 1_000_000
                        fingerprint_cpu_ms = (
                            process_time_ns() - fingerprint_cpu_started
                        ) / 1_000_000
                        frame["digest"] = digest
                        canonical_bytes = int(streaming_telemetry["canonical_byte_count"])
                        frame["canonical_bytes"] = canonical_bytes
                        frame["fingerprint_ms"] = fingerprint_ms
                        _observe_active_writer("writer_fingerprint_ms", fingerprint_ms)
                        _observe_active_writer(
                            "writer_fingerprint_cpu_ms", fingerprint_cpu_ms
                        )
                        _observe_active_writer("writer_canonical_bytes", canonical_bytes)
                        for telemetry_key in (
                            "canonical_chunks_emitted",
                            "stable_fragment_hits",
                            "stable_fragment_misses",
                            "stable_fragment_byte_reuse",
                            "serialization_wall_ms",
                            "serialization_cpu_ms",
                            "hashing_wall_ms",
                            "hashing_cpu_ms",
                            "hash_update_calls",
                        ):
                            _observe_active_writer(
                                "writer_" + telemetry_key,
                                streaming_telemetry.get(telemetry_key, 0),
                            )
                        if isinstance(bundle, PrefetchedSourceBodies):
                            bundle._telemetry["writer_largest_document_bytes"] = max(
                                bundle._telemetry["writer_largest_document_bytes"],
                                canonical_bytes,
                            )
                            owner_metrics = _writer_owner_metrics(bundle, owner)
                            owner_metrics["largest_document_bytes"] = max(
                                owner_metrics["largest_document_bytes"],
                                canonical_bytes,
                            )
                        frame["streaming_telemetry"] = dict(streaming_telemetry)
                        frame["manifest_ms"] = (
                            perf_counter_ns() - manifest_phase_started
                        ) / 1_000_000
                        frame["manifest_cpu_ms"] = (
                            process_time_ns() - manifest_phase_cpu_started
                        ) / 1_000_000
                        _observe_active_writer("writer_manifest_ms", frame["manifest_ms"])
                        _observe_active_writer(
                            "writer_manifest_cpu_ms", frame["manifest_cpu_ms"]
                        )
                        declaration_started = perf_counter_ns()
                        declaration_cpu_started = process_time_ns()
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
                            entrypoint=MutationEntryPointDescriptor(
                                owner, "NATIVE_SOURCE_WRITER"
                            ),
                            writer=MutationWriterDescriptor(
                                owner, "phase5-source-writer-v1", domain
                            ),
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
                        frame["declaration_ms"] = (
                            perf_counter_ns() - declaration_started
                        ) / 1_000_000
                        frame["declaration_cpu_ms"] = (
                            process_time_ns() - declaration_cpu_started
                        ) / 1_000_000
                        _observe_active_writer(
                            "writer_declaration_ms", frame["declaration_ms"]
                        )
                        _observe_active_writer(
                            "writer_declaration_cpu_ms", frame["declaration_cpu_ms"]
                        )
                        phase_started = perf_counter_ns()
                        phase_cpu_started = process_time_ns()
                        fence_mutation_transaction(db, context)
                        frame["fence_ms"] = (
                            perf_counter_ns() - phase_started
                        ) / 1_000_000
                        frame["fence_cpu_ms"] = (
                            process_time_ns() - phase_cpu_started
                        ) / 1_000_000
                        _observe_active_writer("writer_fence_ms", frame["fence_ms"])
                        _observe_active_writer(
                            "writer_fence_cpu_ms", frame["fence_cpu_ms"]
                        )
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
                        body_started = perf_counter_ns()
                        body_cpu_started = process_time_ns()
                        try:
                            return writer(*args, **kwargs)
                        finally:
                            body_ns = perf_counter_ns() - body_started
                            body_cpu_ns = process_time_ns() - body_cpu_started
                            body_ms = body_ns / 1_000_000
                            body_exclusive_ms = max(
                                0, body_ns - frame["child_wrapper_wall_ns"]
                            ) / 1_000_000
                            frame["body_inclusive_ms"] = body_ms
                            frame["body_exclusive_ms"] = body_exclusive_ms
                            frame["body_inclusive_cpu_ms"] = body_cpu_ns / 1_000_000
                            frame["body_exclusive_cpu_ms"] = max(
                                0, body_cpu_ns - frame["child_wrapper_cpu_ns"]
                            ) / 1_000_000
                            _observe_active_writer("writer_body_ms", body_ms)
                            if isinstance(bundle, PrefetchedSourceBodies):
                                metrics = _writer_owner_metrics(bundle, owner)
                                metrics["body_inclusive_ms"] += body_ms
                            _observe_active_writer(
                                "writer_body_exclusive_ms", body_exclusive_ms
                            )
                            _observe_active_writer(
                                "writer_body_cpu_ms", frame["body_inclusive_cpu_ms"]
                            )
                            _observe_active_writer(
                                "writer_body_exclusive_cpu_ms",
                                frame["body_exclusive_cpu_ms"],
                            )
                except Exception:
                    db.rollback()
                    raise
                finally:
                    _active_source_writers.reset(token)
            finally:
                wrapper_wall_ns = perf_counter_ns() - wrapper_wall_started
                wrapper_cpu_ns = process_time_ns() - wrapper_cpu_started
                wrapper_inclusive_ms = wrapper_wall_ns / 1_000_000
                wrapper_exclusive_ms = max(
                    0, wrapper_wall_ns - frame["child_wrapper_wall_ns"]
                ) / 1_000_000
                wrapper_inclusive_cpu_ms = wrapper_cpu_ns / 1_000_000
                wrapper_exclusive_cpu_ms = max(
                    0, wrapper_cpu_ns - frame["child_wrapper_cpu_ns"]
                ) / 1_000_000
                _observe_active_writer("writer_wrapper_inclusive_ms", wrapper_inclusive_ms)
                _observe_active_writer("writer_wrapper_exclusive_ms", wrapper_exclusive_ms)
                _observe_active_writer(
                    "writer_wrapper_inclusive_cpu_ms", wrapper_inclusive_cpu_ms
                )
                _observe_active_writer(
                    "writer_wrapper_exclusive_cpu_ms", wrapper_exclusive_cpu_ms
                )
                if isinstance(bundle, PrefetchedSourceBodies):
                    invocation = {
                        "owner": owner,
                        "parent": frame["parent"],
                        "depth": depth,
                        "ticker": ticker,
                        "domain": domain.value,
                        "role": role,
                        "semantic_mode": mode.value,
                        "semantic_arguments": frame["semantic_arguments"],
                        "source_manifest_id": getattr(
                            arguments.arguments.get("context"), "source_manifest_id", None
                        ),
                        "wrapper_inclusive_ms": round(wrapper_inclusive_ms, 3),
                        "wrapper_exclusive_ms": round(wrapper_exclusive_ms, 3),
                        "wrapper_inclusive_cpu_ms": round(wrapper_inclusive_cpu_ms, 3),
                        "wrapper_exclusive_cpu_ms": round(wrapper_exclusive_cpu_ms, 3),
                        "native_source_ms": round(frame["native_source_ms"], 3),
                        "fingerprint_ms": round(frame["fingerprint_ms"], 3),
                        "manifest_ms": round(frame["manifest_ms"], 3),
                        "manifest_cpu_ms": round(frame["manifest_cpu_ms"], 3),
                        "declaration_ms": round(frame["declaration_ms"], 3),
                        "declaration_cpu_ms": round(frame["declaration_cpu_ms"], 3),
                        "fence_ms": round(frame["fence_ms"], 3),
                        "fence_cpu_ms": round(frame["fence_cpu_ms"], 3),
                        "body_inclusive_ms": round(frame["body_inclusive_ms"], 3),
                        "body_exclusive_ms": round(frame["body_exclusive_ms"], 3),
                        "body_inclusive_cpu_ms": round(
                            frame["body_inclusive_cpu_ms"], 3
                        ),
                        "body_exclusive_cpu_ms": round(
                            frame["body_exclusive_cpu_ms"], 3
                        ),
                        "canonical_bytes": frame["canonical_bytes"],
                        "digest": frame["digest"],
                        "reference_digest": reference_digest,
                        "manifest_equal": manifest_equal,
                        "canonical_equal": canonical_equal,
                        "streaming_telemetry": frame.get("streaming_telemetry", {}),
                    }
                    bundle._writer_invocations.append(invocation)
                    bundle._writer_top_invocations.append(invocation)
                    bundle._writer_top_invocations.sort(
                        key=lambda item: item["wrapper_exclusive_ms"], reverse=True
                    )
                    del bundle._writer_top_invocations[20:]
                _writer_telemetry_frames.reset(telemetry_token)
                if parent_frames:
                    parent_frames[-1]["child_wrapper_wall_ns"] += wrapper_wall_ns
                    parent_frames[-1]["child_wrapper_cpu_ns"] += wrapper_cpu_ns

        return guarded

    return adopt
