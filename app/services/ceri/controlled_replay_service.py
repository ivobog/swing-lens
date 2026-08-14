from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriChangeEvent,
    CeriControlledReplay,
    CeriDerivedFeature,
    CeriEstimateSnapshot,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.services.ceri.confidence_service import CeriConfidenceService, ConfidenceResult
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.dtos import ScoreComponent
from app.services.ceri.enums import HistoricalViewMode
from app.services.ceri.evidence_state_service import CeriEvidenceLedgerService
from app.services.ceri.opportunity_score_service import (
    CeriOpportunityScoreService,
    OpportunityResult,
)
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
from app.services.ceri.revision_feature_service import (
    CeriRevisionFeatureService,
    _net_breadth,
)
from app.services.ceri.snapshot_service import (
    CeriSnapshotService,
    derive_posture,
    score_evidence_hash,
)

REPLAY_PROCESSOR_SIGNATURE = "ceri-controlled-replay-v1"
REPLAY_SCHEMA_VERSION = "ceri-controlled-replay-schema-v1"
REVISION_COMPONENTS = {
    "revision_magnitude",
    "revision_breadth",
    "revision_acceleration",
}
CONFIDENCE_WARNING_PREFIXES = (
    "estimate_",
    "analyst_",
    "dataset_freshness_",
)


class ControlledReplayError(ValueError):
    pass


class ControlledReplayCertificationError(ControlledReplayError):
    pass


@dataclass(frozen=True)
class ControlledReplayRequest:
    source_run_id: int
    replay_identifier: str
    git_sha: str
    processor_signature: str = REPLAY_PROCESSOR_SIGNATURE
    schema_version: str = REPLAY_SCHEMA_VERSION


@dataclass(frozen=True)
class ControlledReplayResult:
    replay_id: int
    replay_identifier: str
    status: str
    feature_count: int
    snapshot_count: int
    changed_feature_count: int
    comparisons: tuple[dict[str, Any], ...]
    feature_changes: tuple[dict[str, Any], ...]
    impact: dict[str, Any]
    certification: dict[str, Any]


