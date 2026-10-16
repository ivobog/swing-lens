"""Non-authoritative evaluation of proposed eligibility policy on frozen inputs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.services.consumer_eligibility_ledger import (
    EligibilityShadowEvaluation,
    persist_shadow_evaluation,
)
from app.services.eligibility_policy import (
    ConsumerEligibilityOutcome,
    OrthogonalConsumerDecision,
)


@dataclass(frozen=True)
class FrozenEligibilityInput:
    pipeline_run_id: int | None
    upload_run_id: int | None
    ticker: str | None
    consumer_edge: str
    policy_version: str
    source_identity_fingerprint: str
    decision_cutoff: datetime
    legacy_outcome: ConsumerEligibilityOutcome
    payload: dict[str, Any]


ProposedPolicy = Callable[[FrozenEligibilityInput], OrthogonalConsumerDecision]


def evaluate_frozen_input(
    frozen: FrozenEligibilityInput,
    *,
    proposed_policy: ProposedPolicy,
) -> EligibilityShadowEvaluation:
    """Evaluate without mutating the frozen payload or granting authority."""

    proposed = proposed_policy(frozen)
    if proposed.authoritative:
        raise ValueError("shadow evaluator rejects authoritative proposed decisions")
    return EligibilityShadowEvaluation(
        pipeline_run_id=frozen.pipeline_run_id,
        upload_run_id=frozen.upload_run_id,
        ticker=frozen.ticker,
        consumer_edge=frozen.consumer_edge,
        policy_version=frozen.policy_version,
        source_identity_fingerprint=frozen.source_identity_fingerprint,
        decision_cutoff=frozen.decision_cutoff,
        legacy_outcome=frozen.legacy_outcome,
        proposed_decision=proposed,
        frozen_input=frozen.payload,
    )


def evaluate_and_record(
    db: Session,
    frozen_inputs: Iterable[FrozenEligibilityInput],
    *,
    proposed_policy: ProposedPolicy,
) -> tuple[EligibilityShadowEvaluation, ...]:
    evaluations = tuple(
        evaluate_frozen_input(item, proposed_policy=proposed_policy)
        for item in frozen_inputs
    )
    for evaluation in evaluations:
        persist_shadow_evaluation(db, evaluation)
    return evaluations
