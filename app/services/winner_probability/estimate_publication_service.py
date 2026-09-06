"""Atomic, reviewed publication of clean Winner estimate replacements."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.tables import (
    EstimateKind,
    EstimateLifecycleStatus,
    EvidenceGrade,
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerEstimatePublicationRequest,
    WinnerProbabilityEstimate,
)
from app.services.winner_probability.estimate_lifecycle import estimate_is_serving
from app.services.winner_probability.temporal_manifest_canonicalization import (
    canonical_manifest_bytes,
)

NO_CLEAN_EVIDENCE_AT_ORIGINAL_CUTOFF = "no_clean_evidence_at_original_decision_cutoff"


class DecisionReconstructionCategory:
    DIRECTLY_CONTAMINATED = "DIRECTLY_CONTAMINATED"
    LEGACY_EVIDENCE_UNVERIFIABLE = "LEGACY_EVIDENCE_UNVERIFIABLE"
    NO_ORIGINAL_EVIDENCE = "NO_ORIGINAL_EVIDENCE"
    OTHER_POINT_IN_TIME_UNRECONSTRUCTABLE = "OTHER_POINT_IN_TIME_UNRECONSTRUCTABLE"


class PublicationTransitionCategory:
    DECISION_TIME_TO_INSUFFICIENT = "DECISION_TIME_TO_INSUFFICIENT"
    LATEST_RESCORE_TO_CLEAN_COHORT = "LATEST_RESCORE_TO_CLEAN_COHORT"
    LATEST_RESCORE_TO_INSUFFICIENT = "LATEST_RESCORE_TO_INSUFFICIENT"


class PublicationInvariantViolation(RuntimeError):
    """A reviewed Winner publication precondition was not satisfied."""


def classify_decision_reconstruction(
    *,
    member_count: int,
    invalid_member_count: int,
    unverifiable_member_count: int,
) -> str:
    """Classify why an original point-in-time estimate cannot be reconstructed."""

    if invalid_member_count:
        return DecisionReconstructionCategory.DIRECTLY_CONTAMINATED
    if member_count == 0:
        return DecisionReconstructionCategory.NO_ORIGINAL_EVIDENCE
    if unverifiable_member_count:
        return DecisionReconstructionCategory.LEGACY_EVIDENCE_UNVERIFIABLE
    return DecisionReconstructionCategory.OTHER_POINT_IN_TIME_UNRECONSTRUCTABLE


def validate_clean_insufficient_replacement(estimate: Any) -> None:
    """Fail closed unless a historical replacement represents honest absence."""

    if estimate.estimate_kind != EstimateKind.DECISION_TIME:
        raise PublicationInvariantViolation(
            "clean historical insufficient replacement must be DECISION_TIME"
        )
    if any(
        getattr(estimate, field, None) is not None
        for field in ("point_probability", "lower_bound", "upper_bound", "interval_width")
    ):
        raise PublicationInvariantViolation(
            "clean historical insufficient replacement contains a numeric probability"
        )
    if estimate.evidence_grade != EvidenceGrade.INSUFFICIENT:
        raise PublicationInvariantViolation(
            "clean historical insufficient replacement must have Insufficient grade"
        )
    reasons = set(estimate.insufficient_reasons_json or ())
    if NO_CLEAN_EVIDENCE_AT_ORIGINAL_CUTOFF not in reasons:
        raise PublicationInvariantViolation(
            "clean historical insufficient replacement is missing the required reason"
        )


def transition_manifest_hash(manifest: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "artifact_hash"}
    return hashlib.sha256(canonical_manifest_bytes(payload)).hexdigest()


def serving_id_snapshot(db: Session) -> dict[str, Any]:
    ids = tuple(
        int(value)
        for value in db.scalars(
            select(WinnerProbabilityEstimate.id)
            .where(estimate_is_serving())
            .order_by(WinnerProbabilityEstimate.id)
        )
    )
    digest = hashlib.sha256()
    for value in ids:
        digest.update(str(value).encode())
        digest.update(b"\n")
    return {"count": len(ids), "sha256": digest.hexdigest(), "ids": ids}


class WinnerEstimatePublicationService:
    """Apply a reviewed clean-generation transition in the caller's transaction."""

    def publish(
        self,
        db: Session,
        *,
        manifest: Mapping[str, Any],
        reviewed_manifest_hash: str,
        candidate_manifest_hash: str,
        actor: str,
        request_key: str,
        approve_write: bool,
        published_at: datetime | None = None,
        stage_hook: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if not approve_write:
            raise PermissionError("explicit approve_write=True is required")
        if not actor.strip() or not request_key.strip():
            raise ValueError("actor and request key are required")
        if transition_manifest_hash(manifest) != reviewed_manifest_hash:
            raise PublicationInvariantViolation("reviewed transition manifest hash mismatch")
        if manifest.get("artifact_hash") != reviewed_manifest_hash:
            raise PublicationInvariantViolation("manifest does not identify the reviewed hash")
        if manifest.get("candidate_manifest_hash") != candidate_manifest_hash:
            raise PublicationInvariantViolation("candidate manifest hash mismatch")

        existing_request = db.scalar(
            select(WinnerEstimatePublicationRequest).where(
                WinnerEstimatePublicationRequest.request_key == request_key
            )
        )
        if existing_request is not None:
            if (
                existing_request.transition_manifest_hash != reviewed_manifest_hash
                or existing_request.candidate_manifest_hash != candidate_manifest_hash
                or existing_request.generation_key != manifest["generation"]["generation_key"]
            ):
                raise PublicationInvariantViolation(
                    "request key was already used for different publication inputs"
                )
            return dict(existing_request.result_json)

        records = list(manifest.get("records") or ())
        if not records:
            raise PublicationInvariantViolation("publication manifest has no transitions")
        candidate_ids = tuple(sorted(int(row["candidate_estimate_id"]) for row in records))
        original_ids = tuple(sorted(int(row["original_estimate_id"]) for row in records))
        if len(candidate_ids) != len(set(candidate_ids)) or len(original_ids) != len(
            set(original_ids)
        ):
            raise PublicationInvariantViolation("publication replacement mapping is ambiguous")
        if len(records) != int(manifest["candidate_count"]):
            raise PublicationInvariantViolation("publication candidate count mismatch")

        generation = db.scalar(
            select(WinnerCohortGeneration)
            .where(WinnerCohortGeneration.id == int(manifest["generation"]["id"]))
            .with_for_update()
        )
        previous = db.scalar(
            select(WinnerCohortGeneration)
            .where(WinnerCohortGeneration.id == int(manifest["previous_generation"]["id"]))
            .with_for_update()
        )
        if generation is None or previous is None:
            raise PublicationInvariantViolation("reviewed generation disappeared")
        self._validate_generation(generation, previous, manifest)

        refresh_states = {
            int(row.id): row
            for row in db.scalars(
                select(WinnerCohortRefreshState)
                .where(
                    WinnerCohortRefreshState.id.in_(
                        sorted({generation.refresh_state_id, previous.refresh_state_id})
                    )
                )
                .with_for_update()
            )
        }
        old_state = refresh_states.get(int(previous.refresh_state_id))
        new_state = refresh_states.get(int(generation.refresh_state_id))
        if old_state is None or new_state is None:
            raise PublicationInvariantViolation("cohort refresh state disappeared")
        if old_state.published_generation_id != previous.id:
            raise PublicationInvariantViolation("current published-generation pointer drifted")
        if new_state.id != old_state.id and new_state.published_generation_id is not None:
            raise PublicationInvariantViolation("target refresh state already has a publication")

        candidates = self._locked_estimates(db, candidate_ids)
        originals = self._locked_estimates(db, original_ids)
        if set(candidates) != set(candidate_ids) or set(originals) != set(original_ids):
            raise PublicationInvariantViolation("publication estimate set is incomplete")
        self._validate_estimates(
            records,
            originals,
            candidates,
            generation,
            candidate_manifest_hash=candidate_manifest_hash,
        )
        self._validate_global_preconditions(db, manifest)
        serving_before = serving_id_snapshot(db)
        if _without_ids(serving_before) != manifest["serving_before"]:
            raise PublicationInvariantViolation("served original estimate set drifted")

        at = published_at or datetime.now(UTC)
        previous.status = "SUPERSEDED"
        previous.completed_at = at
        generation.status = "PUBLISHED"
        generation.published_at = at
        generation.completed_at = at
        db.flush()
        _call_hook(stage_hook, "generation_switch")

        for original in originals.values():
            original.lifecycle_status = EstimateLifecycleStatus.SUPERSEDED
            original.superseded_at = at
        for candidate in candidates.values():
            candidate.lifecycle_status = EstimateLifecycleStatus.PUBLISHED
            candidate.published_at = at
        db.flush()
        _call_hook(stage_hook, "estimate_switch")

        if old_state.id != new_state.id:
            old_state.published_generation_id = None
            old_state.published_watermark_hash = None
        new_state.published_generation_id = generation.id
        new_state.published_watermark_hash = generation.watermark_hash
        db.flush()
        _call_hook(stage_hook, "pointer_switch")

        serving_after = serving_id_snapshot(db)
        expected_after = tuple(
            sorted((set(serving_before["ids"]) - set(original_ids)) | set(candidate_ids))
        )
        if serving_after["ids"] != expected_after:
            raise PublicationInvariantViolation("unexpected future serving-set transition")
        if _without_ids(serving_after) != manifest["serving_after"]:
            raise PublicationInvariantViolation("future serving-set hash disagrees with review")

        result = {
            "generation_id": int(generation.id),
            "previous_generation_id": int(previous.id),
            "published_candidates": len(candidate_ids),
            "superseded_originals": len(original_ids),
            "serving_before": _without_ids(serving_before),
            "serving_after": _without_ids(serving_after),
        }
        request = WinnerEstimatePublicationRequest(
            request_key=request_key,
            actor=actor,
            generation_id=generation.id,
            previous_generation_id=previous.id,
            generation_key=generation.generation_key,
            transition_manifest_hash=reviewed_manifest_hash,
            candidate_manifest_hash=candidate_manifest_hash,
            status="COMPLETED",
            result_json=result,
            completed_at=at,
        )
        db.add(request)
        db.flush()
        _call_hook(stage_hook, "request_recorded")
        return result

    @staticmethod
    def _locked_estimates(
        db: Session, estimate_ids: Sequence[int]
    ) -> dict[int, WinnerProbabilityEstimate]:
        return {
            int(row.id): row
            for row in db.scalars(
                select(WinnerProbabilityEstimate)
                .where(WinnerProbabilityEstimate.id.in_(estimate_ids))
                .with_for_update()
            )
        }

    @staticmethod
    def _validate_generation(generation, previous, manifest) -> None:
        expected = manifest["generation"]
        old_expected = manifest["previous_generation"]
        if (
            generation.status != "READY"
            or generation.published_at is not None
            or generation.generation_key != expected["generation_key"]
            or generation.root_manifest_hash != expected["root_manifest_hash"]
            or previous.status != "PUBLISHED"
            or previous.generation_key != old_expected["generation_key"]
        ):
            raise PublicationInvariantViolation("generation state drifted after review")

    @staticmethod
    def _validate_estimates(
        records,
        originals,
        candidates,
        generation,
        *,
        candidate_manifest_hash: str,
    ) -> None:
        for record in records:
            original = originals[int(record["original_estimate_id"])]
            candidate = candidates[int(record["candidate_estimate_id"])]
            if (
                original.lifecycle_status != EstimateLifecycleStatus.PUBLISHED
                or candidate.lifecycle_status != EstimateLifecycleStatus.CANDIDATE
                or candidate.cohort_generation_id != generation.id
                or candidate.supersedes_estimate_id != original.id
                or candidate.prediction_id != original.prediction_id
                or candidate.outcome_definition_id != original.outcome_definition_id
                or candidate.estimate_kind != original.estimate_kind
                or candidate.reconstruction_category
                != record.get("decision_reconstruction_category")
                or (candidate.metadata_json or {}).get("reviewed_manifest_hash")
                != candidate_manifest_hash
            ):
                raise PublicationInvariantViolation(
                    f"replacement lineage drifted for candidate {candidate.id}"
                )
            if candidate.estimate_kind == EstimateKind.DECISION_TIME:
                validate_clean_insufficient_replacement(candidate)
            elif candidate.estimate_kind != EstimateKind.LATEST_RESCORE:
                raise PublicationInvariantViolation("unsupported publication estimate mode")

    @staticmethod
    def _validate_global_preconditions(db: Session, manifest) -> None:
        active_jobs = int(
            db.scalar(
                text(
                    "SELECT count(*) FROM background_jobs "
                    "WHERE job_type LIKE 'WINNER%' "
                    "AND status IN ('QUEUED','RUNNING','RECOVERING')"
                )
            )
            or 0
        )
        if active_jobs:
            raise PublicationInvariantViolation("active Winner jobs detected")
        reviewed_candidate_ids = {
            int(record["candidate_estimate_id"]) for record in manifest["records"]
        }
        stored_candidate_ids = set(
            int(value)
            for value in db.scalars(
                select(WinnerProbabilityEstimate.id)
                .where(
                    WinnerProbabilityEstimate.cohort_generation_id
                    == int(manifest["generation"]["id"])
                )
                .where(
                    WinnerProbabilityEstimate.lifecycle_status == EstimateLifecycleStatus.CANDIDATE
                )
            )
        )
        if stored_candidate_ids != reviewed_candidate_ids:
            raise PublicationInvariantViolation("unreviewed candidate estimate set detected")
        quarantine = int(
            db.scalar(
                text(
                    """
                    WITH latest AS (
                      SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible
                      FROM winner_temporal_validity_decisions
                      ORDER BY prediction_id,validation_sequence DESC,id DESC
                    )
                    SELECT count(*) FROM latest WHERE NOT evidence_eligible
                    """
                )
            )
            or 0
        )
        if quarantine != int(manifest["quarantined_prediction_count"]):
            raise PublicationInvariantViolation("temporal quarantine drifted")
        impurity = db.execute(
            text(
                """
                WITH latest AS (
                  SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible
                  FROM winner_temporal_validity_decisions
                  ORDER BY prediction_id,validation_sequence DESC,id DESC
                )
                SELECT
                  count(*) FILTER (
                    WHERE latest.prediction_id IS NULL OR NOT latest.evidence_eligible
                  ) AS temporal_bad,
                  count(*) FILTER (WHERE upper(p.ticker)='CLBK') AS clbk
                FROM winner_estimate_evidence_members m
                JOIN winner_probability_estimates e ON e.id=m.estimate_id
                JOIN winner_prediction_snapshots p ON p.id=m.prediction_id
                LEFT JOIN latest ON latest.prediction_id=m.prediction_id
                WHERE e.id = ANY(:candidate_ids)
                """
            ),
            {"candidate_ids": [int(r["candidate_estimate_id"]) for r in manifest["records"]]},
        ).one()
        if int(impurity[0] or 0) or int(impurity[1] or 0):
            raise PublicationInvariantViolation("candidate evidence purity changed")


def _call_hook(hook: Callable[[str], None] | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _without_ids(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {"count": int(snapshot["count"]), "sha256": str(snapshot["sha256"])}