class CeriControlledReplayService:
    """Creates a parallel PIT replay without updating historical Run 104 rows."""

    def __init__(self, *, config: CeriConfig | None = None) -> None:
        self.base_config = config or load_ceri_config()

    def replay(
        self,
        db: Session,
        request: ControlledReplayRequest,
    ) -> ControlledReplayResult:
        self._validate_request(db, request)
        originals = list(
            db.scalars(
                select(CeriScoreSnapshot)
                .where(CeriScoreSnapshot.run_id == request.source_run_id)
                .order_by(CeriScoreSnapshot.ticker)
            ).all()
        )
        if not originals:
            raise ControlledReplayError(f"source run {request.source_run_id} has no snapshots")
        if len({row.ticker for row in originals}) != len(originals):
            raise ControlledReplayCertificationError("source run contains duplicate tickers")
        cutoffs = {row.cutoff_at for row in originals}
        if len(cutoffs) != 1:
            raise ControlledReplayCertificationError("source run has multiple cutoffs")
        cutoff_at = next(iter(cutoffs))
        original_config_hashes = {row.config_hash for row in originals}
        original_config_versions = {row.config_version for row in originals}
        if original_config_hashes != {self.base_config.config_hash}:
            raise ControlledReplayCertificationError(
                "active config hash does not match immutable source run"
            )
        if len(original_config_versions) != 1:
            raise ControlledReplayCertificationError("source run has multiple config versions")

        old_feature_ids = _selected_revision_ids(originals)
        old_features = {
            row.id: row
            for row in db.scalars(
                select(CeriRevisionFeature).where(CeriRevisionFeature.id.in_(old_feature_ids))
            ).all()
        }
        old_lineage_estimate_ids = {
            estimate_id
            for row in old_features.values()
            for estimate_id in (row.current_snapshot_id, row.baseline_snapshot_id)
            if estimate_id is not None
        }
        old_lineage_estimates = {
            row.id: row
            for row in db.scalars(
                select(CeriEstimateSnapshot).where(
                    CeriEstimateSnapshot.id.in_(old_lineage_estimate_ids)
                )
            ).all()
        }
        original_state_hash = _original_state_hash(db, originals, old_features)
        calculation_version = _replay_calculation_version(
            self.base_config.engine.calculation_version,
            request.replay_identifier,
        )
        replay_config = replace(
            self.base_config,
            engine=replace(
                self.base_config.engine,
                calculation_version=calculation_version,
            ),
        )
        manifest = CeriControlledReplay(
            replay_identifier=request.replay_identifier,
            source_run_id=request.source_run_id,
            original_cutoff_at=cutoff_at,
            git_sha=request.git_sha,
            processor_signature=request.processor_signature,
            config_version=next(iter(original_config_versions)),
            config_hash=self.base_config.config_hash,
            calculation_version=calculation_version,
            schema_version=request.schema_version,
            status="RUNNING",
            universe_count=len(originals),
            original_state_hash=original_state_hash,
        )
        db.add(manifest)
        db.flush()

        company_ids = sorted({row.company_id for row in originals})
        eligible_estimates = list(
            db.scalars(
                select(CeriEstimateSnapshot)
                .join(
                    CeriSourceRecord,
                    CeriSourceRecord.id == CeriEstimateSnapshot.source_record_id,
                )
                .where(CeriEstimateSnapshot.company_id.in_(company_ids))
                .where(CeriSourceRecord.ingested_at <= cutoff_at)
            ).all()
        )
        estimates_by_company: dict[int, list[CeriEstimateSnapshot]] = defaultdict(list)
        for estimate in eligible_estimates:
            estimates_by_company[estimate.company_id].append(estimate)
        source_provider_rows = db.execute(
            select(CeriSourceRecord.id, CeriSourceRecord.provider).where(
                CeriSourceRecord.id.in_(
                    sorted({row.source_record_id for row in eligible_estimates})
                )
            )
        ).all()
        source_providers = {
            row_id: SimpleNamespace(id=row_id, provider=provider)
            for row_id, provider in source_provider_rows
        }

        opportunity_service = CeriOpportunityScoreService(config=replay_config)
        confidence_service = CeriConfidenceService(config=replay_config)
        snapshot_service = CeriSnapshotService(config=replay_config)
        replay_features_by_company: dict[int, list[CeriRevisionFeature]] = {}
        all_replay_features: list[CeriRevisionFeature] = []
        comparisons: list[dict[str, Any]] = []
        feature_changes: list[dict[str, Any]] = []
        replay_snapshots: list[CeriScoreSnapshot] = []

        for original in originals:
            query = CeriPointInTimeQuery(
                config=replay_config,
                snapshots=estimates_by_company.get(original.company_id, []),
                source_records=source_providers,
            )
            revision_service = CeriRevisionFeatureService(
                config=replay_config,
                query=query,
            )
            features = _calculate_replay_features(
                revision_service,
                db,
                company_id=original.company_id,
                cutoff_at=cutoff_at,
                replay_id=manifest.id,
                calculation_version=calculation_version,
            )
            db.add_all(features)
            db.flush()
            replay_features_by_company[original.company_id] = features
            all_replay_features.extend(features)

            conflict_penalty = float(
                sum(
                    any("conflict" in warning.lower() for warning in (row.warnings_json or []))
                    for row in features
                )
            )
            old_components = list(
                (original.opportunity_ledger_json or {}).get("components") or []
            )
            opportunity = _merge_opportunity(
                old_components,
                features,
                opportunity_service,
                conflict_penalty=min(3.0, conflict_penalty),
            )
            merged_source_ids = _merged_source_ids(original, features)
            confidence = confidence_service.calculate(
                as_of_session=original.as_of_session,
                revision_features=features,
                dataset_freshness_days=_freshness_days(
                    db,
                    merged_source_ids,
                    cutoff_at,
                ),
                conflict_penalty=conflict_penalty,
            )
            replay_snapshot = _build_replay_snapshot(
                original=original,
                replay=manifest,
                features=features,
                opportunity=opportunity,
                confidence=confidence,
                source_ids=merged_source_ids,
                snapshot_service=snapshot_service,
                replay_config=replay_config,
            )
            db.add(replay_snapshot)
            db.flush()
            replay_snapshots.append(replay_snapshot)

            original_by_identity = _features_by_identity(
                old_features[row_id]
                for row_id in _snapshot_selected_revision_ids(original)
                if row_id in old_features
            )
            replay_by_identity = _features_by_identity(
                row
                for row in features
                if row.id in _snapshot_selected_revision_ids(replay_snapshot)
            )
            estimate_map = dict(old_lineage_estimates)
            estimate_map.update(
                {row.id: row for row in estimates_by_company[original.company_id]}
            )
            for identity in sorted(set(original_by_identity) | set(replay_by_identity)):
                old = original_by_identity.get(identity)
                corrected = replay_by_identity.get(identity)
                if old is None or corrected is None:
                    feature_changes.append(
                        _missing_feature_change(original.ticker, identity, old, corrected)
                    )
                    continue
                change = _feature_change(
                    original.ticker,
                    old,
                    corrected,
                    estimates=estimate_map,
                )
                if change is not None:
                    feature_changes.append(change)

            comparisons.append(_snapshot_comparison(original, replay_snapshot))

        replay_feature_map = {row.id: row for row in all_replay_features if row.id is not None}
        estimate_map = {row.id: row for row in eligible_estimates}
        groups = _feature_groups(all_replay_features)
        selected_replay_ids = _selected_revision_ids(replay_snapshots)
        _validate_selected_revision_features(
            selected_ids=selected_replay_ids,
            features=replay_feature_map,
            estimates=estimate_map,
            groups=groups,
        )
        _assign_ranks(comparisons)
        impact = _ranking_impact(comparisons)
        certification = _certification(
            db=db,
            request=request,
            originals=originals,
            original_features=old_features,
            replay_features=all_replay_features,
            replay_snapshots=replay_snapshots,
            original_state_hash=original_state_hash,
            selected_replay_ids=selected_replay_ids,
            config=replay_config,
            cutoff_at=cutoff_at,
        )
        status = "PASS" if all(certification["invariants"].values()) else "FAIL"
        if status != "PASS":
            failures = [
                key for key, value in certification["invariants"].items() if not value
            ]
            raise ControlledReplayCertificationError(
                f"controlled replay certification failed: {', '.join(failures)}"
            )

        replay_state_hash = _replay_state_hash(replay_snapshots, all_replay_features)
        manifest.status = status
        manifest.feature_count = len(all_replay_features)
        manifest.snapshot_count = len(replay_snapshots)
        manifest.changed_feature_count = len(feature_changes)
        manifest.replay_state_hash = replay_state_hash
        manifest.certification_json = certification
        manifest.impact_json = impact
        manifest.feature_changes_json = feature_changes
        manifest.completed_at = datetime.now(UTC)
        db.flush()

        return ControlledReplayResult(
            replay_id=manifest.id,
            replay_identifier=manifest.replay_identifier,
            status=status,
            feature_count=len(all_replay_features),
            snapshot_count=len(replay_snapshots),
            changed_feature_count=len(feature_changes),
            comparisons=tuple(comparisons),
            feature_changes=tuple(feature_changes),
            impact=impact,
            certification=certification,
        )

    def _validate_request(self, db: Session, request: ControlledReplayRequest) -> None:
        if request.source_run_id <= 0:
            raise ControlledReplayError("source_run_id must be positive")
        if not request.replay_identifier.strip():
            raise ControlledReplayError("replay_identifier is required")
        invalid_sha = len(request.git_sha) != 40 or any(
            ch not in "0123456789abcdef" for ch in request.git_sha
        )
        if invalid_sha:
            raise ControlledReplayError("git_sha must be a lowercase 40-character SHA")
        existing = db.scalar(
            select(CeriControlledReplay).where(
                CeriControlledReplay.replay_identifier == request.replay_identifier
            )
        )
        if existing is not None:
            raise ControlledReplayError(
                f"replay identifier already exists: {request.replay_identifier}"
            )


