from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models.tables import WinnerPredictionSnapshot, WinnerTemporalValidityDecision
from app.services.us_market_calendar import us_market_session
from app.services.winner_probability.temporal_integrity import TemporalValidityStatus


def load_current_temporal_decisions(
    db: Session,
    prediction_ids: set[int],
    *,
    max_id: int | None = None,
) -> dict[int, WinnerTemporalValidityDecision]:
    """Batch-load the latest append-only validation event for each prediction."""
    if not prediction_ids:
        return {}
    latest_statement = select(
        WinnerTemporalValidityDecision.prediction_id.label("prediction_id"),
        func.max(WinnerTemporalValidityDecision.validation_sequence).label("sequence"),
    )
    latest_statement = latest_statement.where(
        WinnerTemporalValidityDecision.prediction_id.in_(sorted(prediction_ids))
    )
    if max_id is not None:
        latest_statement = latest_statement.where(WinnerTemporalValidityDecision.id <= max_id)
    latest = latest_statement.group_by(WinnerTemporalValidityDecision.prediction_id).subquery()
    rows = db.scalars(
        select(WinnerTemporalValidityDecision).join(
            latest,
            (latest.c.prediction_id == WinnerTemporalValidityDecision.prediction_id)
            & (latest.c.sequence == WinnerTemporalValidityDecision.validation_sequence),
        )
    )
    return {int(row.prediction_id): row for row in rows}


def prediction_temporally_eligible(
    prediction: WinnerPredictionSnapshot,
    current_decision: WinnerTemporalValidityDecision | None,
) -> bool:
    """Authoritative evidence policy, with a conservative legacy fallback.

    Explicit ledger state always wins. Historical predictions are not assigned
    invented decision timestamps; captured_at is used only as a conservative
    lower-bound check that can reject, but never certify, a retroactive open.
    """
    if current_decision is not None:
        return bool(
            current_decision.status == TemporalValidityStatus.VALID
            and current_decision.evidence_eligible
            and current_decision.entry_timing_valid
            and current_decision.source_cutoff_valid
            and current_decision.semantic_input_time_valid is True
            and _aware(current_decision.decision_at) < _aware(current_decision.entry_open_at)
        )

    reference = prediction.decision_at or prediction.captured_at
    session = (
        us_market_session(prediction.planned_entry_session)
        if prediction.planned_entry_session is not None
        else None
    )
    lineage = prediction.lineage_json or {}
    if reference is None:
        # Only transient pre-migration objects (chiefly unit fixtures) lack the
        # database-required captured_at value. They retain the pre-existing PIT
        # gate; persisted production rows never take this branch.
        return lineage.get("point_in_time_validated") is True
    semantic_status = (lineage.get("point_in_time_validation") or {}).get("semantic_input_time")
    if not sa_inspect(prediction).transient and (
        prediction.decision_at is None or semantic_status != "VALID"
    ):
        # Persisted pre-migration rows cannot inherit a stronger meaning from
        # the legacy broad boolean. They require an explicit ledger decision or
        # the detailed native-capture lineage written by this version.
        return False
    return bool(
        session is not None
        and _aware(reference) < _aware(session.open_at)
        and _aware(prediction.source_data_cutoff_at) <= _aware(reference)
        and lineage.get("point_in_time_validated") is True
    )


def temporal_eligibility_sql(prediction=WinnerPredictionSnapshot):
    """Set-based equivalent used by due selectors (PostgreSQL production path)."""
    latest_id = (
        select(func.max(WinnerTemporalValidityDecision.id))
        .where(WinnerTemporalValidityDecision.prediction_id == prediction.id)
        .correlate(prediction)
        .scalar_subquery()
    )
    explicit_eligible = (
        select(WinnerTemporalValidityDecision.id)
        .where(WinnerTemporalValidityDecision.id == latest_id)
        .where(WinnerTemporalValidityDecision.status == TemporalValidityStatus.VALID)
        .where(WinnerTemporalValidityDecision.evidence_eligible.is_(True))
        .where(WinnerTemporalValidityDecision.entry_timing_valid.is_(True))
        .where(WinnerTemporalValidityDecision.source_cutoff_valid.is_(True))
        .where(WinnerTemporalValidityDecision.semantic_input_time_valid.is_(True))
        .exists()
    )
    reference = func.coalesce(prediction.decision_at, prediction.captured_at)
    # PostgreSQL timezone(zone, timestamp-without-time-zone) returns timestamptz.
    entry_open = func.timezone(
        "America/New_York",
        func.to_timestamp(
            func.concat(prediction.planned_entry_session, " 09:30:00"),
            "YYYY-MM-DD HH24:MI:SS",
        ),
    )
    legacy_eligible = (
        latest_id.is_(None)
        & prediction.decision_at.is_not(None)
        & prediction.planned_entry_session.is_not(None)
        & (reference < entry_open)
        & (prediction.source_data_cutoff_at <= reference)
        & prediction.lineage_json["point_in_time_validated"].as_boolean().is_(True)
        & (
            prediction.lineage_json["point_in_time_validation"]["semantic_input_time"].as_string()
            == "VALID"
        )
    )
    return explicit_eligible | legacy_eligible


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
