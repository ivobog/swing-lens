"""Certify the current generation against its persisted owner and frozen anchor."""

from typing import Any

from sqlalchemy import select

from app.models.ceri_tables import CeriCompany, CeriScoreSnapshot, CeriSourceRecord
from app.models.tables import CoreCalculationEvidence, PipelineRun, RawCompanyRow
from app.services.ceri.constants import PURGE_INVALIDATION_FLAG
from app.services.ceri.evidence_eligibility import eligible_snapshot_select
from app.services.ceri.pit_eligibility import source_record_is_eligible
from app.services.ceri.score_ownership import bound_pipeline_id
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.configuration_delivery import binding_reference, load_configuration_delivery
from app.services.contextual_calculation_identity import (
    CERI_CONTEXT_COMPATIBILITY,
    consumer_context_identity,
    contextual_compatibility,
)
from app.services.contextual_effective_configuration import ContextualEffectiveConfiguration
from app.services.market_calculation_context_service import (
    resolve_pipeline_ceri_context,
    resolve_pipeline_market_context,
)


def certified_scores(db, owner_id, context_id, *, expected_ids=None):
    owner = db.get(PipelineRun, owner_id)
    if owner is None or owner.execution_authority_state in {"QUARANTINED", "RETIRED"}:
        raise ValueError("CERI_CAPTURE_OWNER_MISMATCH")
    retained = (owner.result_json or {}).get("ceri_calculation_context_id")
    resolver = (
        resolve_pipeline_ceri_context if retained is not None else resolve_pipeline_market_context
    )
    cutoff = resolver(
        db,
        pipeline_run_id=owner_id,
        upload_run_id=owner.upload_run_id,
        calculation_context_id=context_id,
    )
    if db.scalar(select(bound_pipeline_id(owner.upload_run_id, context_id))) != owner_id:
        raise ValueError("CERI_CAPTURE_OWNER_MISMATCH")
    anchor = binding_reference(db, pipeline_run_id=owner_id)
    family = load_configuration_delivery(db, anchor).configurations["contextual.ceri"]
    frozen = ContextualEffectiveConfiguration(family.snapshot)
    config = frozen.ceri_config()
    expected = set(
        db.scalars(select(RawCompanyRow.ticker).where(RawCompanyRow.run_id == owner.upload_run_id))
    )
    matches = db.execute(
        eligible_snapshot_select(CeriScoreSnapshot, CoreCalculationEvidence)
        .join(CoreCalculationEvidence, CoreCalculationEvidence.id == CeriScoreSnapshot.evidence_id)
        .join(CeriCompany, CeriCompany.id == CeriScoreSnapshot.company_id)
        .where(
            CoreCalculationEvidence.artifact_kind == "CERI",
            CoreCalculationEvidence.run_id == CeriScoreSnapshot.run_id,
            CoreCalculationEvidence.ticker == CeriScoreSnapshot.ticker,
            CeriCompany.ticker == CeriScoreSnapshot.ticker,
            CeriScoreSnapshot.run_id == owner.upload_run_id,
            CeriScoreSnapshot.calculation_context_id == context_id,
            CeriScoreSnapshot.controlled_replay_id.is_(None),
        )
        .order_by(CeriScoreSnapshot.ticker, CeriScoreSnapshot.id)
    ).all()
    rows = [row for row, _ in matches]
    if not rows or len(rows) != len(expected) or {row.ticker for row in rows} != expected:
        raise ValueError("CERI_CERTIFIED_CAPTURE_INCOMPLETE")
    if expected_ids is not None and sorted(expected_ids) != sorted(row.id for row in rows):
        raise ValueError("CERI_CERTIFIED_CAPTURE_ID_MISMATCH")
    for row, evidence in matches:
        identity = calculation_identity_from_debug(row.evidence_lineage_json)
        expected_identity = consumer_context_identity(
            market_cutoff=cutoff,
            run_id=owner.upload_run_id,
            pipeline_id=owner_id,
            ticker=row.ticker,
            company_id=row.company_id,
        )
        if (
            PURGE_INVALIDATION_FLAG in (row.warnings_json or [])
            or identity is None
            or identity.ownership.pipeline_id.value != owner_id
            or identity.configuration != frozen.bind(identity).configuration
            or str(identity.fingerprint()) != evidence.calculation_identity_fingerprint
            or not contextual_compatibility(
                expected_identity, identity, policy=CERI_CONTEXT_COMPATIBILITY
            ).accepted
            or row.config_hash != config.config_hash
            or row.calculation_version != config.engine.calculation_version
            or row.cutoff_at != cutoff.cutoff_at
            or row.calendar_version != cutoff.calendar_version
            or row.as_of_session != cutoff.latest_completed_session
        ):
            raise ValueError("CERI_CERTIFIED_SCORE_IDENTITY_MISMATCH")
        validate_score_seal(db, row, evidence=evidence)
    failures = source_lineage_failures(db, rows, cutoff)
    if failures:
        raise ValueError("CERI_CERTIFIED_SOURCE_LINEAGE_MISMATCH: " + ",".join(failures[:25]))
    return rows