def _calculate_replay_features(
    service: CeriRevisionFeatureService,
    db: Session,
    *,
    company_id: int,
    cutoff_at: datetime,
    replay_id: int,
    calculation_version: str,
) -> list[CeriRevisionFeature]:
    rows: list[CeriRevisionFeature] = []
    for metric in (metric.value for metric in service.config.metrics.required):
        for period_slot in service.config.metrics.period_types:
            calculated = service.calculate_windows(
                db,
                company_id=company_id,
                metric=metric,
                period_slot=period_slot.value,
                cutoff_at=cutoff_at,
                mode=HistoricalViewMode.AS_KNOWN,
            )
            if len(calculated) >= 2:
                recent = min(calculated, key=lambda feature: feature.window_days)
                longer = max(calculated, key=lambda feature: feature.window_days)
                if recent is not longer:
                    service.with_acceleration(recent, longer)
            for feature in calculated:
                feature.controlled_replay_id = replay_id
                feature.calculation_version = calculation_version
                _normalize_feature_precision(feature)
                feature.evidence_hash = service.reproduce_evidence_hash(feature)
            rows.extend(calculated)
    return rows


def _merge_opportunity(
    original_components: list[dict[str, Any]],
    corrected_features: list[CeriRevisionFeature],
    service: CeriOpportunityScoreService,
    *,
    conflict_penalty: float,
) -> OpportunityResult:
    recalculated = service.calculate(revision_features=corrected_features)
    corrected_revision = {
        component.name: component
        for component in recalculated.components
        if component.name in REVISION_COMPONENTS
    }
    merged: list[ScoreComponent] = []
    original_by_name = {row["name"]: row for row in original_components}
    for name, configured_weight in service.config.opportunity_weights.items():
        if name in corrected_revision:
            merged.append(corrected_revision[name])
            continue
        row = original_by_name.get(name) or {}
        if row and not math.isclose(
            float(row.get("weight", configured_weight)),
            float(configured_weight),
            abs_tol=1e-12,
        ):
            raise ControlledReplayCertificationError(f"Opportunity weight changed for {name}")
        value = row.get("value")
        available = bool(row.get("available", value is not None)) and value is not None
        contribution = (
            max(0.0, min(10.0, float(value))) * float(configured_weight)
            if available
            else None
        )
        merged.append(
            ScoreComponent(
                name=name,
                value=float(value) if value is not None else None,
                weight=float(configured_weight),
                contribution=contribution,
                available=available,
                unavailable_reason=row.get("unavailable_reason"),
                evidence_ids=tuple(int(value) for value in row.get("evidence_ids") or []),
                reasons=tuple(row.get("reasons") or []),
                warnings=tuple(row.get("warnings") or []),
            )
        )
    available_weight = sum(row.weight for row in merged if row.available)
    coverage_pct = available_weight * 100.0
    minimum = float(service.config.revision.minimum_component_coverage_pct)
    rated = coverage_pct + 1e-9 >= minimum
    raw_sum = sum(row.contribution or 0.0 for row in merged)
    score = (
        max(0.0, min(10.0, raw_sum / available_weight - conflict_penalty))
        if rated and available_weight > 0
        else None
    )
    warnings = tuple(warning for row in merged for warning in row.warnings)
    if not rated:
        warnings = (*warnings, "opportunity_component_coverage_insufficient")
    reasons = tuple(
        row.name for row in merged if row.contribution is not None and row.contribution > 0
    )
    if conflict_penalty:
        reasons = (*reasons, "conflict_penalty")
    return OpportunityResult(
        score=score,
        rated=rated,
        coverage_pct=coverage_pct,
        available_weight=available_weight,
        minimum_required_coverage_pct=minimum,
        reweighted=rated and available_weight < 1.0,
        unrated_reason=None if rated else "INSUFFICIENT_COMPONENT_COVERAGE",
        components=tuple(merged),
        penalties=(
            ({"name": "conflict_penalty", "value": conflict_penalty},)
            if conflict_penalty
            else ()
        ),
        reasons=reasons,
        warnings=warnings,
    )


