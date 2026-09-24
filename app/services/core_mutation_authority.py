"""Writer-side resolution of Phase-5 core/contextual mutation declarations.

No authority is selected by latest/current lookup here. The native writer names
the identity, configuration, exact sources and manifest that it is writing.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
from inspect import signature

from sqlalchemy.orm import Session, object_session

from app.models.tables import (
    CoreCalculationEvidence,
    MarketCalculationContext,
    TechnicalSourceManifest,
    UploadRun,
)
from app.services.calculation_identity import CalculationIdentity, IdentityState
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.domain_mutation import (
    MUTATION_AUTHORITY_POLICIES,
    DomainMutationContext,
    MutationDomain,
    MutationEligibilityReference,
    MutationEntryPointDescriptor,
    MutationEvidenceReference,
    MutationSemanticMode,
    MutationWriterDescriptor,
    fence_mutation_transaction,
)
from app.services.domain_write_fence import current_domain_write_ownership
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.producer_readiness import readiness_from_evidence

_active_writers = ContextVar("core_mutation_semantic_transactions", default=())


def require_semantic_writer(db, owners):
    owners = (owners,) if isinstance(owners, str) else tuple(owners)
    if isinstance(db, Session) and not any(
        active_db is db and active_owner in owners
        for active_db, active_owner in _active_writers.get()
    ):
        # Mapper callbacks run inside flush, where Session.rollback is illegal.
        # Raising aborts that flush; the semantic transaction then rolls back.
        if not db._flushing:
            db.rollback()
        raise ValueError("MUTATION_SUPPORTING_SEMANTIC_TRANSACTION_REQUIRED")


def core_writer_member(owner, *, models=None):
    """Supporting row changes belong to a validating semantic transaction."""

    def adopt(mechanism):
        parameters = signature(mechanism)

        @wraps(mechanism)
        def guarded(*args, **kwargs):
            arguments = parameters.bind(*args, **kwargs).arguments
            if models is not None and not isinstance(arguments.get("row"), models):
                return mechanism(*args, **kwargs)
            db = arguments.get("db")
            if db is None:
                from sqlalchemy import inspect

                for value in arguments.values():
                    state = inspect(value, raiseerr=False)
                    if state is not None and hasattr(state, "mapper"):
                        db = object_session(value)
                        if db is not None:
                            break
            require_semantic_writer(db, owner)
            return mechanism(*args, **kwargs)

        return guarded

    return adopt


def identity_cutoff(db: Session, identity: CalculationIdentity) -> MarketCalculationCutoff:
    """Resolve an exact context ID, or preserve explicitly parentless identity time."""
    context_id = identity.calculation_context.market_calculation_context_id
    if context_id.state is IdentityState.KNOWN:
        from app.services.market_calculation_context_service import cutoff_from_row

        row = db.get(MarketCalculationContext, context_id.value)
        if row is None:
            raise ValueError("MUTATION_CONTEXT_RECORD_MISSING")
        return cutoff_from_row(row)
    calendar = identity.temporal.calendar.value
    if calendar is None:
        raise ValueError("MUTATION_TEMPORAL_AUTHORITY_REQUIRED")
    readiness = calendar.bar_readiness_version
    return MarketCalculationCutoff(
        cutoff_at=identity.temporal.calculation_cutoff.value,
        latest_completed_session=identity.temporal.as_of_session.value,
        exchange_timezone=calendar.exchange_timezone,
        calendar_version=calendar.calendar_version,
        bar_readiness_version=readiness.value.version
        if readiness.state is IdentityState.KNOWN
        else readiness.state.value,
        daily_bar_ready_at=None,
        cutoff_reason="EXPLICIT_CALCULATION_IDENTITY",
    )


def core_mutation_context(
    db: Session,
    *,
    domain: MutationDomain,
    identity: CalculationIdentity,
    configuration,
    sources: dict,
    manifests: dict[str, dict],
    eligibility: tuple[MutationEligibilityReference, ...] = (),
    entrypoint: str,
    writer: str = "persist_core_evidence",
    semantic_mode: MutationSemanticMode = MutationSemanticMode.CANONICAL_CALCULATION,
) -> DomainMutationContext:
    """Construct from explicitly named native arguments; construction is no permit."""
    if identity is None or configuration is None:
        raise ValueError("MUTATION_IDENTITY_AND_CONFIGURATION_REQUIRED")
    pins = []
    with db.no_autoflush:
        for role, source in sorted(sources.items()):
            evidence_id = getattr(source, "evidence_id", None)
            evidence = db.get(CoreCalculationEvidence, evidence_id) if evidence_id else None
            if evidence is None:
                raise ValueError("MUTATION_SOURCE_EVIDENCE_MISSING: " + role)
            pins.append(
                MutationEvidenceReference(
                    role, "core_calculation_evidence", evidence.id, evidence.evidence_key
                )
            )
        for role, manifest in sorted(manifests.items()):
            fingerprint = Canonical.fingerprint(manifest)
            pins.append(
                MutationEvidenceReference(role, "native_manifest", fingerprint, fingerprint)
            )
        ownership = current_domain_write_ownership()
        return DomainMutationContext(
            domain=domain,
            semantic_mode=semantic_mode,
            entrypoint=MutationEntryPointDescriptor(entrypoint, "NATIVE_SEMANTIC_WRITER"),
            writer=MutationWriterDescriptor(writer, "phase5-core-writer-v1", domain),
            reason="Persist explicitly bound native calculation",
            calculation_identity=identity,
            temporal=identity_cutoff(db, identity),
            configuration=configuration.identity,
            evidence=tuple(pins),
            eligibility=eligibility,
            run_id=identity.ownership.run_id.value
            if identity.ownership.run_id.state is IdentityState.KNOWN
            else None,
            pipeline_run_id=identity.ownership.pipeline_id.value
            if identity.ownership.pipeline_id.state is IdentityState.KNOWN
            else None,
            execution=ownership,
            durable=ownership is not None,
        )


def validate_core_mutation_authority(
    db: Session,
    context: DomainMutationContext,
    *,
    domain: MutationDomain,
    identity: CalculationIdentity,
    configuration,
    sources: dict,
    manifests: dict[str, dict],
    writer: str = "persist_core_evidence",
) -> None:
    """Validate declarations and authoritative storage in the effective transaction."""
    if not isinstance(context, DomainMutationContext):
        raise ValueError("DOMAIN_MUTATION_CONTEXT_REQUIRED")
    native_writer = MutationWriterDescriptor(writer, "phase5-core-writer-v1", domain)
    if context.domain is not domain or context.writer != native_writer:
        raise ValueError("MUTATION_WRITER_DOMAIN_MISMATCH")
    if context.calculation_identity != identity or context.configuration != configuration.identity:
        raise ValueError("MUTATION_NATIVE_ARGUMENT_AUTHORITY_MISMATCH")
    with db.no_autoflush:
        fence_mutation_transaction(db, context)
        actual_ownership = current_domain_write_ownership()
        if actual_ownership is not None and context.execution != actual_ownership:
            raise ValueError("MUTATION_DURABLE_OWNERSHIP_MISMATCH")
        if context.execution is not None:
            from sqlalchemy import select

            from app.models.tables import BackgroundJob
            from app.services.domain_write_fence import retained_execution_job_scope

            job = retained_execution_job_scope(
                db,
                job_id=context.execution.job_id,
                execution_token=context.execution.execution_token,
            )
            if job is None:
                job = (
                    db.execute(
                        select(BackgroundJob.related_run_id, BackgroundJob.payload_json)
                        .where(BackgroundJob.id == context.execution.job_id)
                        .with_for_update()
                    )
                    .one()
                    ._mapping
                )
            if context.run_id is not None and job["related_run_id"] != context.run_id:
                raise ValueError("MUTATION_EXECUTION_RUN_SCOPE_MISMATCH")
            if (
                context.pipeline_run_id is not None
                and job["payload_json"].get("pipeline_run_id") != context.pipeline_run_id
            ):
                raise ValueError("MUTATION_EXECUTION_PIPELINE_SCOPE_MISMATCH")
        if context.run_id is not None and db.get(UploadRun, context.run_id) is None:
            raise ValueError("MUTATION_UPLOAD_OWNER_MISSING")
        if context.pipeline_run_id is not None:
            from app.services.market_calculation_context_service import (
                assert_pipeline_calculation_context,
                market_calculation_context_fingerprint,
            )

            authoritative = assert_pipeline_calculation_context(
                db,
                pipeline_run_id=context.pipeline_run_id,
                upload_run_id=context.run_id,
                supplied_context=context.temporal,
            )
            fingerprint = identity.calculation_context.context_fingerprint.value
            if fingerprint is None or fingerprint.digest != market_calculation_context_fingerprint(
                authoritative
            ):
                raise ValueError("MUTATION_PERSISTED_CONTEXT_FINGERPRINT_MISMATCH")
        _validate_configuration(db, context, configuration)
        expected = core_mutation_context(
            db,
            domain=domain,
            identity=identity,
            configuration=configuration,
            sources=sources,
            manifests=manifests,
            eligibility=context.eligibility,
            entrypoint=context.entrypoint.identity,
            semantic_mode=context.semantic_mode,
        )
        if set(context.evidence) != set(expected.evidence):
            raise ValueError("MUTATION_EXACT_SOURCE_MANIFEST_MISMATCH")
        for role, source in sources.items():
            evidence = db.get(CoreCalculationEvidence, source.evidence_id)
            _validate_evidence(evidence)
            validate_source_values(source, evidence, db=db)
            validate_source_configuration(db, identity, evidence, context.execution)
            if getattr(source, "run_id", evidence.run_id) != evidence.run_id:
                raise ValueError("MUTATION_SOURCE_RUN_MISMATCH: " + role)
            ticker = getattr(source, "ticker", None)
            if ticker is not None and ticker.upper() != evidence.ticker:
                raise ValueError("MUTATION_SOURCE_SUBJECT_MISMATCH: " + role)
            _validate_permission(context, role, evidence)
            source_identity = CalculationIdentity.from_canonical_payload(
                evidence.calculation_identity_json
            )
            if role.startswith("ibmi_") and (
                source_identity.temporal.as_of_session.state is not IdentityState.KNOWN
                or source_identity.temporal.calculation_cutoff.state is not IdentityState.KNOWN
                or source_identity.temporal.as_of_session.value
                > identity.temporal.as_of_session.value
                or source_identity.temporal.calculation_cutoff.value
                > identity.temporal.calculation_cutoff.value
                or evidence.ticker != identity.subject.ticker.value
            ):
                raise ValueError("MUTATION_IBMI_TEMPORAL_OR_SUBJECT_COMPATIBILITY_REJECTED")
            if role in {"fundamental", "technical", "combined", "ranking"}:
                for actual, intended in (
                    (source_identity.ownership, identity.ownership),
                    (source_identity.calculation_context, identity.calculation_context),
                    (source_identity.temporal, identity.temporal),
                ):
                    if actual != intended:
                        raise ValueError("MUTATION_SOURCE_CALCULATION_CONTEXT_MISMATCH: " + role)
            if role == "regime" and (
                source_identity.temporal.as_of_session != identity.temporal.as_of_session
                or source_identity.temporal.calculation_cutoff.value
                > identity.temporal.calculation_cutoff.value
                or source_identity.temporal.calendar != identity.temporal.calendar
            ):
                raise ValueError("MUTATION_REGIME_TEMPORAL_COMPATIBILITY_REJECTED")
            if role == "prior_sector" and (
                source_identity.temporal.as_of_session.value
                >= identity.temporal.as_of_session.value
                or source_identity.temporal.calculation_cutoff.value
                > identity.temporal.calculation_cutoff.value
                or source_identity.configuration != identity.configuration
            ):
                raise ValueError("MUTATION_PRIOR_SECTOR_COMPATIBILITY_REJECTED")


def _validate_configuration(db, context, configuration):
    from app.services.configuration_delivery import binding_reference, load_configuration_delivery
    from app.services.contextual_effective_configuration import ContextualEffectiveConfiguration
    from app.services.core_effective_configuration import CoreEffectiveConfiguration

    fresh = type(configuration)(
        configuration.family,
        configuration.entries,
        configuration.producer_version,
        configuration.evaluated_at,
    )
    if (
        fresh.semantic_hash != configuration.semantic_hash
        or fresh.resolution_hash != configuration.resolution_hash
    ):
        raise ValueError("MUTATION_CONFIGURATION_FINGERPRINT_MISMATCH")
    namespace = configuration.family.namespace
    from app.services.decision_effective_configuration import DecisionEffectiveConfiguration

    config = (
        DecisionEffectiveConfiguration(configuration)
        if namespace.startswith("decision.")
        else CoreEffectiveConfiguration(configuration)
        if namespace.startswith("core.")
        else ContextualEffectiveConfiguration(configuration)
    )
    config.require_family(namespace)
    if context.pipeline_run_id is not None or context.execution is not None:
        owners = (
            [{"pipeline_run_id": context.pipeline_run_id}]
            if context.pipeline_run_id is not None
            else []
        ) + ([{"job_id": context.execution.job_id}] if context.execution is not None else [])
        for owner in owners:
            delivery = load_configuration_delivery(
                db, binding_reference(db, **owner), resolution_hash=configuration.resolution_hash
            )
            frozen = [c.snapshot for c in delivery.configurations.values()]
            if not any(
                c.semantic_hash == configuration.semantic_hash
                and c.resolution_hash == configuration.resolution_hash
                and Canonical.dumps(c.as_dict()) == Canonical.dumps(configuration.as_dict())
                for c in frozen
            ):
                raise ValueError("MUTATION_RETAINED_CONFIGURATION_MISMATCH")


def _validate_evidence(evidence):
    if evidence is None:
        raise ValueError("MUTATION_SOURCE_EVIDENCE_MISSING")
    if Canonical.fingerprint(evidence.payload_json) != evidence.payload_fingerprint:
        raise ValueError("MUTATION_SOURCE_PAYLOAD_FINGERPRINT_MISMATCH")
    identity = CalculationIdentity.from_canonical_payload(evidence.calculation_identity_json)
    if str(identity.fingerprint()) != evidence.calculation_identity_fingerprint:
        raise ValueError("MUTATION_SOURCE_IDENTITY_FINGERPRINT_MISMATCH")
    if identity.configuration.effective_configuration.state is not IdentityState.KNOWN:
        raise ValueError("MUTATION_SOURCE_LEGACY_CONFIGURATION")
    namespace = identity.configuration.effective_configuration.value.namespace
    if not namespace.startswith(("core.", "contextual.", "decision.")):
        raise ValueError("MUTATION_SOURCE_LEGACY_CONFIGURATION")
    from app.services.effective_configuration import configuration_from_evidence

    configuration = configuration_from_evidence(evidence)
    if configuration.value != identity.configuration.effective_configuration.value:
        raise ValueError("MUTATION_SOURCE_CONFIGURATION_BINDING_MISMATCH")
    from app.services.decision_effective_configuration import (
        configuration_from_payload,
        validate_executable_configuration,
    )
    from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY

    retained = configuration_from_payload(evidence.payload_json[CONFIGURATION_PAYLOAD_KEY])
    validate_executable_configuration(retained)
    from app.services.contextual_effective_configuration import ContextualEffectiveConfiguration
    from app.services.core_effective_configuration import CoreEffectiveConfiguration
    from app.services.decision_effective_configuration import DecisionEffectiveConfiguration

    native = (
        DecisionEffectiveConfiguration(retained.snapshot)
        if namespace.startswith("decision.")
        else CoreEffectiveConfiguration(retained.snapshot)
        if namespace.startswith("core.")
        else ContextualEffectiveConfiguration(retained.snapshot)
    )
    native.require_family(namespace)
    expected_namespace = (
        "decision.setup"
        if evidence.artifact_kind == "SETUP"
        else "core." + evidence.artifact_kind.lower()
        if evidence.artifact_kind in {"FUNDAMENTAL", "TECHNICAL", "COMBINED", "RANKING"}
        else "contextual." + evidence.artifact_kind.lower()
    )
    if evidence.artifact_kind == "IBMI":
        expected_namespace += "." + str(evidence.ranking_profile).lower()
    if namespace != expected_namespace:
        raise ValueError("MUTATION_SOURCE_KIND_CONFIGURATION_MISMATCH")
    if evidence.artifact_kind == "RANKING":
        from app.services.core_effective_configuration import core_configuration_from_evidence

        if (
            core_configuration_from_evidence(evidence).values["profile"]["name"]
            != evidence.ranking_profile
        ):
            raise ValueError("MUTATION_RANKING_PROFILE_CONFIGURATION_MISMATCH")
    if (
        identity.ownership.run_id.state is IdentityState.KNOWN
        and evidence.run_id != identity.ownership.run_id.value
    ):
        raise ValueError("MUTATION_EVIDENCE_IDENTITY_RUN_MISMATCH")
    if (
        identity.subject.ticker.state is IdentityState.KNOWN
        and evidence.ticker != identity.subject.ticker.value
    ):
        raise ValueError("MUTATION_EVIDENCE_IDENTITY_SUBJECT_MISMATCH")
    if evidence.evidence_key != Canonical.fingerprint(
        {
            "artifact_kind": evidence.artifact_kind,
            "calculation_identity_fingerprint": evidence.calculation_identity_fingerprint,
            "payload_fingerprint": evidence.payload_fingerprint,
            "source_evidence_ids": evidence.source_evidence_ids_json,
        }
    ):
        raise ValueError("MUTATION_SOURCE_EVIDENCE_KEY_MISMATCH")


def validate_source_configuration(db, identity, evidence, execution=None):
    """Compare pinned producers with the parent's retained slots, when present."""
    from app.models.tables import ExecutionConfigurationAnchor
    from app.services.configuration_delivery import binding_reference

    producer = CalculationIdentity.from_canonical_payload(evidence.calculation_identity_json)
    configuration = producer.configuration.effective_configuration.value
    key = configuration.namespace
    if evidence.artifact_kind == "RANKING":
        key += ":" + evidence.ranking_profile
    owners = []
    if identity.ownership.pipeline_id.state is IdentityState.KNOWN:
        owners.append({"pipeline_run_id": identity.ownership.pipeline_id.value})
    if execution is not None:
        owners.append({"job_id": execution.job_id})
    for owner in owners:
        reference = binding_reference(db, **owner)
        if reference is None:
            raise ValueError("MUTATION_SOURCE_PARENT_CONFIGURATION_BINDING_REQUIRED")
        anchor = db.get(ExecutionConfigurationAnchor, reference["anchor_id"])
        slot = anchor.payload_json["configurations"].get(key)
        if slot is not None and Canonical.canonicalize(slot["identity"]) != configuration.as_dict():
            raise ValueError("MUTATION_SOURCE_RETAINED_CONFIGURATION_MISMATCH")


