from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.dtos import FamilyEvidence, NormalizedSnapshot
from app.services.setup_lifecycle.enums import ConfidenceLabel


@dataclass(frozen=True)
class ConfidenceBreakdown:
    score: int
    label: ConfidenceLabel
    components: dict[str, float]
    reason_codes: tuple[str, ...]
    component_details: dict[str, dict[str, float | bool | str | None]]


class SetupLifecycleConfidenceService:
    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        self.config = config or load_setup_lifecycle_config()

    def score(
        self,
        snapshot: NormalizedSnapshot,
        evidence: FamilyEvidence,
        *,
        persistence_sessions: int = 0,
        context_complete: bool | None = None,
    ) -> ConfidenceBreakdown:
        coverage = _coverage(snapshot)
        agreement, agreement_detail = _agreement(
            evidence,
            self.config.confidence.agreement_weights,
        )
        persistence = min(1.0, max(0.0, persistence_sessions / 3))
        freshness, freshness_detail = _freshness_and_lineage(
            snapshot,
            self.config.confidence.freshness_and_lineage_weights,
        )
        context = _context(snapshot) if context_complete is None else float(context_complete)

        components = {
            "required_feature_coverage": coverage,
            "signal_agreement": agreement,
            "persistence": persistence,
            "freshness_and_lineage": freshness,
            "context_completeness": context,
        }
        score = weighted_confidence_score(
            components,
            self.config.confidence.weights,
        )

        reason_codes: list[str] = []
        if coverage < 0.5:
            reason_codes.append("LOW_REQUIRED_FEATURE_COVERAGE")
        if freshness < 1.0:
            if freshness_detail["freshness"] < 1.0:
                reason_codes.append("STALE_SOURCE_EVIDENCE")
            if freshness_detail["source_run_success"] < 1.0:
                reason_codes.append("SOURCE_RUN_NOT_SUCCESSFUL")
            if freshness_detail["lineage_integrity"] < 1.0:
                reason_codes.append("MISSING_OR_INCONSISTENT_LINEAGE")
        if agreement < 1.0:
            reason_codes.append("SIGNAL_DISAGREEMENT")
        if context < 1.0:
            reason_codes.append("MISSING_CONTEXT")
        if not evidence.trackable:
            reason_codes.append("NOT_TRACKABLE")

        return ConfidenceBreakdown(
            score=score,
            label=self.label_for_score(score),
            components=components,
            reason_codes=tuple(reason_codes),
            component_details={
                "signal_agreement": agreement_detail,
                "freshness_and_lineage": freshness_detail,
            },
        )

    def label_for_score(self, score: int) -> ConfidenceLabel:
        if score >= self.config.confidence.high_min:
            return ConfidenceLabel.HIGH
        if score >= self.config.confidence.normal_min:
            return ConfidenceLabel.NORMAL
        if score >= self.config.confidence.low_min:
            return ConfidenceLabel.LOW
        return ConfidenceLabel.INSUFFICIENT


def weighted_confidence_score(
    components: dict[str, float],
    weights: dict[str, float],
) -> int:
    """Return the SDD 10.2 weighted explainability/data-quality score."""
    weighted = sum(components[key] * weights.get(key, 0.0) for key in components)
    return max(0, min(100, round(weighted * 100)))


def _coverage(snapshot: NormalizedSnapshot) -> float:
    if snapshot.required_feature_coverage is not None:
        return max(0.0, min(1.0, float(snapshot.required_feature_coverage)))
    if not snapshot.signals:
        return 0.0
    present = sum(signal.raw_value is not None for signal in snapshot.signals.values())
    return present / len(snapshot.signals)


def _agreement(
    evidence: FamilyEvidence,
    weights: dict[str, float],
) -> tuple[float, dict[str, float]]:
    expected = ("trend", "contraction", "relative_strength", "classification")
    components = {
        key: max(0.0, min(1.0, float(evidence.agreement_components.get(key, 0.0))))
        for key in expected
    }
    if evidence.hard_failure:
        return 0.0, components
    score = sum(components[key] * weights[key] for key in expected)
    return max(0.0, min(1.0, score)), components


def _freshness_and_lineage(
    snapshot: NormalizedSnapshot,
    weights: dict[str, float],
) -> tuple[float, dict[str, float | bool | str | None]]:
    freshness_status = str(snapshot.freshness_status or "").upper()
    freshness = {"FRESH": 1.0, "NEAR_STALE": 0.5, "STALE": 0.0}.get(
        freshness_status,
        0.0,
    )
    lineage: dict[str, Any] = snapshot.source_lineage or {}
    run_status = str(lineage.get("source_run_status") or "").upper() or None
    run_success = float(
        lineage.get("source_run_successful") is True or run_status == "COMPLETED"
    )
    hashes_present = all(
        bool(value)
        for value in (
            snapshot.engine_version,
            snapshot.config_version,
            snapshot.schema_version,
            snapshot.config_hash,
            snapshot.source_data_hash,
        )
    )
    lineage_ids = lineage.get("source_ids")
    ids_consistent = isinstance(lineage_ids, dict) and all(
        lineage_ids.get(key) == value
        for key, value in snapshot.source_ids.items()
        if key not in {"snapshot_id", "run_id"} and value is not None
    )
    lineage_integrity = float(
        lineage.get("lineage_integrity") is True and hashes_present and ids_consistent
    )
    detail: dict[str, float | bool | str | None] = {
        "freshness": freshness,
        "source_run_success": run_success,
        "lineage_integrity": lineage_integrity,
        "freshness_status": freshness_status or None,
        "source_run_status": run_status,
        "hashes_present": hashes_present,
        "source_ids_consistent": ids_consistent,
    }
    score = (
        freshness * weights["freshness"]
        + run_success * weights["source_run_success"]
        + lineage_integrity * weights["lineage_integrity"]
    )
    return max(0.0, min(1.0, score)), detail


def _context(snapshot: NormalizedSnapshot) -> float:
    lineage: dict[str, Any] = snapshot.source_lineage or {}
    has_market = lineage.get("market_regime_as_of") is not None
    has_sector = lineage.get("sector_rotation_as_of") is not None
    return (float(has_market) + float(has_sector)) / 2