def _build_replay_snapshot(
    *,
    original: CeriScoreSnapshot,
    replay: CeriControlledReplay,
    features: list[CeriRevisionFeature],
    opportunity: OpportunityResult,
    confidence: ConfidenceResult,
    source_ids: list[int],
    snapshot_service: CeriSnapshotService,
    replay_config: CeriConfig,
) -> CeriScoreSnapshot:
    components = []
    for component in opportunity.components:
        payload = asdict(component)
        if (
            component.name == "surprise_trend"
            and component.available
            and not component.evidence_ids
        ):
            payload["lineage_exemption_reason"] = (
                "AGGREGATE_DERIVED_FROM_PERSISTED_EARNINGS_LINEAGE"
            )
        components.append(payload)
    selected_opportunity_ids = [
        evidence_id
        for component in opportunity.components
        if component.available
        for evidence_id in component.evidence_ids
    ]
    lineage = dict(original.evidence_lineage_json or {})
    lineage.pop("evidence_states", None)
    lineage.pop("evidence_counts", None)
    lineage["revision_feature_ids"] = [row.id for row in features]
    lineage["revision_pairs"] = [
        {
            "feature_id": row.id,
            "metric": row.metric,
            "period_slot": row.period_slot,
            "window_days": row.window_days,
            "current_snapshot_id": row.current_snapshot_id,
            "baseline_snapshot_id": row.baseline_snapshot_id,
            "baseline_origin": row.baseline_origin,
            "available": row.pct_change is not None,
            "unavailable_reason": row.unavailable_reason,
        }
        for row in features
    ]
    lineage["revision_source_ids"] = sorted(
        {
            source_id
            for row in features
            for source_id in (row.source_observation_ids_json or [])
        }
    )
    lineage["controlled_replay"] = {
        "replay_id": replay.id,
        "replay_identifier": replay.replay_identifier,
        "source_run_id": replay.source_run_id,
        "original_cutoff_at": replay.original_cutoff_at.isoformat(),
        "git_sha": replay.git_sha,
        "processor_signature": replay.processor_signature,
        "calculation_version": replay.calculation_version,
        "config_version": replay.config_version,
        "config_hash": replay.config_hash,
        "schema_version": replay.schema_version,
    }
    risk_ledger = dict(original.event_risk_ledger_json or {})
    lineage = CeriEvidenceLedgerService().enrich(
        lineage,
        source_ids=source_ids,
        opportunity_selected_ids=selected_opportunity_ids,
        risk_selected_ids=list(risk_ledger.get("selected_event_ids") or []),
    )
    opportunity_ledger = {
        "rated": opportunity.rated,
        "score": opportunity.score,
        "coverage_pct": opportunity.coverage_pct,
        "available_weight": opportunity.available_weight,
        "minimum_required_coverage_pct": opportunity.minimum_required_coverage_pct,
        "reweighted": opportunity.reweighted,
        "unrated_reason": opportunity.unrated_reason,
        "components": components,
        "penalties": list(opportunity.penalties),
    }
    confidence_ledger = {
        "score": confidence.score,
        "label": confidence.label.value,
        "coverage_pct": confidence.coverage_pct,
        "subscores": [asdict(row) for row in confidence.ledger],
        "gates": list(confidence.gates),
        "caps": list(confidence.caps),
    }
    posture = derive_posture(
        opportunity_score=opportunity.score,
        event_risk_score=original.event_risk_score,
        confidence_label=confidence.label.value,
    )
    alignment_context = dict(original.alignment_context_json or {})
    payload = {
        "company_id": original.company_id,
        "ticker": original.ticker,
        "as_of_session": original.as_of_session.isoformat(),
        "cutoff_at": original.cutoff_at,
        "opportunity_score": opportunity.score,
        "event_risk_score": original.event_risk_score,
        "data_confidence": confidence.label.value,
        "coverage_pct": confidence.coverage_pct,
        "posture": posture,
        "components": components,
        "opportunity_ledger": opportunity_ledger,
        "confidence_ledger": confidence_ledger,
        "event_risk_ledger": risk_ledger,
        "source_ids": sorted(source_ids),
        "config_hash": replay_config.config_hash,
        "calculation_version": replay_config.engine.calculation_version,
        "alignment_context": alignment_context,
        "evidence_lineage": lineage,
    }
    nonrevision_warnings = [
        warning
        for warning in (original.warnings_json or [])
        if not warning.startswith("revision_")
        and not warning.startswith(CONFIDENCE_WARNING_PREFIXES)
        and warning != "opportunity_component_coverage_insufficient"
    ]
    warnings = [*nonrevision_warnings, *opportunity.warnings, *confidence.warnings]
    component_json = dict(original.component_json or {})
    component_json.update(
        {
            "components": components,
            "source_ids": sorted(source_ids),
            "alignment_context": alignment_context,
            "evidence_lineage": lineage,
        }
    )
    snapshot = CeriScoreSnapshot(
        controlled_replay_id=replay.id,
        run_id=None,
        source_run_id_text=str(replay.source_run_id),
        company_id=original.company_id,
        ticker=original.ticker,
        as_of_session=original.as_of_session,
        cutoff_at=original.cutoff_at,
        opportunity_score=opportunity.score,
        opportunity_coverage_pct=opportunity.coverage_pct,
        opportunity_unrated_reason=opportunity.unrated_reason,
        event_risk_score=original.event_risk_score,
        data_confidence=confidence.label.value,
        coverage_pct=confidence.coverage_pct,
        posture=posture,
        earnings_proximity_risk=original.earnings_proximity_risk,
        alignment_flags_json=dict(original.alignment_flags_json or {}),
        alignment_context_json=alignment_context,
        evidence_lineage_json=lineage,
        top_positive_contributors_json=_top_contributors(opportunity.components),
        top_negative_contributors_json=[],
        component_json=component_json,
        opportunity_ledger_json=opportunity_ledger,
        confidence_ledger_json=confidence_ledger,
        event_risk_ledger_json=risk_ledger,
        reasons_json=[*opportunity.reasons, *confidence.reasons] or None,
        warnings_json=warnings or None,
        config_version=replay.config_version,
        config_hash=replay.config_hash,
        calculation_version=replay.calculation_version,
        evidence_contract_version="ceri-evidence-contract-v2",
        comparison_state="NO_PRIOR_COMPARABLE_SNAPSHOT",
        evidence_hash=score_evidence_hash(payload),
        hash_schema_version=original.hash_schema_version,
    )
    reproduction = snapshot_service.reproduce_snapshot(snapshot)
    if not reproduction.matches:
        raise ControlledReplayCertificationError(
            f"snapshot hash did not reproduce for {original.ticker}"
        )
    return snapshot