def validate_source_values(source, evidence, *, db=None):
    """Consumed values must agree with the exact sealed producer target."""
    from decimal import Decimal

    from sqlalchemy import Float, Numeric, inspect, select

    from app.services.core_calculation_evidence import (
        normalize_configuration_business_payload,
    )

    state = inspect(source, raiseerr=False)
    if state is None or not hasattr(state, "mapper"):
        return  # A pointer-only DTO supplies no mutable financial values.
    if evidence is None:
        raise ValueError("MUTATION_SOURCE_EVIDENCE_MISSING")
    if isinstance(db, Session):
        # Eligibility readers may return a detached, intentionally reduced
        # frozen projection. Validate the exact stored source, not that DTO.
        primary_key = state.mapper.primary_key
        if len(primary_key) != 1 or getattr(source, "id", None) is None:
            raise ValueError("MUTATION_SOURCE_ROW_ADDRESS_REQUIRED")
        with db.no_autoflush:
            stored = db.scalar(
                select(type(source)).where(primary_key[0] == source.id).with_for_update()
            )
        if stored is None or stored.evidence_id != evidence.id:
            raise ValueError("MUTATION_SOURCE_ROW_EVIDENCE_MISMATCH")
    supplied_fields = set(state.dict) if state.transient or state.detached else None
    kind = evidence.artifact_kind
    diagnostic_fields = set()
    if (
        kind == "TECHNICAL"
        and supplied_fields is None
        and (getattr(source, "missing_data_json", None) or {}).get("market_data", {}).get("mode")
        == "CACHE_FALLBACK"
        and "cache_fallback_market_data" in (getattr(source, "warning_flags_json", None) or [])
    ):
        # The native pipeline annotates these serving diagnostics after sealing.
        # Financial consumers use their immutable counterparts and readiness.
        diagnostic_fields = {
            "technical_confidence",
            "data_quality_score",
            "warning_flags_json",
            "missing_data_json",
        }
    expected = dict(evidence.payload_json)
    if kind == "IBMI":
        expected = expected.get("derived_output")
    elif kind == "CERI":
        expected = expected.get("decision_output")
    if not isinstance(expected, dict):
        raise ValueError("MUTATION_SOURCE_FINANCIAL_PAYLOAD_REQUIRED: " + kind)
    if kind in {"FUNDAMENTAL", "TECHNICAL", "COMBINED", "RANKING"}:
        from app.services.core_calculation_evidence import calculation_evidence_payload

        native = calculation_evidence_payload(source)
        if "effective_configuration_at_creation" in evidence.payload_json:
            normalize_configuration_business_payload(
                native,
                namespace="core." + kind.lower(),
                source_ids=evidence.source_evidence_ids_json,
            )
    else:
        native = None

    def compare(row, payload, normalized=None):
        mapper = inspect(row).mapper
        for field, value in payload.items():
            if field in {"producer_readiness", "effective_configuration_at_creation", "rows"}:
                continue
            if field in diagnostic_fields:
                continue
            name = field if field in mapper.column_attrs else field + "_json"
            if name not in mapper.column_attrs:
                raise ValueError("MUTATION_SOURCE_VALUE_SCHEMA_MISMATCH: " + field)
            if supplied_fields is not None and name not in supplied_fields:
                continue  # Native frozen readers deliberately omit unused columns.
            actual = normalized[name] if normalized is not None else getattr(row, name)
            column = mapper.column_attrs[name].columns[0]
            from datetime import UTC, datetime

            if (
                isinstance(actual, datetime)
                and actual.tzinfo is None
                and getattr(column.type, "timezone", False)
            ):
                actual = actual.replace(tzinfo=UTC)
            if (
                actual is not None
                and value is not None
                and isinstance(column.type, (Numeric, Float))
            ):
                equal = Decimal(str(actual)) == Decimal(str(value))
            else:
                # calculation_evidence_payload already canonicalizes the complete
                # native projection. Re-encoding each normalized JSON field adds
                # no check; detached/contextual projections still normalize here.
                equal = (
                    actual
                    if normalized is not None
                    else Canonical.canonicalize({field: actual})[field]
                ) == value
            if not equal:
                raise ValueError("MUTATION_SOURCE_FINANCIAL_VALUE_MISMATCH: " + field)

    compare(source, expected, native)
    if kind == "SECTOR":
        from app.models.tables import SectorRotationRow

        db = object_session(source)
        rows = list(
            db.scalars(
                select(SectorRotationRow)
                .where(SectorRotationRow.snapshot_id == source.id)
                .with_for_update()
            )
        )
        children = {row.sector_slug: row for row in rows}
        if len(children) != len(expected["rows"]):
            raise ValueError("MUTATION_SOURCE_SECTOR_CHILDREN_MISMATCH")
        for payload in expected["rows"]:
            row = children.get(payload["sector_slug"])
            if row is None:
                raise ValueError("MUTATION_SOURCE_SECTOR_CHILDREN_MISMATCH")
            compare(row, payload)


