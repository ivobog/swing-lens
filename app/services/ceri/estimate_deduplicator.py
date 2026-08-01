from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.ceri_tables import CeriEstimateSnapshot


@dataclass(frozen=True)
class EstimateDeduplicationGroup:
    canonical: CeriEstimateSnapshot
    observations: tuple[CeriEstimateSnapshot, ...]
    duplicate_snapshot_ids: tuple[int | None, ...]
    reason: str


class CeriEstimateDeduplicator:
    def __init__(self, consensus_tolerance: Decimal = Decimal("0.0001")) -> None:
        self.consensus_tolerance = consensus_tolerance

    def group(self, observations: list[CeriEstimateSnapshot]) -> list[EstimateDeduplicationGroup]:
        groups: list[list[CeriEstimateSnapshot]] = []
        for observation in observations:
            for group in groups:
                if self._same_observation(group[0], observation):
                    group.append(observation)
                    break
            else:
                groups.append([observation])
        return [
            EstimateDeduplicationGroup(
                canonical=_canonical(group),
                observations=tuple(group),
                duplicate_snapshot_ids=tuple(row.id for row in group[1:]),
                reason="canonical_key_effective_session_and_tolerance",
            )
            for group in groups
        ]

    def _same_observation(
        self,
        left: CeriEstimateSnapshot,
        right: CeriEstimateSnapshot,
    ) -> bool:
        if left.canonical_observation_key != right.canonical_observation_key:
            return False
        if left.effective_session != right.effective_session:
            return False
        if left.consensus is None or right.consensus is None:
            return left.consensus is right.consensus
        return abs(left.consensus - right.consensus) <= self.consensus_tolerance


def _canonical(group: list[CeriEstimateSnapshot]) -> CeriEstimateSnapshot:
    return sorted(
        group,
        key=lambda row: (
            row.effective_at is None,
            row.effective_at,
            row.source_record_id,
        ),
    )[0]