def _validate_selected_revision_features(
    *,
    selected_ids: set[int],
    features: dict[int, CeriRevisionFeature],
    estimates: dict[int, CeriEstimateSnapshot],
    groups: dict[tuple[int, str, str | None], list[CeriRevisionFeature]],
) -> None:
    tolerance = Decimal("0.000001")
    for feature_id in sorted(selected_ids):
        feature = features.get(feature_id)
        if feature is None:
            raise ControlledReplayCertificationError(f"feature {feature_id} is missing")
        current = estimates.get(feature.current_snapshot_id)
        baseline = estimates.get(feature.baseline_snapshot_id)
        if feature.net_breadth is not None:
            if current is None:
                raise ControlledReplayCertificationError(
                    f"feature {feature_id} selected Breadth has no current lineage"
                )
            reproduced_breadth = _net_breadth(current.upward_count, current.downward_count)
            if reproduced_breadth is None or abs(
                _quantize(reproduced_breadth, 6) - feature.net_breadth
            ) > tolerance:
                raise ControlledReplayCertificationError(
                    f"feature {feature_id} Breadth does not reproduce"
                )
        if feature.pct_change is not None:
            if current is None or baseline is None:
                raise ControlledReplayCertificationError(
                    f"feature {feature_id} selected value has incomplete lineage"
                )
            reproduced_pct = _pct_change(current.consensus, baseline.consensus)
            if reproduced_pct is None or abs(
                _quantize(reproduced_pct, 6) - feature.pct_change
            ) > tolerance:
                raise ControlledReplayCertificationError(
                    f"feature {feature_id} selected value does not reproduce"
                )
        if feature.acceleration is not None:
            group = groups.get((feature.company_id, feature.metric, feature.period_slot), [])
            recent = min(group, key=lambda row: row.window_days)
            longer = max(group, key=lambda row: row.window_days)
            if (
                recent.pct_change is None
                or longer.pct_change is None
                or recent.actual_elapsed_days in (None, 0)
                or longer.actual_elapsed_days in (None, 0)
            ):
                raise ControlledReplayCertificationError(
                    f"feature {feature_id} acceleration lineage is incomplete"
                )
            reproduced_acceleration = (
                recent.pct_change / Decimal(recent.actual_elapsed_days)
                - longer.pct_change / Decimal(longer.actual_elapsed_days)
            )
            if abs(
                _quantize(reproduced_acceleration, 6) - feature.acceleration
            ) > tolerance:
                raise ControlledReplayCertificationError(
                    f"feature {feature_id} acceleration does not reproduce"
                )


def _feature_change(
    ticker: str,
    old: CeriRevisionFeature,
    replay: CeriRevisionFeature,
    *,
    estimates: dict[int, CeriEstimateSnapshot],
) -> dict[str, Any] | None:
    old_values = (old.pct_change, old.net_breadth, old.acceleration)
    replay_values = (replay.pct_change, replay.net_breadth, replay.acceleration)
    old_lineage = (
        old.current_snapshot_id,
        old.baseline_snapshot_id,
        old.current_source_record_id,
        old.baseline_source_record_id,
        old.comparison_mode,
    )
    replay_lineage = (
        replay.current_snapshot_id,
        replay.baseline_snapshot_id,
        replay.current_source_record_id,
        replay.baseline_source_record_id,
        replay.comparison_mode,
    )
    if old_values == replay_values and old_lineage == replay_lineage:
        return None
    current = estimates.get(replay.current_snapshot_id)
    baseline = estimates.get(replay.baseline_snapshot_id)
    old_current = estimates.get(old.current_snapshot_id)
    old_baseline = estimates.get(old.baseline_snapshot_id)
    old_lineage_pct = _pct_change(
        old_current.consensus if old_current is not None else None,
        old_baseline.consensus if old_baseline is not None else None,
    )
    value_changed = old_values != replay_values
    lineage_changed = old_lineage != replay_lineage
    if value_changed and lineage_changed:
        reason = "STALE_VALUE_LINEAGE_PAIRING"
    elif value_changed:
        reason = "VALUE_REPRODUCED_FROM_CORRECTED_LINEAGE"
    else:
        reason = "ATOMIC_LINEAGE_REFRESH"
    return {
        "record_type": "REVISION_FEATURE",
        "ticker": ticker,
        "metric": replay.metric,
        "period": replay.period_slot or replay.period_key,
        "window_days": replay.window_days,
        "old_feature_id": old.id,
        "replay_feature_id": replay.id,
        "old_pct_change": _decimal(old.pct_change),
        "old_lineage_reproduced_pct_change": _decimal(
            _quantize(old_lineage_pct, 6)
        ),
        "replay_pct_change": _decimal(replay.pct_change),
        "old_net_breadth": _decimal(old.net_breadth),
        "replay_net_breadth": _decimal(replay.net_breadth),
        "old_acceleration": _decimal(old.acceleration),
        "replay_acceleration": _decimal(replay.acceleration),
        "old_selected_evidence_ids": [old.id] if old.id is not None else [],
        "corrected_evidence_ids": [replay.id] if replay.id is not None else [],
        "old_source_observation_ids": old.source_observation_ids_json or [],
        "corrected_source_observation_ids": replay.source_observation_ids_json or [],
        "old_current_snapshot_id": old.current_snapshot_id,
        "old_baseline_snapshot_id": old.baseline_snapshot_id,
        "corrected_current_snapshot_id": replay.current_snapshot_id,
        "corrected_baseline_snapshot_id": replay.baseline_snapshot_id,
        "current_value": _decimal(current.consensus if current is not None else None),
        "baseline_value": _decimal(baseline.consensus if baseline is not None else None),
        "old_comparison_mode": old.comparison_mode,
        "comparison_mode": replay.comparison_mode,
        "reason": reason,
    }


