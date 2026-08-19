from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    WinnerCohortGeneration,
    WinnerCohortStatistic,
    WinnerOutcomeDefinition,
)
from app.services.winner_probability.cohort_definition import (
    CohortDefinitionService,
    CohortKey,
)
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    CohortGenerationStatus,
    GenerationInvariantViolation,
    validate_generation_transition,
)
from app.services.winner_probability.cohort_statistics import CohortStatisticsService
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.evidence_manifest_service import EvidenceManifestService
from app.services.winner_probability.evidence_service import EvidenceOutcome, EvidenceService


class CohortMaterializationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class CohortMaterializationResult:
    generation_id: int
    generation_key: str
    status: str
    evidence_rows_loaded: int
    planned_groups: int
    completed_groups: int
    groups_in_slice: int
    manifest_members_inserted: int
    continuation_required: bool
    desired_watermark_advanced: bool = False
    no_op: bool = False

    def as_dict(self) -> dict[str, int | str | bool]:
        return self.__dict__.copy()


class CohortMaterializationService:
    def __init__(
        self,
        *,
        evidence_service: EvidenceService | None = None,
        definition_service: CohortDefinitionService | None = None,
        statistics_service: CohortStatisticsService | None = None,
        manifest_service: EvidenceManifestService | None = None,
        generation_service: CohortGenerationService | None = None,
    ) -> None:
        self.evidence_service = evidence_service or EvidenceService()
        self.definition_service = definition_service or CohortDefinitionService()
        self.statistics_service = statistics_service or CohortStatisticsService()
        self.manifest_service = manifest_service or EvidenceManifestService()
        self.generation_service = generation_service or CohortGenerationService()

    def materialize_slice(
        self,
        db: Session,
        *,
        generation: WinnerCohortGeneration,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
        lease_guard: Callable[[], None],
        should_cancel: Callable[[], bool],
        max_groups: int = 100,
        max_wall_seconds: float = 45.0,
    ) -> CohortMaterializationResult:
        if generation.status == CohortGenerationStatus.PUBLISHED:
            return self._result(generation, no_op=True)
        if generation.status != CohortGenerationStatus.BUILDING:
            raise GenerationInvariantViolation(
                f"generation {generation.id} is not buildable: {generation.status}"
            )
        started = monotonic()
        lease_guard()
        if should_cancel():
            self._cancel(db, generation, lease_guard)

        universe = self.evidence_service.load_generation_evidence(
            db,
            outcome_definition=outcome_definition,
            training_cutoff_at=generation.training_cutoff_at,
            config=config,
            watermark=dict(generation.watermark_json or {}),
        )
        groups = self._groups(universe.evidence, config)
        ordered = self._ordered_groups(groups, config)
        generation.evidence_row_count = len(universe.evidence)
        generation.planned_group_count = len(ordered)
        generation.metrics_json = {
            **(generation.metrics_json or {}),
            "evidence_funnel": universe.counts(),
            "evidence_rows_loaded": len(universe.evidence),
            "unique_cohort_keys_planned": len(ordered),
        }
        remaining = self._remaining_from_checkpoint(generation, ordered)
        groups_in_slice = 0
        manifest_members_inserted = 0

        for cohort_key, evidence in remaining:
            if groups_in_slice >= max(1, int(max_groups)):
                break
            if groups_in_slice and monotonic() - started >= max_wall_seconds:
                break
            lease_guard()
            if should_cancel():
                self._cancel(db, generation, lease_guard)
            definition = self.definition_service.ensure_definition(
                db,
                cohort_key=cohort_key,
                outcome_definition=outcome_definition,
                config=config,
            )
            statistics = self.statistics_service.calculate(evidence, config)
            manifest = self.manifest_service.create_or_get_manifest(
                db,
                evidence=evidence,
                hash_algorithm=config.evidence_membership.manifest_hash_algorithm,
            )
            manifest_members_inserted += self.manifest_service.persist_manifest_members(
                db, manifest=manifest.manifest, evidence=evidence
            )
            existing = db.scalar(
                select(WinnerCohortStatistic)
                .where(WinnerCohortStatistic.generation_id == generation.id)
                .where(WinnerCohortStatistic.cohort_definition_id == definition.id)
            )
            if existing is None:
                db.add(
                    WinnerCohortStatistic(
                        generation_id=generation.id,
                        cohort_definition_id=definition.id,
                        outcome_definition_id=outcome_definition.id,
                        evidence_manifest_id=manifest.manifest.id,
                        statistic_as_of=datetime.now(UTC),
                        training_cutoff_at=generation.training_cutoff_at,
                        sample_n=statistics.sample_n,
                        effective_n=statistics.effective_n,
                        wins=statistics.wins,
                        raw_rate=statistics.raw_rate,
                        posterior_probability=statistics.posterior_probability,
                        lower_bound=statistics.lower_bound,
                        upper_bound=statistics.upper_bound,
                        median_return_pct=statistics.median_return_pct,
                        median_mfe_pct=statistics.median_mfe_pct,
                        median_mae_pct=statistics.median_mae_pct,
                        evidence_grade=statistics.evidence_grade,
                        config_hash=config.config_hash,
                        evidence_manifest_hash=manifest.manifest_hash,
                        metadata_json={
                            "mean_return_pct": _str_or_none(
                                statistics.mean_return_pct
                            ),
                            "target_first_rate": _str_or_none(
                                statistics.target_first_rate
                            ),
                            "interval_width": str(statistics.interval_width),
                            "materialization_order": "L5_TO_L0",
                            "cohort_level": cohort_key.level,
                            "cohort_key": cohort_key.key,
                        },
                    )
                )
            elif existing.evidence_manifest_hash != manifest.manifest_hash:
                raise GenerationInvariantViolation(
                    "generation cohort statistic has corrupted evidence identity"
                )
            db.flush()
            generation.completed_group_count = int(
                db.scalar(
                    select(func.count(WinnerCohortStatistic.id)).where(
                        WinnerCohortStatistic.generation_id == generation.id
                    )
                )
                or 0
            )
            generation.checkpoint_json = {
                "phase": "MATERIALIZE_GROUPS",
                "last_cohort_level": cohort_key.level,
                "last_cohort_key": cohort_key.key,
                "completed_groups": generation.completed_group_count,
                "planned_groups": len(ordered),
            }
            groups_in_slice += 1

        # The heartbeat executes an execution-token compare-and-swap and commits
        # this batch and its checkpoint together. A stale owner is rolled back.
        lease_guard()
        complete = generation.completed_group_count == len(ordered)
        if not complete:
            generation.metrics_json = {
                **(generation.metrics_json or {}),
                "manifest_members_inserted": manifest_members_inserted,
                "slice_seconds": monotonic() - started,
            }
            db.flush()
            lease_guard()
            return self._result(
                generation,
                groups_in_slice=groups_in_slice,
                manifest_members_inserted=manifest_members_inserted,
                continuation_required=True,
            )

        self._validate_complete(db, generation, ordered)
        validate_generation_transition(
            generation.status, CohortGenerationStatus.READY
        )
        generation.status = CohortGenerationStatus.READY
        generation.ready_at = datetime.now(UTC)
        generation.checkpoint_json = {
            **(generation.checkpoint_json or {}),
            "phase": "READY",
            "validated_count": generation.completed_group_count,
        }
        db.flush()
        lease_guard()
        if should_cancel():
            self._cancel(db, generation, lease_guard)
        desired_advanced = self.generation_service.publish(
            db,
            generation=generation,
            lease_guard=lease_guard,
        )
        return self._result(
            generation,
            groups_in_slice=groups_in_slice,
            manifest_members_inserted=manifest_members_inserted,
            continuation_required=desired_advanced,
            desired_watermark_advanced=desired_advanced,
        )

    def _groups(
        self,
        evidence: tuple[EvidenceOutcome, ...],
        config: WinnerProbabilityConfig,
    ) -> dict[str, tuple[CohortKey, tuple[EvidenceOutcome, ...]]]:
        mutable: dict[str, tuple[CohortKey, list[EvidenceOutcome]]] = {}
        for row in evidence:
            for key in self.definition_service.cohort_keys_for_features(
                row.prediction.feature_json or {}, config
            ):
                if key.key not in mutable:
                    mutable[key.key] = (key, [])
                mutable[key.key][1].append(row)
        # L5 must exist even for an empty universe so the generation can
        # truthfully materialize an insufficient global baseline.
        for key in self.definition_service.cohort_keys_for_features({}, config):
            if key.level == config.cohort.hierarchy[-1].level:
                mutable.setdefault(key.key, (key, []))
        return {
            key: (cohort_key, tuple(rows))
            for key, (cohort_key, rows) in mutable.items()
        }

    @staticmethod
    def _ordered_groups(groups, config):
        rank = {
            level.level: index for index, level in enumerate(config.cohort.hierarchy)
        }
        return sorted(
            groups.values(),
            key=lambda item: (-rank[item[0].level], item[0].key),
        )

    @staticmethod
    def _remaining_from_checkpoint(generation, ordered):
        checkpoint = generation.checkpoint_json or {}
        last_level = checkpoint.get("last_cohort_level")
        last_key = checkpoint.get("last_cohort_key")
        if not last_level or not last_key:
            return ordered
        for index, (cohort_key, _evidence) in enumerate(ordered):
            if cohort_key.level == last_level and cohort_key.key == last_key:
                return ordered[index + 1 :]
        raise GenerationInvariantViolation("generation checkpoint cohort key is invalid")

    @staticmethod
    def _validate_complete(db, generation, ordered) -> None:
        count = int(
            db.scalar(
                select(func.count(WinnerCohortStatistic.id)).where(
                    WinnerCohortStatistic.generation_id == generation.id
                )
            )
            or 0
        )
        if count != len(ordered):
            raise GenerationInvariantViolation("generation statistic count is incomplete")
        if not ordered or ordered[0][0].level != "L5":
            raise GenerationInvariantViolation("generation did not materialize L5 first")

    @staticmethod
    def _cancel(db, generation, lease_guard) -> None:
        if generation.status not in {
            CohortGenerationStatus.BUILDING,
            CohortGenerationStatus.READY,
        }:
            raise GenerationInvariantViolation("only active generations can cancel")
        validate_generation_transition(
            generation.status, CohortGenerationStatus.CANCELLED
        )
        generation.status = CohortGenerationStatus.CANCELLED
        generation.cancelled_at = datetime.now(UTC)
        generation.completed_at = generation.cancelled_at
        db.flush()
        lease_guard()
        raise CohortMaterializationCancelled("winner cohort generation was cancelled")

    @staticmethod
    def _result(
        generation,
        *,
        groups_in_slice=0,
        manifest_members_inserted=0,
        continuation_required=False,
        desired_watermark_advanced=False,
        no_op=False,
    ) -> CohortMaterializationResult:
        return CohortMaterializationResult(
            generation_id=generation.id,
            generation_key=generation.generation_key,
            status=generation.status,
            evidence_rows_loaded=int(generation.evidence_row_count or 0),
            planned_groups=int(generation.planned_group_count or 0),
            completed_groups=int(generation.completed_group_count or 0),
            groups_in_slice=groups_in_slice,
            manifest_members_inserted=manifest_members_inserted,
            continuation_required=continuation_required,
            desired_watermark_advanced=desired_watermark_advanced,
            no_op=no_op,
        )


def _str_or_none(value) -> str | None:
    return str(value) if value is not None else None