def _validate_permission(context, role, evidence):
    policy = MUTATION_AUTHORITY_POLICIES[context.domain]
    if role not in policy.eligibility_roles + policy.eligibility_if_pinned:
        return
    supplied = next((p for p in context.eligibility if p.role == role), None)
    readiness = readiness_from_evidence(evidence)
    if supplied is None or supplied.readiness != readiness:
        raise ValueError("MUTATION_PERSISTED_READINESS_MISMATCH: " + role)
    from app.services import contextual_consumer_eligibility as contextual
    from app.services import technical_consumer_eligibility as technical

    prefix = {
        "ibmi_liquidity": "IBMI_LIQUIDITY",
        "ibmi_volatility": "IBMI_VOLATILITY",
        "ibmi_short_pressure": "IBMI_SHORT_PRESSURE",
        "prior_sector": "PRIOR_SECTOR",
    }.get(role, evidence.artifact_kind)
    module = technical if prefix == "TECHNICAL" else contextual
    consumer = context.domain.value
    if consumer == "WINNER_PREDICTION":
        from app.services.winner_probability import consumer_eligibility as module

        consumer = "WINNER"
    native = getattr(module, prefix + "_TO_" + consumer, None)
    if native is None or native.evaluate(readiness) != supplied.decision:
        raise ValueError("MUTATION_FROZEN_ELIGIBILITY_MISMATCH: " + role)


