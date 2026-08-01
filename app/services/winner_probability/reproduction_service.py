from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    WinnerEstimateEvidenceMember,
    WinnerForwardOutcome,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.cohort_statistics import CohortStatisticsService
from app.services.winner_probability.config import (
    WinnerProbabilityConfig,
    load_winner_probability_config,
)
from app.services.winner_probability.evidence_manifest_service import (
    _hash_payload,
    _manifest_payload,
)
from app.services.winner_probability.evidence_service import EvidenceOutcome


@dataclass(frozen=True)
class EstimateReproductionResult:
    estimate_id: int
    matches: bool
    mismatches: tuple[str, ...]
    evidence_manifest_hash: str
    point_probability: Decimal | None
    sample_n: int


class ReproductionService:
    def reproduce_estimate(
        self,
        db: Session,
        *,
        estimate_id: int,
        config: WinnerProbabilityConfig | None = None,
    ) -> EstimateReproductionResult:
        config = config or load_winner_probability_config()
        estimate = db.get(WinnerProbabilityEstimate, estimate_id)
        if estimate is None:
            raise ValueError(f"Estimate {estimate_id} was not found.")
        evidence = self._load_exact_evidence(db, estimate)
        statistics = CohortStatisticsService().calculate(evidence, config)
        manifest_hash = _hash_payload(_manifest_payload(evidence))
        mismatches = _mismatches(
            estimate=estimate,
            point_probability=statistics.posterior_probability if evidence else None,
            sample_n=statistics.sample_n,
            manifest_hash=manifest_hash,
        )
        return EstimateReproductionResult(
            estimate_id=estimate.id,
            matches=not mismatches,
            mismatches=tuple(mismatches),
            evidence_manifest_hash=manifest_hash,
            point_probability=statistics.posterior_probability if evidence else None,
            sample_n=statistics.sample_n,
        )

    def _load_exact_evidence(
        self,
        db: Session,
        estimate: WinnerProbabilityEstimate,
    ) -> tuple[EvidenceOutcome, ...]:
        members = list(
            db.scalars(
                select(WinnerEstimateEvidenceMember)
                .where(WinnerEstimateEvidenceMember.estimate_id == estimate.id)
                .order_by(WinnerEstimateEvidenceMember.id)
            )
        )
        evidence: list[EvidenceOutcome] = []
        for member in members:
            outcome = db.get(WinnerForwardOutcome, member.outcome_id)
            if outcome is None or outcome.revision != member.outcome_revision:
                raise ValueError("Evidence outcome revision could not be reproduced.")
            target_stop_id = (member.metadata_json or {}).get("target_stop_outcome_id")
            target_stop = db.get(WinnerTargetStopOutcome, target_stop_id)
            if target_stop is None:
                raise ValueError("Evidence target/stop outcome could not be reproduced.")
            evidence.append(
                EvidenceOutcome(
                    prediction=outcome.prediction,
                    forward_outcome=outcome,
                    target_stop_outcome=target_stop,
                    inclusion_weight=Decimal(str(member.inclusion_weight)),
                )
            )
        return tuple(evidence)


def _mismatches(
    *,
    estimate: WinnerProbabilityEstimate,
    point_probability: Decimal | None,
    sample_n: int,
    manifest_hash: str,
) -> list[str]:
    mismatches: list[str] = []
    if estimate.evidence_manifest_hash != manifest_hash:
        mismatches.append("evidence_manifest_hash")
    if estimate.sample_n != sample_n:
        mismatches.append("sample_n")
    if _decimal_or_none(estimate.point_probability) != _decimal_or_none(point_probability):
        mismatches.append("point_probability")
    return mismatches


def _decimal_or_none(value) -> Decimal | None:
    return Decimal(str(value)).quantize(Decimal("0.000001")) if value is not None else None
