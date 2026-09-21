"""Winner adoption of the shared scope, refresh, and acquisition identities."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    EntryModel,
    OutcomeStatus,
    RawCompanyRow,
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
)
from app.services.scope_refresh_adoption import (
    SemanticAuthorityError,
    SemanticWorkAuthority,
    admit_frozen_operation,
    retained_scope_members,
)
from app.services.winner_probability.cohort_generation_service import watermark_from_state
from app.services.winner_probability.evidence_service import EvidenceService
from app.services.winner_probability.temporal_eligibility import temporal_eligibility_sql
from app.services.winner_probability.trading_session_service import latest_completed_session
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

WINNER_CAPTURE_MEMBER = "WINNER_CAPTURE_SOURCE_ROW"
WINNER_BACKFILL_MEMBER = "WINNER_BACKFILL_RUN"
WINNER_MATURATION_MEMBER = "WINNER_PREDICTION"
WINNER_COHORT_PREDICTION_MEMBER = "WINNER_COHORT_PREDICTION"
WINNER_COHORT_FORWARD_MEMBER = "WINNER_COHORT_FORWARD_OUTCOME"
WINNER_COHORT_TARGET_MEMBER = "WINNER_COHORT_TARGET_STOP_OUTCOME"


def admit_prediction_capture(
    db: Session,
    *,
    run_id: int,
    cycle_key: str,
    cutoff: datetime,
    configuration_identity: str,
) -> SemanticWorkAuthority:
    row_ids = tuple(
        db.scalars(
            select(RawCompanyRow.id)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number, RawCompanyRow.id)
        )
    )
    return admit_frozen_operation(
        db,
        operation_kind="winner-prediction-capture",
        subject_kind="winner-capture-source-row",
        members=tuple(ScopeMember(WINNER_CAPTURE_MEMBER, value) for value in row_ids),
        cycle_key=cycle_key,
        business_cutoff=cutoff,
        provider_source_class="retained-decision-handoff",
        request_type="winner-prediction-capture",
        requirements=(AcquisitionRequirement("frozen-decision-inputs"),),
        policy_identity="winner-capture-population-v1",
        configuration_identity=configuration_identity,
        scope_definition={"run_id": run_id, "source_row_ids": list(row_ids)},
    )


def capture_source_row_ids(db: Session, authority: SemanticWorkAuthority) -> tuple[int, ...]:
    return _member_ids(db, authority, WINNER_CAPTURE_MEMBER)


def admit_historical_backfill(
    db: Session,
    *,
    run_ids: tuple[int, ...],
    cycle_key: str,
    cutoff: datetime,
    configuration_identity: str,
) -> SemanticWorkAuthority:
    run_ids = tuple(dict.fromkeys(run_ids))
    return admit_frozen_operation(
        db,
        operation_kind="winner-historical-backfill",
        subject_kind="winner-upload-run",
        members=tuple(ScopeMember(WINNER_BACKFILL_MEMBER, value) for value in run_ids),
        cycle_key=cycle_key,
        business_cutoff=cutoff,
        provider_source_class="retained-historical-decision-context",
        request_type="winner-historical-backfill",
        requirements=(AcquisitionRequirement("historical-decision-identity"),),
        policy_identity="winner-historical-backfill-v1",
        configuration_identity=configuration_identity,
        scope_definition={"run_ids": list(run_ids)},
    )


def backfill_run_ids(db: Session, authority: SemanticWorkAuthority) -> tuple[int, ...]:
    return _member_ids(db, authority, WINNER_BACKFILL_MEMBER)


def admit_maturation(
    db: Session,
    *,
    cycle_key: str,
    operation_cutoff: datetime,
    due_session: date | None,
    configuration_identity: str,
) -> SemanticWorkAuthority:
    if operation_cutoff.tzinfo is None or operation_cutoff.utcoffset() is None:
        raise ValueError("MUTATION_AWARE_OPERATION_CUTOFF_REQUIRED")
    completed_on = min(
        latest_completed_session(operation_cutoff),
        due_session or latest_completed_session(operation_cutoff),
    )
    prediction_ids = tuple(
        dict.fromkeys(
            db.scalars(
                select(WinnerForwardOutcome.prediction_id)
                .join(
                    WinnerPredictionSnapshot,
                    WinnerPredictionSnapshot.id == WinnerForwardOutcome.prediction_id,
                )
                .where(WinnerForwardOutcome.status == OutcomeStatus.PENDING)
                .where(WinnerForwardOutcome.is_current_revision.is_(True))
                .where(WinnerForwardOutcome.entry_model == EntryModel.NEXT_OPEN)
                .where(WinnerForwardOutcome.horizon_sessions == 5)
                .where(WinnerForwardOutcome.due_session <= completed_on)
                .where(temporal_eligibility_sql(WinnerPredictionSnapshot))
                .order_by(WinnerForwardOutcome.due_session, WinnerForwardOutcome.id)
            )
        )
    )
    return admit_frozen_operation(
        db,
        operation_kind="winner-h5-maturation",
        subject_kind="winner-prediction",
        members=tuple(ScopeMember(WINNER_MATURATION_MEMBER, value) for value in prediction_ids),
        cycle_key=cycle_key,
        business_cutoff=operation_cutoff,
        provider_source_class="retained-price-observation-policy",
        request_type="winner-h5-next-open-maturation",
        requirements=(
            AcquisitionRequirement("price-bars"),
            AcquisitionRequirement("price-bar-revision-identity"),
        ),
        policy_identity="winner-h5-next-open-maturation-v1",
        configuration_identity=configuration_identity,
        scope_definition={
            "entry_model": EntryModel.NEXT_OPEN,
            "horizon_sessions": 5,
            "completed_session": completed_on.isoformat(),
            "operation_cutoff_at": operation_cutoff.isoformat(),
            "prediction_ids": list(prediction_ids),
        },
    )


def maturation_prediction_ids(db: Session, authority: SemanticWorkAuthority) -> tuple[int, ...]:
    return _member_ids(db, authority, WINNER_MATURATION_MEMBER)


def maturation_remaining_members(
    db: Session, authority: SemanticWorkAuthority
) -> tuple[ScopeMember, ...]:
    prediction_ids = maturation_prediction_ids(db, authority)
    if not prediction_ids:
        return ()
    pending = set(
        db.scalars(
            select(WinnerForwardOutcome.prediction_id)
            .where(WinnerForwardOutcome.prediction_id.in_(prediction_ids))
            .where(WinnerForwardOutcome.entry_model == EntryModel.NEXT_OPEN)
            .where(WinnerForwardOutcome.horizon_sessions == 5)
            .where(WinnerForwardOutcome.status == OutcomeStatus.PENDING)
            .where(WinnerForwardOutcome.is_current_revision.is_(True))
        )
    )
    return tuple(
        ScopeMember(WINNER_MATURATION_MEMBER, prediction_id)
        for prediction_id in prediction_ids
        if prediction_id in pending
    )


def admit_cohort_refresh(
    db: Session,
    *,
    state: WinnerCohortRefreshState,
    outcome_definition: WinnerOutcomeDefinition,
    config,
    cycle_key: str,
    observed_at: datetime,
) -> SemanticWorkAuthority:
    cutoff = state.updated_at + _one_microsecond()
    universe = EvidenceService().load_generation_evidence(
        db,
        outcome_definition=outcome_definition,
        training_cutoff_at=cutoff,
        config=config,
        watermark=watermark_from_state(state).as_dict(),
    )
    members: list[ScopeMember] = []
    for evidence in universe.evidence:
        members.extend(
            (
                ScopeMember(WINNER_COHORT_PREDICTION_MEMBER, evidence.prediction.id),
                ScopeMember(WINNER_COHORT_FORWARD_MEMBER, evidence.forward_outcome.id),
                ScopeMember(WINNER_COHORT_TARGET_MEMBER, evidence.target_stop_outcome.id),
            )
        )
    # A prediction can legitimately participate through one native and one
    # compatibility record; scope membership is a set, not a row-count ledger.
    unique = tuple({member.key: member for member in members}.values())
    return admit_frozen_operation(
        db,
        operation_kind="winner-cohort-generation",
        subject_kind="winner-cohort-evidence",
        members=unique,
        cycle_key=cycle_key,
        business_cutoff=observed_at,
        provider_source_class="retained-winner-evidence",
        request_type="winner-cohort-refresh",
        requirements=(AcquisitionRequirement("winner-outcome-evidence"),),
        policy_identity="winner-cohort-generation-population-v1",
        configuration_identity=config.config_hash,
        scope_definition={
            "refresh_state_id": state.id,
            "outcome_definition_id": outcome_definition.id,
            "desired_watermark_hash": state.desired_watermark_hash,
            "watermark": watermark_from_state(state).as_dict(),
            "training_cutoff_at": cutoff.isoformat(),
            "evidence_rows": len(universe.evidence),
        },
    )


def validate_generation_scope(
    db: Session,
    *,
    generation: WinnerCohortGeneration,
    authority: SemanticWorkAuthority,
) -> None:
    retained = retained_scope_members(db, authority.scope_id)
    scoped = {
        subject_type: {
            int(candidate.subject_id)
            for candidate in retained
            if candidate.subject_type == subject_type
        }
        for subject_type in (
            WINNER_COHORT_PREDICTION_MEMBER,
            WINNER_COHORT_FORWARD_MEMBER,
            WINNER_COHORT_TARGET_MEMBER,
        )
    }
    if generation.root_manifest_hash is None:
        return
    from app.models.tables import WinnerEvidenceManifest, WinnerEvidenceManifestMember

    realized_rows = list(
        db.execute(
            select(
                WinnerEvidenceManifestMember.prediction_id,
                WinnerEvidenceManifestMember.forward_outcome_id,
                WinnerEvidenceManifestMember.target_stop_outcome_id,
            )
            .join(
                WinnerEvidenceManifest,
                WinnerEvidenceManifest.id == WinnerEvidenceManifestMember.manifest_id,
            )
            .where(WinnerEvidenceManifest.manifest_hash == generation.root_manifest_hash)
        )
    )
    realized = {
        WINNER_COHORT_PREDICTION_MEMBER: {int(row[0]) for row in realized_rows},
        WINNER_COHORT_FORWARD_MEMBER: {int(row[1]) for row in realized_rows},
        WINNER_COHORT_TARGET_MEMBER: {int(row[2]) for row in realized_rows},
    }
    if realized != scoped:
        raise SemanticAuthorityError("WINNER_GENERATION_SCOPE_MEMBERSHIP_MISMATCH")


def _member_ids(
    db: Session, authority: SemanticWorkAuthority, subject_type: str
) -> tuple[int, ...]:
    members = retained_scope_members(db, authority.scope_id)
    unexpected = [member for member in members if member.subject_type != subject_type]
    if unexpected:
        raise SemanticAuthorityError("WINNER_SCOPE_MEMBER_TYPE_MISMATCH")
    return tuple(int(member.subject_id) for member in members)


def _one_microsecond():
    from datetime import timedelta

    return timedelta(microseconds=1)