def mutation_eligibility(role: str, evidence, frozen: dict) -> MutationEligibilityReference:
    """Rehydrate a native frozen decision, retaining the exact readiness source."""
    from app.services.producer_readiness import (
        ConsumerEligibilityDecision,
        ConsumerEligibilityStatus,
        EligibilityReason,
    )

    dto = frozen.get("decision") or {}
    from app.services.producer_readiness import ProducerReadinessEnvelope

    retained = readiness_from_evidence(evidence)
    if ProducerReadinessEnvelope.from_payload(frozen["producer_readiness"]) != retained:
        raise ValueError("MUTATION_PERSISTED_READINESS_MISMATCH: " + role)
    decision = ConsumerEligibilityDecision(
        consumer=dto["consumer"],
        status=ConsumerEligibilityStatus(dto["status"]),
        reasons=tuple(EligibilityReason(r) for r in dto["reasons"]),
        policy_version=dto["policy_version"],
        producer_readiness_fingerprint=dto["producer_readiness_fingerprint"],
        producer_evidence_id=dto["producer_evidence_id"],
    )
    return MutationEligibilityReference(
        role,
        retained,
        decision,
        decision.status is ConsumerEligibilityStatus.ELIGIBLE,
    )


def core_writer_transaction(writer):
    """Roll back this semantic writer's serving/evidence changes on rejection.

    A local savepoint supplements the writer's existing transaction; it never
    commits the caller's transaction and does not intercept unrelated ORM writes.
    Fake calculator sessions are not certified persistence boundaries.
    """
    parameters = signature(writer)

    @wraps(writer)
    def transactional(*args, **kwargs):
        arguments = parameters.bind(*args, **kwargs).arguments
        db = arguments.get("db")
        if not isinstance(db, Session):
            return writer(*args, **kwargs)
        owner = writer.__module__ + ":" + writer.__qualname__
        token = None
        preserved_exception = None
        try:
            connection = db.connection()
            if (
                connection.dialect.name == "sqlite"
                and not connection.connection.driver_connection.in_transaction
            ):
                # pysqlite otherwise lets the first SAVEPOINT become the outer
                # transaction, whose release commits before caller rollback.
                connection.exec_driver_sql("BEGIN")
            with db.begin_nested():
                # Savepoint entry flushes caller staging. It must not borrow
                # this writer's projection permission before validation starts.
                token = _active_writers.set(_active_writers.get() + ((db, owner),))
                try:
                    result = writer(*args, **kwargs)
                except Exception as exc:
                    if not getattr(exc, "preserve_semantic_state_on_raise", False):
                        raise
                    # Some terminal control-flow signals deliberately persist
                    # the state they announce (for example, CANCELLED). Commit
                    # only this writer's savepoint; the caller still owns the
                    # surrounding transaction and final commit.
                    preserved_exception = exc
                    result = None
            if preserved_exception is not None:
                raise preserved_exception
            return result
        except Exception as exc:
            # begin_nested flushes pre-existing pending rows. Rejecting a lower
            # writer must also discard a caller's staged serving mutation.
            if not getattr(exc, "preserve_semantic_state_on_raise", False):
                db.rollback()
            raise
        finally:
            if token is not None:
                _active_writers.reset(token)

    return transactional