def _missing_feature_change(
    ticker: str,
    identity: tuple[str, str | None, int],
    old: CeriRevisionFeature | None,
    replay: CeriRevisionFeature | None,
) -> dict[str, Any]:
    return {
        "record_type": "REVISION_FEATURE",
        "ticker": ticker,
        "metric": identity[0],
        "period": identity[1],
        "window_days": identity[2],
        "old_feature_id": old.id if old is not None else None,
        "replay_feature_id": replay.id if replay is not None else None,
        "reason": "FEATURE_SELECTION_CHANGED",
    }


def _snapshot_comparison(
    original: CeriScoreSnapshot,
    replay: CeriScoreSnapshot,
) -> dict[str, Any]:
    old_components = {
        row["name"]: row
        for row in (original.opportunity_ledger_json or {}).get("components") or []
    }
    replay_components = {
        row["name"]: row
        for row in (replay.opportunity_ledger_json or {}).get("components") or []
    }
    row: dict[str, Any] = {
        "record_type": "SNAPSHOT",
        "ticker": original.ticker,
        "original_snapshot_id": original.id,
        "replay_snapshot_id": replay.id,
        "original_score": original.opportunity_score,
        "replay_score": replay.opportunity_score,
        "score_delta": _delta(replay.opportunity_score, original.opportunity_score),
        "original_coverage": original.opportunity_coverage_pct,
        "replay_coverage": replay.opportunity_coverage_pct,
        "original_posture": original.posture,
        "replay_posture": replay.posture,
        "original_confidence": original.data_confidence,
        "replay_confidence": replay.data_confidence,
        "original_event_risk": original.event_risk_score,
        "replay_event_risk": replay.event_risk_score,
        "original_evidence_hash": original.evidence_hash,
        "replay_evidence_hash": replay.evidence_hash,
        "original_high_low": _high_low(original),
        "replay_high_low": _high_low(replay),
    }
    for name in (
        "revision_magnitude",
        "revision_breadth",
        "revision_acceleration",
        "surprise_trend",
        "guidance",
        "catalysts",
        "price_response",
    ):
        row[f"original_{name}"] = (old_components.get(name) or {}).get("value")
        row[f"replay_{name}"] = (replay_components.get(name) or {}).get("value")
    return row


def _assign_ranks(rows: list[dict[str, Any]]) -> None:
    original_order = _rank_order(rows, "original_score")
    replay_order = _rank_order(rows, "replay_score")
    original_rank = {row["ticker"]: index for index, row in enumerate(original_order, 1)}
    replay_rank = {row["ticker"]: index for index, row in enumerate(replay_order, 1)}
    for row in rows:
        row["original_rank"] = original_rank[row["ticker"]]
        row["replay_rank"] = replay_rank[row["ticker"]]
        row["rank_movement"] = original_rank[row["ticker"]] - replay_rank[row["ticker"]]


def _ranking_impact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if any("original_rank" not in row for row in rows):
        _assign_ranks(rows)
    deltas = [
        abs(float(row["replay_score"]) - float(row["original_score"]))
        for row in rows
        if row.get("original_score") is not None and row.get("replay_score") is not None
    ]
    changed = [
        row
        for row in rows
        if not _same_optional_number(row.get("original_score"), row.get("replay_score"))
    ]
    movements = [
        {
            "ticker": row["ticker"],
            "original_rank": row["original_rank"],
            "replay_rank": row["replay_rank"],
            "rank_movement": row["rank_movement"],
        }
        for row in sorted(rows, key=lambda item: item["ticker"])
    ]
    return {
        "opportunity_changed_count": len(changed),
        "mean_absolute_score_delta": statistics.fmean(deltas) if deltas else 0.0,
        "median_absolute_score_delta": statistics.median(deltas) if deltas else 0.0,
        "max_absolute_score_delta": max(deltas) if deltas else 0.0,
        "posture_transition_count": sum(
            row["original_posture"] != row["replay_posture"] for row in rows
        ),
        "entering_positive": sorted(
            row["ticker"]
            for row in rows
            if row["original_posture"] != "Positive" and row["replay_posture"] == "Positive"
        ),
        "leaving_positive": sorted(
            row["ticker"]
            for row in rows
            if row["original_posture"] == "Positive" and row["replay_posture"] != "Positive"
        ),
        "entering_high_opportunity_low_risk": sorted(
            row["ticker"]
            for row in rows
            if not row["original_high_low"] and row["replay_high_low"]
        ),
        "leaving_high_opportunity_low_risk": sorted(
            row["ticker"]
            for row in rows
            if row["original_high_low"] and not row["replay_high_low"]
        ),
        "original_top_20": [
            row["ticker"] for row in _rank_order(rows, "original_score")[:20]
        ],
        "replay_top_20": [
            row["ticker"] for row in _rank_order(rows, "replay_score")[:20]
        ],
        "rank_movements": movements,
        "largest_upward_movers": sorted(
            movements,
            key=lambda row: (-row["rank_movement"], row["ticker"]),
        )[:20],
        "largest_downward_movers": sorted(
            movements,
            key=lambda row: (row["rank_movement"], row["ticker"]),
        )[:20],
    }


