from __future__ import annotations

from dataclasses import dataclass

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.config import CeriConfig, load_ceri_config


@dataclass(frozen=True)
class ProviderConflictResolution:
    selected: CeriEstimateSnapshot
    competing: tuple[CeriEstimateSnapshot, ...]
    conflict_type: str
    resolution_reason: str


class CeriProviderConflictService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()
        self._priority = {
            provider.value: index for index, provider in enumerate(self.config.providers.priority)
        }

    def resolve_estimate(
        self,
        observations: list[CeriEstimateSnapshot],
        source_records: dict[int, CeriSourceRecord],
    ) -> ProviderConflictResolution:
        if not observations:
            raise ValueError("at least one observation is required")
        selected = sorted(
            observations,
            key=lambda observation: (
                self._source_priority(observation, source_records),
                self._quality_penalty(observation),
                self._freshness_sort(observation),
                observation.source_record_id,
            ),
        )[0]
        conflict_type = "NONE" if len(observations) == 1 else _conflict_type(observations)
        reason = (
            "single_observation"
            if len(observations) == 1
            else "provider_priority_then_quality_then_freshness"
        )
        return ProviderConflictResolution(
            selected=selected,
            competing=tuple(row for row in observations if row is not selected),
            conflict_type=conflict_type,
            resolution_reason=reason,
        )

    def _source_priority(
        self,
        observation: CeriEstimateSnapshot,
        source_records: dict[int, CeriSourceRecord],
    ) -> int:
        source = source_records.get(observation.source_record_id)
        return self._priority.get(source.provider if source is not None else "", 10_000)

    def _quality_penalty(self, observation: CeriEstimateSnapshot) -> int:
        return len(observation.quality_flags_json or [])

    def _freshness_sort(self, observation: CeriEstimateSnapshot) -> float:
        if observation.effective_at is None:
            return 0.0
        return -observation.effective_at.timestamp()


def _conflict_type(observations: list[CeriEstimateSnapshot]) -> str:
    values = {row.consensus for row in observations}
    return "VALUE_CONFLICT" if len(values) > 1 else "SOURCE_CONFLICT"
