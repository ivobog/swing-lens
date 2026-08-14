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
    WinnerTrainingEligibilityDecision,
    WinnerTrainingOutcomeReplay,
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
from app.services.winner_probability.pre11_compatibility_service import (
    EVIDENCE_ORIGIN_PRE11,
)


@dataclass(frozen=True)
class EstimateReproductionResult:
    estimate_id: int
    matches: bool
    mismatches: tuple[str, ...]
    evidence_manifest_hash: str
    point_probability: Decimal | None
    sample_n: int
    effective_n: Decimal
    wins: Decimal
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    interval_width: Decimal | None


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
            effective_n=statistics.effective_n,
            wins=statistics.wins,
            lower_bound=statistics.lower_bound if evidence else None,
            upper_bound=statistics.upper_bound if evidence else None,
            interval_width=statistics.interval_width if evidence else None,
            config_hash=config.config_hash,
        )
        return EstimateReproductionResult(
            estimate_id=estimate.id,
            matches=not mismatches,
            mismatches=tuple(mismatches),
            evidence_manifest_hash=manifest_hash,
            point_probability=statistics.posterior_probability if evidence else None,
            sample_n=statistics.sample_n,
            effective_n=statistics.effective_n,
            wins=statistics.wins,
            lower_bound=statistics.lower_bound if evidence else None,
            upper_bound=statistics.upper_bound if evidence else None,
            interval_width=statistics.interval_width if evidence else None,
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
            evidence_origin = member.evidence_origin or "NATIVE_1_1"
            outcome = db.get(WinnerForwardOutcome, member.outcome_id)
            if outcome is None or outcome.revision != member.outcome_revision:
                raise ValueError("Evidence outcome revision could not be reproduced.")
            target_stop_id = (member.metadata_json or {}).get("target_stop_outcome_id")
            if evidence_origin == EVIDENCE_ORIGIN_PRE11:
                target_stop = db.get(WinnerTrainingOutcomeReplay, member.outcome_replay_id)
                decision = db.get(WinnerTrainingEligibilityDecision, member.eligibility_decision_id)
                if decision is None or not decision.training_allowed:
                    raise ValueError("Evidence eligibility decision could not be reproduced.")
            else:
                target_stop = db.get(WinnerTargetStopOutcome, target_stop_id)
            if target_stop is None:
                raise ValueError("Evidence target/stop outcome could not be reproduced.")
            expected_target_revision = (member.metadata_json or {}).get("target_stop_revision")
            if target_stop.revision != expected_target_revision:
                raise ValueError("Evidence target/stop revision could not be reproduced.")
            evidence.append(
                EvidenceOutcome(
                    prediction=outcome.prediction,
                    forward_outcome=outcome,
                    target_stop_outcome=target_stop,
                    inclusion_weight=Decimal(str(member.inclusion_weight)),
                    eligibility_decision_id=member.eligibility_decision_id,
                    outcome_replay_id=member.outcome_replay_id,
                    evidence_origin=evidence_origin,
                )
            )
        return tuple(evidence)


def _mismatches(
    *,
    estimate: WinnerProbabilityEstimate,
    point_probability: Decimal | None,
    sample_n: int,
    manifest_hash: str,
    effective_n: Decimal,
    wins: Decimal,
    lower_bound: Decimal | None,
    upper_bound: Decimal | None,
    interval_width: Decimal | None,
    config_hash: str,
) -> list[str]:
    mismatches: list[str] = []
    if estimate.evidence_manifest_hash != manifest_hash:
        mismatches.append("evidence_manifest_hash")
    if estimate.sample_n != sample_n:
        mismatches.append("sample_n")
    if _decimal_or_none(estimate.effective_n) != _decimal_or_none(effective_n):
        mismatches.append("effective_n")
    stored_wins = (estimate.metadata_json or {}).get("wins")
    if _decimal_or_none(stored_wins) != _decimal_or_none(wins):
        mismatches.append("wins")
    if _decimal_or_none(estimate.point_probability) != _decimal_or_none(point_probability):
        mismatches.append("point_probability")
    if _decimal_or_none(estimate.lower_bound) != _decimal_or_none(lower_bound):
        mismatches.append("lower_bound")
    if _decimal_or_none(estimate.upper_bound) != _decimal_or_none(upper_bound):
        mismatches.append("upper_bound")
    if _decimal_or_none(estimate.interval_width) != _decimal_or_none(interval_width):
        mismatches.append("interval_width")
    if estimate.config_hash != config_hash:
        mismatches.append("config_hash")
    return mismatches


def _decimal_or_none(value) -> Decimal | None:
    return Decimal(str(value)).quantize(Decimal("0.000001")) if value is not None else None