def validate_native_snapshot_fields(row, frozen=None, *, db=None):
    """Bind the physical native key and output columns to the sealed identity."""
    from app.services.calculation_identity import IdentityState

    identity = calculation_identity_from_debug(row.evidence_lineage_json)
    if identity is None:
        raise ValueError("CERI_NATIVE_SNAPSHOT_IDENTITY_REQUIRED")
    for dimension, expected in (
        (identity.ownership.run_id, row.run_id),
        (identity.subject.company_id, row.company_id),
        (identity.subject.ticker, row.ticker),
        (identity.calculation_context.market_calculation_context_id, row.calculation_context_id),
        (identity.temporal.as_of_session, row.as_of_session),
        (identity.temporal.calculation_cutoff, row.cutoff_at),
    ):
        if dimension.state is not IdentityState.KNOWN or dimension.value != expected:
            raise ValueError("CERI_NATIVE_SNAPSHOT_IDENTITY_MISMATCH")
    version = identity.algorithm.calculation_version
    calendar = identity.temporal.calendar
    if (
        version.state is not IdentityState.KNOWN
        or version.value.version != row.calculation_version
        or calendar.state is not IdentityState.KNOWN
        or calendar.value.calendar_version != row.calendar_version
    ):
        raise ValueError("CERI_NATIVE_SNAPSHOT_IDENTITY_MISMATCH")
    if frozen is not None and identity.configuration != frozen.bind(identity).configuration:
        raise ValueError("CERI_SAME_CONTEXT_CONFIGURATION_MISMATCH")
    if db is not None:
        from app.models.tables import MarketCalculationContext
        from app.services.market_calculation_context_service import (
            cutoff_from_row,
            market_calculation_context_fingerprint,
        )

        context = db.get(MarketCalculationContext, row.calculation_context_id)
        fingerprint = identity.calculation_context.context_fingerprint
        if (
            context is None
            or context.upload_run_id != row.run_id
            or context.cutoff_at != row.cutoff_at
            or context.latest_completed_session != row.as_of_session
            or context.calendar_version != row.calendar_version
            or fingerprint.state is not IdentityState.KNOWN
            or fingerprint.value.digest
            != market_calculation_context_fingerprint(cutoff_from_row(context))
        ):
            raise ValueError("CERI_NATIVE_SNAPSHOT_CONTEXT_MISMATCH")


def validate_score_seal(db, row, *, evidence=None):
    """Verify immutable envelope hashes and the compatibility row's sealed output.

    Comparison pointers are deliberately mutable projections; the financial
    result and frozen identity remain bound to the original immutable envelope.
    """
    from app.services.ceri.decision_evidence import _row_payload
    from app.services.core_mutation_authority import validate_evidence

    if row.calculation_context_id is not None and row.controlled_replay_id is None:
        validate_native_snapshot_fields(row, db=db)
    evidence = evidence or db.get(CoreCalculationEvidence, row.evidence_id)
    validate_evidence(evidence)
    if (
        evidence.artifact_kind != "CERI"
        or evidence.run_id != row.run_id
        or evidence.ticker != row.ticker
    ):
        raise ValueError("CERI_CERTIFIED_SCORE_SEAL_MISMATCH")
    mutable = {"comparison_state", "comparison_snapshot_id"}
    actual = _row_payload(row, excluded={"id", "evidence_id", *mutable})
    sealed = {
        key: value
        for key, value in evidence.payload_json.get("decision_output", {}).items()
        if key not in mutable
    }
    if actual != sealed:
        raise ValueError("CERI_CERTIFIED_SCORE_SEAL_MISMATCH")


def source_lineage_failures(db, rows, cutoff):
    """Check the referenced provider records against their frozen knowledge time."""
    failures = []
    ceri_source_ids: set[int] = set()
    for row in rows:
        _collect_source_ids(row.evidence_lineage_json or {}, ceri_source_ids)
        _collect_source_ids(row.component_json or {}, ceri_source_ids)
    evidence_ids = {row.evidence_id for row in rows if row.evidence_id is not None}
    for evidence in db.scalars(
        select(CoreCalculationEvidence).where(CoreCalculationEvidence.id.in_(evidence_ids))
    ):
        ceri_source_ids.update(
            record["id"]
            for record in evidence.payload_json.get("source_manifest", {}).get("source_records", [])
        )
    if ceri_source_ids:
        source_rows = list(
            db.scalars(select(CeriSourceRecord).where(CeriSourceRecord.id.in_(ceri_source_ids)))
        )
        found = {row.id for row in source_rows}
        if found != ceri_source_ids:
            failures.append("ceri:source_record_missing")
        for row in source_rows:
            if not source_record_is_eligible(row, cutoff.cutoff_at):
                failures.append(f"ceri:post_cutoff_source:{row.id}")
    return failures


def _collect_source_ids(value: Any, target: set[int], *, key: str | None = None) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _collect_source_ids(child, target, key=str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_source_ids(child, target, key=key)
    elif key is not None and "source_id" in key and isinstance(value, int):
        target.add(value)