def artifact_mutation_context(
    db, *, kind, current_row, identity, configuration, sources, payload, declaration_only=False
):
    """Native artifact adapter. Every named manifest is independently checked."""
    from app.models.tables import RawCompanyRow
    from app.services.combined_ranking_identity import require_fundamental_raw_source

    domain = MutationDomain(kind.value)
    if identity is None or configuration is None:
        raise ValueError("MUTATION_IDENTITY_AND_CONFIGURATION_REQUIRED")
    if (
        identity.ownership.run_id.state is IdentityState.KNOWN
        and identity.ownership.run_id.value != getattr(current_row, "run_id", None)
    ):
        raise ValueError("MUTATION_ARTIFACT_RUN_MISMATCH")
    if (
        identity.subject.ticker.state is IdentityState.KNOWN
        and identity.subject.ticker.value != getattr(current_row, "ticker", "").upper()
    ):
        raise ValueError("MUTATION_ARTIFACT_SUBJECT_MISMATCH")
    debug = (
        getattr(current_row, "debug_json", None)
        or getattr(current_row, "evidence_lineage_json", None)
        or getattr(current_row, "source_lineage_json", None)
        or {}
    )
    if (
        identity.subject.company_id.state is IdentityState.KNOWN
        and identity.subject.company_id.value != getattr(current_row, "company_id", None)
    ):
        raise ValueError("MUTATION_ARTIFACT_COMPANY_MISMATCH")
    native_session = (
        getattr(current_row, "as_of_session", None)
        or getattr(current_row, "as_of_date", None)
        or getattr(current_row, "data_as_of_date", None)
    )
    native_cutoff = getattr(current_row, "cutoff_at", None) or getattr(
        current_row, "calculation_cutoff_at", None
    )
    from datetime import UTC

    if native_cutoff is not None and native_cutoff.tzinfo is None:
        native_cutoff = native_cutoff.replace(tzinfo=UTC)
    if (
        native_session is not None
        and native_session != identity.temporal.as_of_session.value
        or native_cutoff is not None
        and native_cutoff != identity.temporal.calculation_cutoff.value
    ):
        raise ValueError("MUTATION_ARTIFACT_TEMPORAL_MISMATCH")
    if domain is MutationDomain.SETUP:
        debug = getattr(current_row, "source_lineage_json", None) or {}
    manifest = {}
    if domain is MutationDomain.SETUP:
        from app.services.decision_mutation_authority import validate_setup_inputs

        manifest["price_manifest"] = validate_setup_inputs(
            db,
            current_row,
            identity,
            sources,
            configuration=configuration,
            declaration_only=declaration_only,
        )
    elif domain is MutationDomain.FUNDAMENTAL:
        references = (
            identity.source_lineage.value.references if identity.source_lineage.value else ()
        )
        addresses = [r.artifact_id for r in references if r.artifact_type == "RawCompanyRow"]
        if len(addresses) != 1 or not addresses[0].isdigit():
            raise ValueError("MUTATION_EXACT_RAW_SOURCE_REQUIRED")
        from sqlalchemy import select

        raw = db.scalar(
            select(RawCompanyRow).where(RawCompanyRow.id == int(addresses[0])).with_for_update()
        )
        if (
            raw is None
            or raw.run_id != current_row.run_id
            or raw.ticker.upper() != current_row.ticker.upper()
        ):
            raise ValueError("MUTATION_RAW_SOURCE_OWNER_MISMATCH")
        require_fundamental_raw_source(identity, raw, consumer="FundamentalWriter")
        manifest["raw_source"] = {
            "id": raw.id,
            "run_id": raw.run_id,
            "row_number": raw.row_number,
            "ticker": raw.ticker.upper(),
            "raw_json": raw.raw_json,
        }
    elif domain is MutationDomain.TECHNICAL:
        lineage = debug.get("temporal_lineage") or {}
        source_reference = lineage.get("canonical_source_manifest")
        if not isinstance(source_reference, dict):
            raise ValueError("MUTATION_TECHNICAL_CANONICAL_MANIFEST_REQUIRED")
        manifest["price_manifest"] = {
            "canonical_source_manifest": source_reference,
            "calculation_context_id": current_row.calculation_context_id,
            "calculation_cutoff_at": current_row.calculation_cutoff_at,
            "input_as_of_session": current_row.input_as_of_session,
            "calendar_version": current_row.calendar_version,
        }
        references = (
            identity.source_lineage.value.references if identity.source_lineage.value else ()
        )
        expected = Canonical.fingerprint(manifest["price_manifest"])
        if not any(
            r.artifact_type == "TechnicalInputEnvelope"
            and r.fingerprint.value
            and r.fingerprint.value.digest == expected
            for r in references
        ):
            raise ValueError("MUTATION_TECHNICAL_INPUT_ENVELOPE_MISMATCH")
        source_manifest = db.get(TechnicalSourceManifest, current_row.source_manifest_id)
        if (
            source_manifest is None
            or source_manifest.run_id != current_row.run_id
            or source_manifest.pipeline_run_id != identity.ownership.pipeline_id.value
            or source_manifest.calculation_context_id != current_row.calculation_context_id
            or source_manifest.manifest_digest != source_reference.get("digest")
            or Canonical.fingerprint(source_manifest.manifest_json)
            != source_manifest.manifest_digest
        ):
            raise ValueError("MUTATION_TECHNICAL_CANONICAL_MANIFEST_MISMATCH")
        validate_technical_source_manifest(db, source_manifest, identity)
    elif domain is MutationDomain.REGIME:
        require_payload_reference(
            identity,
            "contextual-source-envelope",
            {
                "input_symbols": payload.get("input_symbols"),
                "market_inputs": debug.get("market_inputs"),
                "source_latest_sessions": (debug.get("temporal_lineage") or {}).get(
                    "source_latest_sessions"
                ),
                "universe_participation": payload.get("universe_participation") or None,
                "sector_leadership": payload.get("sector_leadership"),
            },
        )
        manifest["price_manifest"] = {
            "input_symbols": payload.get("input_symbols"),
            "market_inputs": debug.get("market_inputs"),
            "temporal_lineage": debug.get("temporal_lineage"),
        }
        inputs = debug.get("market_inputs")
        if not isinstance(inputs, dict) or not inputs:
            raise ValueError("MUTATION_REGIME_PIT_INPUTS_REQUIRED")
        found = 0

        def validate_frames(value):
            nonlocal found
            if isinstance(value, dict):
                if "row_count" in value and "fingerprint" in value:
                    source_manifest = value.get("pit_source_manifest")
                    if source_manifest is None:
                        raise ValueError("MUTATION_REGIME_PIT_MANIFEST_REQUIRED")
                    validate_price_source_manifest(
                        db,
                        source_manifest,
                        identity.temporal.calculation_cutoff.value,
                        identity.temporal.as_of_session.value,
                    )
                    found += 1
                else:
                    for child in value.values():
                        validate_frames(child)
            elif isinstance(value, list):
                for child in value:
                    validate_frames(child)

        validate_frames(inputs)
        if not found:
            raise ValueError("MUTATION_REGIME_PIT_MANIFEST_REQUIRED")
    elif domain is MutationDomain.SECTOR:
        envelope = debug.get("native_input_envelope")
        if not isinstance(envelope, dict):
            raise ValueError("MUTATION_SECTOR_NATIVE_INPUT_ENVELOPE_REQUIRED")
        require_payload_reference(identity, "sector-rotation-input-envelope", envelope)
        validate_sector_manifest(db, identity, envelope)
        manifest["universe_manifest"] = {
            "native_input_envelope": envelope,
            "source_evidence": {
                role: source.evidence_id for role, source in sorted(sources.items())
            },
            "input_lineage": identity.source_lineage.value.as_dict(),
        }
    elif domain is MutationDomain.CERI:
        if not isinstance(payload.get("source_manifest"), dict):
            raise ValueError("MUTATION_CERI_SOURCE_MANIFEST_REQUIRED")
        manifest["source_manifest"] = payload["source_manifest"]
        from app.services.ceri.decision_evidence import build_ceri_source_manifest

        if Canonical.fingerprint(
            build_ceri_source_manifest(db, current_row)
        ) != Canonical.fingerprint(payload["source_manifest"]):
            raise ValueError("MUTATION_CERI_PERSISTED_SOURCE_MANIFEST_MISMATCH")
    elif domain is MutationDomain.IBMI:
        if not isinstance(payload.get("constituent_manifest"), dict):
            raise ValueError("MUTATION_IBMI_CONSTITUENT_MANIFEST_REQUIRED")
        manifest["constituent_manifest"] = payload["constituent_manifest"]
        validate_ibmi_manifest(db, current_row, identity, payload["constituent_manifest"])
    allowed = set(
        MUTATION_AUTHORITY_POLICIES[domain].evidence_roles
        + MUTATION_AUTHORITY_POLICIES[domain].optional_evidence_roles
    )
    native_sources = {role: source for role, source in sources.items() if role in allowed}
    permissions = []
    for role, source in sources.items():
        evidence = db.get(CoreCalculationEvidence, source.evidence_id)
        if evidence is None:
            raise ValueError("MUTATION_SOURCE_EVIDENCE_MISSING")
        if not declaration_only and role not in allowed:
            # Core authority validates every allowed source's seal immediately
            # after this adapter, in the same writer transaction. Extra Sector
            # population references are validated here because they are outside
            # the policy's ordinary source roles.
            _validate_evidence(evidence)
            validate_source_values(source, evidence, db=db)
            validate_source_configuration(db, identity, evidence, current_domain_write_ownership())
        expected_kind = (
            "RANKING"
            if role.startswith("ranking:")
            else "SECTOR"
            if role == "prior_sector"
            else "IBMI"
            if role.startswith("ibmi_")
            else "RANKING"
            if role == "ranking_metadata"
            else role.upper()
        )
        if evidence.artifact_kind != expected_kind:
            raise ValueError("MUTATION_SOURCE_ROLE_KIND_MISMATCH: " + role)
        validate_source_address(identity, role, source, evidence)
        if role not in allowed:
            if domain is not MutationDomain.SECTOR or not role.startswith("ranking:"):
                raise ValueError("MUTATION_UNDECLARED_SOURCE_ROLE: " + role)
            validate_financial_source_context(identity, evidence)
            validate_sector_permission(evidence, envelope["universe_rows"])
            continue
        policy = MUTATION_AUTHORITY_POLICIES[domain]
        if role in policy.eligibility_roles + policy.eligibility_if_pinned:
            frozen = (
                debug.get("technical_consumer_eligibility")
                if role == "technical"
                else (debug.get("contextual_consumer_eligibility") or {}).get(role)
            )
            if not isinstance(frozen, dict):
                raise ValueError("MUTATION_FROZEN_ELIGIBILITY_REQUIRED: " + role)
            permissions.append(mutation_eligibility(role, evidence, frozen))
    context = core_mutation_context(
        db,
        domain=domain,
        identity=identity,
        configuration=configuration,
        sources=native_sources,
        manifests=manifest,
        eligibility=tuple(permissions),
        entrypoint=type(current_row).__name__,
    )
    return context, native_sources, manifest