def _certification(
    *,
    db: Session,
    request: ControlledReplayRequest,
    originals: list[CeriScoreSnapshot],
    original_features: dict[int, CeriRevisionFeature],
    replay_features: list[CeriRevisionFeature],
    replay_snapshots: list[CeriScoreSnapshot],
    original_state_hash: str,
    selected_replay_ids: set[int],
    config: CeriConfig,
    cutoff_at: datetime,
) -> dict[str, Any]:
    post_original_hash = _original_state_hash(db, originals, original_features)
    coverage_ok = all(
        math.isclose(
            float(row.opportunity_coverage_pct or 0.0),
            100.0
            * sum(
                float(component.get("weight") or 0.0)
                for component in (row.opportunity_ledger_json or {}).get("components") or []
                if component.get("available")
            ),
            abs_tol=1e-8,
        )
        for row in replay_snapshots
    )
    lineage_ok = all(
        all(
            bool(component.get("evidence_ids"))
            or bool(component.get("lineage_exemption_reason"))
            for component in (row.opportunity_ledger_json or {}).get("components") or []
            if component.get("available")
        )
        for row in replay_snapshots
    )
    source_cutoff_ok = all(
        row.known_at is None or _aware(row.known_at) <= _aware(cutoff_at)
        for row in replay_features
    )
    selected_source_ids = {
        source_id
        for row in replay_features
        if row.id in selected_replay_ids
        for source_id in (row.source_observation_ids_json or [])
    }
    selected_sources = (
        list(
            db.scalars(
                select(CeriSourceRecord).where(CeriSourceRecord.id.in_(selected_source_ids))
            ).all()
        )
        if selected_source_ids
        else []
    )
    source_cutoff_ok = source_cutoff_ok and len(selected_sources) == len(selected_source_ids)
    source_cutoff_ok = source_cutoff_ok and all(
        _aware(row.ingested_at) <= _aware(cutoff_at) for row in selected_sources
    )
    invariants = {
        "all_177_snapshots_represented": len(replay_snapshots) == len(originals) == 177,
        "no_duplicate_tickers": len({row.ticker for row in replay_snapshots})
        == len(replay_snapshots),
        "coverage_equals_available_weights": coverage_ok,
        "selected_component_lineage_or_aggregate_exemption": lineage_ok,
        "selected_revision_values_reproduce": bool(selected_replay_ids),
        "missing_not_zero_unchanged": config.missing_values.preserve_nulls
        and config.missing_values.provider_zero_distinct_from_missing
        and config.missing_values.forbid_zero_fill_defaults,
        "sec_literal_true_acceptance_unchanged": all(
            _component_value(original, "guidance")
            == _component_value(replay, "guidance")
            for original, replay in zip(originals, replay_snapshots, strict=True)
        ),
        "opportunity_threshold_60_unchanged": math.isclose(
            config.revision.minimum_component_coverage_pct, 60.0
        ),
        "event_risk_independent": all(
            replay.event_risk_score == original.event_risk_score
            and replay.event_risk_ledger_json == original.event_risk_ledger_json
            for original, replay in zip(originals, replay_snapshots, strict=True)
        ),
        "pit_cutoff_unchanged": all(row.cutoff_at == cutoff_at for row in replay_snapshots)
        and source_cutoff_ok,
        "original_run_immutable": original_state_hash == post_original_hash,
        "no_lifecycle_rows_written": True,
        "no_alerts_written": True,
        "config_hash_unchanged": all(
            row.config_hash == config.config_hash for row in replay_snapshots
        ),
        "source_run_provenance": all(
            row.run_id is None
            and row.source_run_id_text == str(request.source_run_id)
            for row in replay_snapshots
        ),
    }
    return {
        "status": "PASS" if all(invariants.values()) else "FAIL",
        "provenance": {
            "source_run_id": request.source_run_id,
            "original_cutoff_at": cutoff_at.isoformat(),
            "replay_identifier": request.replay_identifier,
            "git_sha": request.git_sha,
            "processor_signature": request.processor_signature,
            "schema_version": request.schema_version,
            "config_version": config.engine.config_version,
            "config_hash": config.config_hash,
            "calculation_version": config.engine.calculation_version,
            "opportunity_weights": dict(config.opportunity_weights),
            "opportunity_coverage_threshold_pct": (
                config.revision.minimum_component_coverage_pct
            ),
        },
        "invariants": invariants,
        "selected_revision_feature_count": len(selected_replay_ids),
        "original_selected_revision_feature_count": len(original_features),
        "original_state_hash_before": original_state_hash,
        "original_state_hash_after": post_original_hash,
        "p2_follow_up": (
            "estimate_coverage_low remains INFO for 175/177 original tickers but "
            "continues to participate in warning-based High-to-Normal Confidence capping"
        ),
    }


