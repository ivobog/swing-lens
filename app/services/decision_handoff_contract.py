from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.models.tables import MarketRegimeSnapshot, SectorRotationSnapshot
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.core_calculation_evidence import CoreEvidenceKind


@dataclass(frozen=True)
class RequiredHandoffContextArtifact:
    key: str
    kind: CoreEvidenceKind
    model: type


REQUIRED_HANDOFF_CONTEXT_ARTIFACTS = (
    RequiredHandoffContextArtifact(
        key="market_regime_snapshot",
        kind=CoreEvidenceKind.REGIME,
        model=MarketRegimeSnapshot,
    ),
    RequiredHandoffContextArtifact(
        key="sector_rotation_snapshot",
        kind=CoreEvidenceKind.SECTOR,
        model=SectorRotationSnapshot,
    ),
)


class RequiredHandoffArtifactError(ValueError):
    def __init__(
        self,
        reason: str,
        *,
        ticker: str,
        artifact_type: str,
        upload_run_id: int,
        pipeline_run_id: int | None,
        calculation_context_id: int | None,
        artifact_id: int | None = None,
    ) -> None:
        self.reason = reason
        self.ticker = ticker
        self.artifact_type = artifact_type
        self.upload_run_id = upload_run_id
        self.pipeline_run_id = pipeline_run_id
        self.calculation_context_id = calculation_context_id
        self.artifact_id = artifact_id
        super().__init__(
            f"{reason} ticker={ticker} artifact={artifact_type} "
            f"upload_run_id={upload_run_id} pipeline_run_id={pipeline_run_id} "
            f"context_id={calculation_context_id} artifact_id={artifact_id}"
        )


_IDENTITY_NOT_SUPPLIED = object()


def handoff_artifact_identity(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": getattr(row, "id", None),
        "semantic_hash": CanonicalEvidenceSerializer.fingerprint(_semantic_payload(row)),
    }


def validate_required_handoff_context_artifact(
    row: Any | None,
    *,
    definition: RequiredHandoffContextArtifact,
    ticker: str,
    upload_run_id: int,
    pipeline_run_id: int | None,
    calculation_context_id: int | None,
    cutoff_at: datetime,
    input_as_of_session: date,
    calendar_version: str,
    expected_identity: Any = _IDENTITY_NOT_SUPPLIED,
    allow_current_revision_transition: bool = False,
) -> None:
    """Apply the required market-context contract at freeze and resume boundaries."""

    if expected_identity is not _IDENTITY_NOT_SUPPLIED and (
        not isinstance(expected_identity, dict)
        or not isinstance(expected_identity.get("id"), int)
        or not isinstance(expected_identity.get("semantic_hash"), str)
    ):
        raise _problem(
            "required_reference_absent",
            definition=definition,
            ticker=ticker,
            upload_run_id=upload_run_id,
            pipeline_run_id=pipeline_run_id,
            calculation_context_id=calculation_context_id,
        )
    expected_id = (
        int(expected_identity["id"])
        if isinstance(expected_identity, dict)
        else None
    )
    if row is None:
        raise _problem(
            "referenced_database_row_unavailable"
            if expected_identity is not _IDENTITY_NOT_SUPPLIED
            else "required_artifact_unavailable",
            definition=definition,
            ticker=ticker,
            upload_run_id=upload_run_id,
            pipeline_run_id=pipeline_run_id,
            calculation_context_id=calculation_context_id,
            artifact_id=expected_id,
        )
    if not isinstance(row, definition.model):
        raise _problem(
            "artifact_identity_invalid",
            definition=definition,
            ticker=ticker,
            upload_run_id=upload_run_id,
            pipeline_run_id=pipeline_run_id,
            calculation_context_id=calculation_context_id,
            artifact_id=getattr(row, "id", expected_id),
        )

    if expected_identity is not _IDENTITY_NOT_SUPPLIED:
        actual = handoff_artifact_identity(row)
        identity_matches = actual == expected_identity
        if not identity_matches and allow_current_revision_transition:
            normalized = _semantic_payload(row)
            normalized.update(
                {
                    "is_current_revision": True,
                    "superseded_by_snapshot_id": None,
                    "superseded_at": None,
                }
            )
            identity_matches = (
                getattr(row, "id", None) == expected_id
                and CanonicalEvidenceSerializer.fingerprint(normalized)
                == expected_identity.get("semantic_hash")
            )
        if not identity_matches:
            raise _problem(
                "artifact_identity_invalid",
                definition=definition,
                ticker=ticker,
                upload_run_id=upload_run_id,
                pipeline_run_id=pipeline_run_id,
                calculation_context_id=calculation_context_id,
                artifact_id=getattr(row, "id", expected_id),
            )

    ownership_matches = (
        row.run_id == upload_run_id
        and row.calculation_context_id == calculation_context_id
        and _canonical(row.calculation_cutoff_at) == _canonical(cutoff_at)
        and row.input_as_of_session == input_as_of_session
        and row.calendar_version == calendar_version
    )
    if not ownership_matches:
        raise _problem(
            "artifact_ownership_or_temporal_identity_invalid",
            definition=definition,
            ticker=ticker,
            upload_run_id=upload_run_id,
            pipeline_run_id=pipeline_run_id,
            calculation_context_id=calculation_context_id,
            artifact_id=getattr(row, "id", expected_id),
        )


def _semantic_payload(row: Any) -> dict[str, Any]:
    return {
        column.name: getattr(row, column.name, None)
        for column in row.__table__.columns
        if column.name not in {"created_at", "updated_at", "last_seen_at", "calculated_at"}
    }


def _canonical(value: Any) -> Any:
    return CanonicalEvidenceSerializer.canonicalize(value)


def _problem(
    reason: str,
    *,
    definition: RequiredHandoffContextArtifact,
    ticker: str,
    upload_run_id: int,
    pipeline_run_id: int | None,
    calculation_context_id: int | None,
    artifact_id: int | None = None,
) -> RequiredHandoffArtifactError:
    return RequiredHandoffArtifactError(
        reason,
        ticker=ticker,
        artifact_type=definition.key,
        upload_run_id=upload_run_id,
        pipeline_run_id=pipeline_run_id,
        calculation_context_id=calculation_context_id,
        artifact_id=artifact_id,
    )