def require_payload_reference(identity, kind, payload):
    expected = Canonical.fingerprint(payload)
    if not any(
        ref.artifact_type == kind
        and ref.fingerprint.value is not None
        and ref.fingerprint.value.digest == expected
        for ref in identity.source_lineage.value.references
    ):
        raise ValueError("MUTATION_NATIVE_INPUT_ENVELOPE_MISMATCH: " + kind)


def validate_financial_source_context(identity, evidence):
    source = CalculationIdentity.from_canonical_payload(evidence.calculation_identity_json)
    if (
        source.ownership != identity.ownership
        or source.calculation_context != identity.calculation_context
        or source.temporal != identity.temporal
    ):
        raise ValueError("MUTATION_SOURCE_CALCULATION_CONTEXT_MISMATCH")


def validate_sector_permission(evidence, universe_rows):
    from app.services import contextual_consumer_eligibility as contextual
    from app.services import technical_consumer_eligibility as technical

    if evidence.artifact_kind == "FUNDAMENTAL":
        return  # Native Sector policy has no Fundamental eligibility dimension.
    native = getattr(
        technical if evidence.artifact_kind == "TECHNICAL" else contextual,
        evidence.artifact_kind + "_TO_SECTOR",
    )
    for row in universe_rows:
        for frozen in (row.get("debug", {}).get("contextual_consumer_eligibility") or {}).values():
            if (frozen.get("decision") or {}).get("producer_evidence_id") == evidence.id:
                pin = mutation_eligibility(evidence.artifact_kind.lower(), evidence, frozen)
                if native.evaluate(pin.readiness) != pin.decision:
                    raise ValueError("MUTATION_FROZEN_ELIGIBILITY_MISMATCH")
                return
    raise ValueError("MUTATION_FROZEN_ELIGIBILITY_REQUIRED: sector contributor")