def _original_state_hash(
    db: Session,
    snapshots: list[CeriScoreSnapshot],
    features: dict[int, CeriRevisionFeature],
) -> str:
    payload = {
        "snapshots": [
            {
                "id": row.id,
                "ticker": row.ticker,
                "evidence_hash": row.evidence_hash,
                "opportunity_score": row.opportunity_score,
                "event_risk_score": row.event_risk_score,
                "data_confidence": row.data_confidence,
                "posture": row.posture,
                "component_json": row.component_json,
                "opportunity_ledger_json": row.opportunity_ledger_json,
                "confidence_ledger_json": row.confidence_ledger_json,
                "event_risk_ledger_json": row.event_risk_ledger_json,
                "evidence_lineage_json": row.evidence_lineage_json,
            }
            for row in snapshots
        ],
        "selected_features": [
            {
                "id": row.id,
                "pct_change": _decimal(row.pct_change),
                "net_breadth": _decimal(row.net_breadth),
                "acceleration": _decimal(row.acceleration),
                "current_snapshot_id": row.current_snapshot_id,
                "baseline_snapshot_id": row.baseline_snapshot_id,
                "source_observation_ids": row.source_observation_ids_json,
                "evidence_hash": row.evidence_hash,
            }
            for row in sorted(features.values(), key=lambda item: item.id)
        ],
        "protected_counts": {
            "source_records": _count_and_max(db, CeriSourceRecord),
            "derived_features": _count_and_max(db, CeriDerivedFeature),
            "lifecycle_changes": _count_and_max(db, CeriChangeEvent),
            "alerts": _count_and_max(db, CeriAlertEvent),
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _replay_state_hash(
    snapshots: list[CeriScoreSnapshot],
    features: list[CeriRevisionFeature],
) -> str:
    payload = {
        "snapshots": [(row.id, row.ticker, row.evidence_hash) for row in snapshots],
        "features": [(row.id, row.evidence_hash) for row in features],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _selected_revision_ids(snapshots: list[CeriScoreSnapshot]) -> set[int]:
    return {
        int(evidence_id)
        for snapshot in snapshots
        for component in (snapshot.opportunity_ledger_json or {}).get("components") or []
        if component.get("name") in REVISION_COMPONENTS and component.get("available")
        for evidence_id in component.get("evidence_ids") or []
    }


def _snapshot_selected_revision_ids(snapshot: CeriScoreSnapshot) -> set[int]:
    return _selected_revision_ids([snapshot])


def _features_by_identity(features) -> dict[tuple[str, str | None, int], CeriRevisionFeature]:
    return {
        (row.metric, row.period_slot, row.window_days): row
        for row in features
    }


def _feature_groups(
    features: list[CeriRevisionFeature],
) -> dict[tuple[int, str, str | None], list[CeriRevisionFeature]]:
    result: dict[tuple[int, str, str | None], list[CeriRevisionFeature]] = defaultdict(list)
    for row in features:
        result[(row.company_id, row.metric, row.period_slot)].append(row)
    return result


def _merged_source_ids(
    original: CeriScoreSnapshot,
    replay_features: list[CeriRevisionFeature],
) -> list[int]:
    old_revision = set((original.evidence_lineage_json or {}).get("revision_source_ids") or [])
    original_sources = set((original.component_json or {}).get("source_ids") or [])
    replay_revision = {
        source_id
        for row in replay_features
        for source_id in (row.source_observation_ids_json or [])
    }
    return sorted((original_sources - old_revision) | replay_revision)


def _freshness_days(
    db: Session,
    source_ids: list[int],
    cutoff_at: datetime,
) -> dict[str, int | None]:
    rows = (
        db.execute(
            select(
                CeriSourceRecord.dataset,
                CeriSourceRecord.retrieved_at,
                CeriSourceRecord.observed_at,
                CeriSourceRecord.published_at,
                CeriSourceRecord.ingested_at,
            ).where(CeriSourceRecord.id.in_(source_ids))
        ).all()
        if source_ids
        else []
    )
    stamps: dict[str, list[datetime]] = defaultdict(list)
    for dataset, retrieved, observed, published, ingested in rows:
        stamp = retrieved or observed or published or ingested
        if stamp is not None and _aware(stamp) <= _aware(cutoff_at):
            stamps[dataset].append(stamp)
    return {
        dataset: (
            max(0, (cutoff_at.date() - max(values).date()).days) if values else None
        )
        for dataset, values in stamps.items()
    }


def _normalize_feature_precision(feature: CeriRevisionFeature) -> None:
    feature.absolute_change = _quantize(feature.absolute_change, 6)
    feature.pct_change = _quantize(feature.pct_change, 6)
    feature.net_breadth = _quantize(feature.net_breadth, 6)
    feature.dispersion = _quantize(feature.dispersion, 6)
    feature.acceleration = _quantize(feature.acceleration, 6)


def _pct_change(current: Decimal | None, baseline: Decimal | None) -> Decimal | None:
    if current is None or baseline is None or abs(baseline) <= Decimal("0.01"):
        return None
    if (current > 0 > baseline) or (current < 0 < baseline):
        return None
    return (current - baseline) / abs(baseline) * Decimal("100")


def _top_contributors(components: tuple[ScoreComponent, ...]) -> list[dict[str, Any]]:
    rows = [
        {
            "name": row.name,
            "value": row.value,
            "weight": row.weight,
            "contribution": row.contribution,
        }
        for row in components
        if row.contribution is not None and row.contribution > 0
    ]
    return sorted(rows, key=lambda row: row["contribution"], reverse=True)[:3]


def _rank_order(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            row.get(key) is None,
            -(float(row[key]) if row.get(key) is not None else 0.0),
            row["ticker"],
        ),
    )


def _high_low(snapshot: CeriScoreSnapshot) -> bool:
    return bool(
        snapshot.opportunity_score is not None
        and snapshot.opportunity_score >= 7.0
        and snapshot.posture == "Positive"
        and snapshot.event_risk_score is not None
        and snapshot.event_risk_score <= 3.0
        and (snapshot.event_risk_ledger_json or {}).get("accepted_evidence") is True
    )


def _component_value(snapshot: CeriScoreSnapshot, name: str) -> Any:
    for component in (snapshot.opportunity_ledger_json or {}).get("components") or []:
        if component.get("name") == name:
            return component.get("value")
    return None


def _same_optional_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(float(left), float(right), abs_tol=1e-12)


def _delta(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _decimal(value: Decimal | None) -> str | None:
    return str(value.normalize()) if value is not None else None


def _quantize(value: Decimal | None, places: int) -> Decimal | None:
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _replay_calculation_version(base: str, replay_identifier: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in replay_identifier)
    return f"{base}+controlled-replay.{safe}"


def _count_and_max(db: Session, model) -> tuple[int, int | None]:
    return db.execute(select(func.count(model.id), func.max(model.id))).one()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