def validate_sector_manifest(db, identity, envelope):
    from sqlalchemy import select

    from app.models.tables import RawCompanyRow

    for universe in envelope["universe_rows"]:
        manifest = universe.get("debug", {}).get("native_source_manifest")
        if not isinstance(manifest, dict):
            raise ValueError("MUTATION_SECTOR_CONTRIBUTOR_MANIFEST_REQUIRED")
        for pin in manifest["financial_inputs"]:
            evidence = db.get(CoreCalculationEvidence, pin["evidence_id"])
            _validate_evidence(evidence)
            validate_source_configuration(db, identity, evidence, current_domain_write_ownership())
            if (
                evidence.artifact_kind != pin["artifact_kind"]
                or evidence.ticker != pin["ticker"]
                or evidence.run_id != pin["run_id"]
            ):
                raise ValueError("MUTATION_SECTOR_CONTRIBUTOR_ADDRESS_MISMATCH")
            validate_financial_source_context(identity, evidence)
            validate_sector_permission(evidence, envelope["universe_rows"])
        for pin in manifest["raw_inputs"]:
            raw = db.scalar(
                select(RawCompanyRow).where(RawCompanyRow.id == pin["id"]).with_for_update()
            )
            if (
                raw is None
                or raw.run_id != identity.ownership.run_id.value
                or Canonical.fingerprint(
                    {
                        "run_id": raw.run_id,
                        "ticker": raw.ticker,
                        "raw_json": raw.raw_json,
                        "sector": raw.sector,
                        "sector_canonical": raw.sector_canonical,
                    }
                )
                != pin["fingerprint"]
            ):
                raise ValueError("MUTATION_SECTOR_RAW_SOURCE_MISMATCH")
    for etf in envelope["etf_rows"]:
        if etf.get("debug", {}).get("missing_proxy"):
            continue
        manifests = etf.get("debug", {}).get("source_manifests")
        if not isinstance(manifests, dict) or not manifests:
            raise ValueError("MUTATION_SECTOR_ETF_PIT_MANIFEST_REQUIRED")
        for source in manifests.values():
            if source is None:
                raise ValueError("MUTATION_SECTOR_ETF_PIT_MANIFEST_REQUIRED")
            validate_price_source_manifest(
                db,
                source,
                identity.temporal.calculation_cutoff.value,
                identity.temporal.as_of_session.value,
            )


def validate_source_address(identity, role, source, evidence):
    """A serving address alone cannot substitute for a different sealed parent."""
    kind = {
        "fundamental": "FundamentalScore",
        "technical": "TechnicalScore",
        "combined": "CombinedResult",
        "regime": "MarketRegimeSnapshot",
        "sector": "SectorRotationSnapshot",
        "ranking_metadata": "RankingResult",
        "ranking": "RankingResult",
        "prior_sector": "PriorSectorRotationSnapshot",
    }.get(role, "RankingResult" if role.startswith("ranking:") else "IBIntelligenceFeature")
    references = identity.source_lineage.value.references if identity.source_lineage.value else ()
    # CERI's native source envelope seals IBMI IDs through its independently
    # rebuilt manifest rather than one SourceArtifactReference per feature.
    if (
        role.startswith("ibmi_")
        and identity.configuration.effective_configuration.value.namespace == "contextual.ceri"
    ):
        return
    if not any(
        ref.artifact_type == kind
        and ref.artifact_id
        in {
            str(getattr(source, "id", None)),
            f"evidence:{evidence.id}",
            f"identity:{evidence.calculation_identity_fingerprint}",
        }
        and ref.fingerprint.value is not None
        and ref.fingerprint.value.digest == evidence.calculation_identity_fingerprint
        for ref in references
    ):
        raise ValueError("MUTATION_IMMUTABLE_SOURCE_ADDRESS_MISMATCH: " + role)


def validate_ibmi_manifest(db, feature, identity, manifest):
    from datetime import UTC

    from sqlalchemy import select

    from app.models.ib_market_intelligence_tables import (
        IBHistogramBin,
        IBHistogramSnapshot,
        IBHistoricalMetricBar,
        IBIntelligenceRequestItem,
        IBMarketIntelligenceSnapshot,
    )
    from app.models.tables import PriceBar
    from app.services.ib_market_intelligence.decision_evidence import (
        IbmiFeatureConstituents,
        build_ibmi_constituent_manifest,
    )
    from app.services.ib_market_intelligence.repository import project_historical_metric_rows_as_of
    from app.services.price_bar_repository import project_price_bar_rows_as_of

    cutoff = identity.temporal.calculation_cutoff.value
    session = identity.temporal.as_of_session.value

    def exact_rows(model, ids):
        if len(ids) != len(set(ids)):
            raise ValueError("MUTATION_IBMI_DUPLICATE_SOURCE_ADDRESS")
        rows = list(db.scalars(select(model).where(model.id.in_(ids)).with_for_update()))
        if len(rows) != len(ids):
            raise ValueError("MUTATION_IBMI_SOURCE_ADDRESS_MISSING")
        return rows

    metrics = exact_rows(
        IBHistoricalMetricBar, [s["metric_bar_id"] for s in manifest["historical_metric_states"]]
    )
    metrics = project_historical_metric_rows_as_of(db, metrics, as_of=cutoff)
    live = exact_rows(
        IBMarketIntelligenceSnapshot, [s["id"] for s in manifest["live_observations"]]
    )
    requests = exact_rows(
        IBIntelligenceRequestItem, [s["id"] for s in manifest["availability_observations"]]
    )
    price_states = manifest["price_series"]["states"]
    prices = project_price_bar_rows_as_of(
        db, exact_rows(PriceBar, [s["price_bar_id"] for s in price_states]), as_of=cutoff
    )
    for row in metrics + live + requests:
        if row.ticker.upper() != feature.ticker.upper():
            raise ValueError("MUTATION_IBMI_SOURCE_SUBJECT_MISMATCH")
    for row in metrics + prices:
        if getattr(row, "effective_session", getattr(row, "bar_date", None)) > session:
            raise ValueError("MUTATION_IBMI_SOURCE_SESSION_MISMATCH")
        seen = row.first_seen_at
        if seen is None or (seen.replace(tzinfo=UTC) if seen.tzinfo is None else seen) > cutoff:
            raise ValueError("MUTATION_IBMI_SOURCE_OBSERVATION_MISMATCH")
    for row in live + requests:
        observed = getattr(row, "observed_at", None) or row.completed_at or row.started_at
        if (
            observed is None
            or (observed.replace(tzinfo=UTC) if observed.tzinfo is None else observed) > cutoff
        ):
            raise ValueError("MUTATION_IBMI_SOURCE_OBSERVATION_MISMATCH")
    histogram = manifest.get("histogram")
    snapshot = (
        exact_rows(IBHistogramSnapshot, [histogram["snapshot"]["id"]])[0] if histogram else None
    )
    bins = exact_rows(IBHistogramBin, [s["id"] for s in histogram["bins"]]) if histogram else ()
    if snapshot is not None and (
        snapshot.ticker.upper() != feature.ticker.upper()
        or snapshot.observed_at > cutoff
        or any(row.histogram_snapshot_id != snapshot.id for row in bins)
    ):
        raise ValueError("MUTATION_IBMI_HISTOGRAM_SOURCE_MISMATCH")
    rebuilt, issues = build_ibmi_constituent_manifest(
        db,
        IbmiFeatureConstituents(
            metric_bars=tuple(metrics),
            live_observations=tuple(live),
            availability_observations=tuple(requests),
            price_bars=tuple(prices),
            price_roles=tuple((s["price_bar_id"], r) for s in price_states for r in s["roles"]),
            price_basis=tuple(manifest["price_series"]["basis"].items()),
            histogram_snapshot=snapshot,
            histogram_bins=tuple(bins),
        ),
    )
    if issues or Canonical.fingerprint(rebuilt) != Canonical.fingerprint(manifest):
        raise ValueError("MUTATION_IBMI_PERSISTED_CONSTITUENT_MISMATCH")


def validate_price_source_manifest(db, manifest, cutoff, session):
    from datetime import date, datetime

    from sqlalchemy import select

    from app.models.tables import PriceBar
    from app.services.price_bar_repository import (
        _pipeline_acquisition_visibility,
        price_bar_pit_manifest_hash,
        project_price_bar_rows_as_of,
    )

    if (
        datetime.fromisoformat(manifest["as_of"]) != cutoff
        or date.fromisoformat(manifest["max_session"]) != session
    ):
        raise ValueError("MUTATION_PRICE_MANIFEST_TEMPORAL_MISMATCH")
    states = manifest["states"]
    ids = [state["id"] for state in states]
    if len(ids) != len(set(ids)) or any(type(id) is not int or id <= 0 for id in ids):
        raise ValueError("MUTATION_PRICE_MANIFEST_ADDRESS_INVALID")
    rows = list(db.scalars(select(PriceBar).where(PriceBar.id.in_(ids)).with_for_update()))
    if len(rows) != len(ids) or any(
        row.ticker != manifest["ticker"]
        or row.what_to_show != manifest["what_to_show"]
        or row.timeframe != manifest["timeframe"]
        or row.bar_date > session
        or row.first_seen_at is None
        for row in rows
    ):
        raise ValueError("MUTATION_PRICE_MANIFEST_SCOPE_MISMATCH")
    baseline = [
        row
        for row in rows
        if row.first_seen_at <= cutoff and row.created_at <= cutoff
    ]
    acquired = [row for row in rows if row not in baseline]
    projected = project_price_bar_rows_as_of(db, baseline, as_of=cutoff)
    if acquired:
        authority = manifest.get("acquisition_authority")
        if not isinstance(authority, dict):
            raise ValueError("MUTATION_PRICE_ACQUISITION_AUTHORITY_REQUIRED")
        resolved = _pipeline_acquisition_visibility(
            db,
            calculation_context_id=int(authority["calculation_context_id"]),
            ticker=manifest["ticker"],
            what_to_show=manifest["what_to_show"],
            timeframe=manifest["timeframe"],
        )
        if resolved is None or Canonical.canonicalize(authority) != Canonical.canonicalize(
            resolved.__dict__
        ):
            raise ValueError("MUTATION_PRICE_ACQUISITION_AUTHORITY_MISMATCH")
        if any(
            row.first_fetch_run_id != resolved.fetch_run_id
            or row.first_fetch_item_id != resolved.fetch_item_id
            or row.created_at > resolved.completed_at
            or row.first_seen_at > resolved.completed_at
            for row in acquired
        ):
            raise ValueError("MUTATION_PRICE_ACQUISITION_SCOPE_MISMATCH")
        projected.extend(
            project_price_bar_rows_as_of(db, acquired, as_of=resolved.completed_at)
        )
    expected = {state["id"]: state["fingerprint"] for state in states}
    if len(projected) != len(ids) or any(
        price_bar_pit_manifest_hash(db, row) != expected[row.id] for row in projected
    ):
        raise ValueError("MUTATION_PRICE_MANIFEST_PIT_FINGERPRINT_MISMATCH")


def validate_technical_source_manifest(
    db,
    source_manifest,
    identity,
    *,
    checkpoint_callback=None,
    should_cancel=None,
):
    """Validate one shared Technical cohort manifest once per SQL transaction."""

    payload = source_manifest.manifest_json
    if (
        payload.get("contract") != "technical-source-manifest-v1"
        or payload.get("run_id") != identity.ownership.run_id.value
        or payload.get("pipeline_run_id") != identity.ownership.pipeline_id.value
        or payload.get("calculation_context_id")
        != identity.calculation_context.market_calculation_context_id.value
    ):
        raise ValueError("MUTATION_TECHNICAL_CANONICAL_MANIFEST_SCOPE_MISMATCH")
    transaction = db.get_transaction()
    cache = db.info.setdefault("validated_technical_source_manifests", {})
    if cache.get("transaction") is transaction and source_manifest.manifest_digest in cache.get(
        "digests", set()
    ):
        return
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("MUTATION_EXACT_PIT_PRICE_MANIFEST_REQUIRED")
    should_cancel = should_cancel or (lambda: False)
    for index, item in enumerate(sources, start=1):
        native = item.get("manifest") if isinstance(item, dict) else None
        if (
            not isinstance(native, dict)
            or item.get("digest") != Canonical.fingerprint(native)
        ):
            raise ValueError("MUTATION_TECHNICAL_SOURCE_DIGEST_MISMATCH")
        validate_price_source_manifest(
            db,
            native,
            identity.temporal.calculation_cutoff.value,
            identity.temporal.as_of_session.value,
        )
        if index % 10 == 0 or index == len(sources):
            if checkpoint_callback is not None:
                checkpoint_callback(
                    phase="VALIDATING_EVIDENCE",
                    processed=index,
                    total=len(sources),
                    current_item=item.get("digest"),
                    last_completed_item=item.get("digest"),
                )
            if should_cancel():
                raise ValueError("TECHNICAL_CANCELLED_DURING_EVIDENCE_VALIDATION")
    cache["transaction"] = transaction
    cache["digests"] = {source_manifest.manifest_digest}


def projection_mutation_context(evidence):
    """An explicit immutable target and exact scope, without pointer inference."""
    scope = {
        "kind": evidence.artifact_kind,
        "run_id": evidence.run_id,
        "ticker": evidence.ticker,
        "profile": evidence.ranking_profile or "",
    }
    digest = Canonical.fingerprint(scope)
    domain = MutationDomain.CURRENT_PROJECTION
    ownership = current_domain_write_ownership()
    return DomainMutationContext(
        domain=domain,
        semantic_mode=MutationSemanticMode.CURRENT_PROJECTION_ADVANCE,
        entrypoint=MutationEntryPointDescriptor("persist_core_evidence", "NATIVE_SEMANTIC_WRITER"),
        writer=MutationWriterDescriptor(
            "_advance_current_projection", "phase5-core-writer-v1", domain
        ),
        reason="Advance explicit certified evidence scope",
        evidence=(
            MutationEvidenceReference(
                "target_evidence", "core_calculation_evidence", evidence.id, evidence.evidence_key
            ),
            MutationEvidenceReference("projection_scope", "native_manifest", digest, digest),
        ),
        run_id=evidence.run_id,
        durable=ownership is not None,
        execution=ownership,
    )


def validate_projection_mutation(db, evidence, context):
    with db.no_autoflush:
        persisted = db.get(CoreCalculationEvidence, evidence.id)
        if persisted is None or persisted.evidence_key != evidence.evidence_key:
            raise ValueError("MUTATION_PROJECTION_TARGET_MISSING")
        _validate_evidence(persisted)
        expected = projection_mutation_context(persisted)
        if (
            context.domain is not expected.domain
            or context.writer != expected.writer
            or context.evidence != expected.evidence
            or context.run_id != persisted.run_id
        ):
            raise ValueError("MUTATION_PROJECTION_SCOPE_MISMATCH")
        fence_mutation_transaction(db, context)
        if expected.execution is not None and context.execution != expected.execution:
            raise ValueError("MUTATION_PROJECTION_OWNERSHIP_MISMATCH")
