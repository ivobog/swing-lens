from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    PredictionEligibility,
    PriceBar,
    PriceBarRevision,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTrainingEligibilityDecision,
    WinnerTrainingOutcomeReplay,
)
from app.services.price_bar_repository import load_preferred_price_bar_rows
from app.services.winner_probability.config import (
    SAME_BAR_CONSERVATIVE_STOP_FIRST,
    WinnerProbabilityConfig,
)
from app.services.winner_probability.target_stop_service import TargetStopService
from app.services.winner_probability.trading_session_service import (
    horizon_due_session,
    latest_completed_session,
    next_regular_session,
)

TRAINING_FAMILY = "OWPE_1_1_COMPAT_V1"
POLICY_VERSION = "owpe-pre11-eligibility-1.0.0"
BRIDGE_VERSION = "owpe-training-compat-1.0.0"
REPLAY_POLICY_VERSION = "owpe-pre11-replay-1.0.0"
REPLAY_METHOD = "PRE11_TO_11_TRAINING_REPLAY"
EVIDENCE_ORIGIN_NATIVE = "NATIVE_1_1"
EVIDENCE_ORIGIN_PRE11 = "PRE11_COMPAT_REPLAY"

IDENTICAL = "IDENTICAL_SEMANTICS"
MAPPABLE = "MAPPABLE_WITHOUT_LOOKAHEAD"
OPTIONAL_MISSING = "OPTIONAL_MISSING_ALLOWED"
INCOMPATIBLE = "INCOMPATIBLE"

# This bridge is intentionally allow-listed by immutable source identity.  It
# records semantic equivalence; it does not overwrite either identity.
PRE11_SOURCE_CONFIG_HASH = "2260060ab44d6f46ccff94d61943bbdfcaa49b734ef2ccf177b71dc50f225184"
PRE11_SOURCE_CALCULATION = "owpe-calc-1.0.0"
PRE11_SOURCE_FEATURE_SCHEMA = "owpe-features-1.0.0"

REQUIRED_LINEAGE_IDS = (
    "raw_row_id",
    "technical_score_id",
    "fundamental_score_id",
    "combined_result_id",
    "market_regime_snapshot_id",
)
REQUIRED_FEATURES = (
    "setup_family",
    "technical_score",
    "combined_score",
    "market_regime",
    "market_risk_state",
    "technical_data_quality",
    "universe_provenance",
)
OPTIONAL_FEATURES = (
    "ranking_profile",
    "fundamental_score",
    "fundamental_coverage",
    "sector_state",
    "sector_rank",
    "sector_leadership_bucket",
    "earnings_risk",
    "reward_risk",
    "screener_provenance",
)


@dataclass(frozen=True)
class Pre11CompatibilityScope:
    training_family: str
    outcome_definition_id: int
    cutoff_at: datetime
    start_date: date
    end_date: date

    def validate(self) -> None:
        if self.training_family != TRAINING_FAMILY:
            raise ValueError(f"unsupported training family: {self.training_family}")
        if not self.outcome_definition_id:
            raise ValueError("outcome_definition_id is required")
        if self.cutoff_at.tzinfo is None:
            raise ValueError("cutoff_at must be timezone-aware")
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")

    @property
    def request_key(self) -> str:
        return _hash(
            {
                "training_family": self.training_family,
                "outcome_definition_id": self.outcome_definition_id,
                "cutoff_at": self.cutoff_at.isoformat(),
                "start_date": self.start_date.isoformat(),
                "end_date": self.end_date.isoformat(),
                "policy_version": POLICY_VERSION,
                "bridge_version": BRIDGE_VERSION,
            }
        )


@dataclass(frozen=True)
class ReplayPreview:
    prediction_id: int
    source_forward_outcome_id: int
    source_forward_outcome_revision: int
    entry_session: date
    due_session: date
    entry_price: Decimal
    exit_price: Decimal
    close_return_pct: Decimal
    mfe_pct: Decimal
    mae_pct: Decimal
    target_hit: bool
    stop_hit: bool
    first_event: str
    event_session: date | None
    same_bar_conflict: bool
    primary_winner: bool
    optimistic_winner: bool
    conservative_winner: bool
    bar_lineage: tuple[dict[str, Any], ...]
    bar_lineage_hash: str
    source_revision_cutoff_at: datetime


@dataclass(frozen=True)
class SnapshotCompatibility:
    prediction_id: int
    passed_stages: tuple[str, ...]
    reason_codes: tuple[str, ...]
    feature_compatibility: dict[str, str]
    config_compatibility: dict[str, Any]
    source_manifest_hash: str
    replay: ReplayPreview | None

    @property
    def training_allowed(self) -> bool:
        return not self.reason_codes and self.replay is not None


@dataclass(frozen=True)
class Pre11DryRunResult:
    scope: Pre11CompatibilityScope
    generated_at: datetime
    funnel_counts: dict[str, int]
    reason_frequencies: dict[str, int]
    classifications: tuple[SnapshotCompatibility, ...]
    manifest_hash: str

    @property
    def final_training_eligible(self) -> int:
        return self.funnel_counts["final_training_eligible"]

    @property
    def eligible_prediction_ids(self) -> tuple[int, ...]:
        return tuple(row.prediction_id for row in self.classifications if row.training_allowed)

    def manifest_payload(self) -> dict[str, Any]:
        return {
            "dry_run": True,
            "write_count": 0,
            "policy_version": POLICY_VERSION,
            "compatibility_bridge_version": BRIDGE_VERSION,
            "scope": {
                **asdict(self.scope),
                "cutoff_at": self.scope.cutoff_at.isoformat(),
                "start_date": self.scope.start_date.isoformat(),
                "end_date": self.scope.end_date.isoformat(),
                "request_key": self.scope.request_key,
            },
            "funnel_counts": self.funnel_counts,
            "reason_frequencies": self.reason_frequencies,
            "eligible_prediction_ids": list(self.eligible_prediction_ids),
            "manifest_hash": self.manifest_hash,
        }


class Pre11CompatibilityService:
    """Read-only classifier for native pre-1.1 immutable snapshots."""

    STAGES = (
        "historical_snapshots_considered",
        "native_snapshots",
        "pit_valid",
        "prediction_eligible",
        "lineage_sufficient",
        "feature_compatible",
        "config_semantics_compatible",
        "outcome_replay_possible",
        "quality_allowed",
        "independent_episode_representatives",
        "inside_rolling_window",
        "final_training_eligible",
    )

    def __init__(
        self,
        replay_resolver: Callable[
            [Session, WinnerPredictionSnapshot, WinnerOutcomeDefinition, datetime],
            ReplayPreview | None,
        ]
        | None = None,
    ) -> None:
        self._uses_default_replay_resolver = replay_resolver is None
        self.replay_resolver = replay_resolver or self._replay_preview
        self._forward_cache: dict[int, WinnerForwardOutcome] | None = None
        self._bar_cache: dict[tuple[str, str], list[PriceBar]] | None = None
        self._bar_revision_cache: dict[tuple[int, int], PriceBarRevision] | None = None

    def dry_run(
        self,
        db: Session,
        *,
        scope: Pre11CompatibilityScope,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
        predictions: Iterable[WinnerPredictionSnapshot] | None = None,
    ) -> Pre11DryRunResult:
        scope.validate()
        self._validate_target(scope, outcome_definition, config)
        rows = (
            list(predictions)
            if predictions is not None
            else list(
                db.scalars(
                    select(WinnerPredictionSnapshot)
                    .where(WinnerPredictionSnapshot.calculation_version == PRE11_SOURCE_CALCULATION)
                    .where(WinnerPredictionSnapshot.prediction_as_of_date >= scope.start_date)
                    .where(WinnerPredictionSnapshot.prediction_as_of_date <= scope.end_date)
                    .where(WinnerPredictionSnapshot.source_data_cutoff_at < scope.cutoff_at)
                    .order_by(
                        WinnerPredictionSnapshot.prediction_as_of_date,
                        WinnerPredictionSnapshot.source_data_cutoff_at,
                        WinnerPredictionSnapshot.id,
                    )
                )
            )
        )
        if self._uses_default_replay_resolver:
            self._prepare_replay_cache(db, rows, outcome_definition)
        counts = {name: 0 for name in self.STAGES}
        reasons: Counter[str] = Counter()
        classifications: list[SnapshotCompatibility] = []
        seen_episodes: set[int] = set()
        rolling_start = _subtract_years(scope.cutoff_at.date(), config.cohort.rolling_window_years)

        for prediction in rows:
            counts["historical_snapshots_considered"] += 1
            passed = ["historical_snapshots_considered"]
            row_reasons: list[str] = []
            feature_map = self._feature_compatibility(prediction)
            config_map = self._config_compatibility(prediction, outcome_definition, config)
            replay: ReplayPreview | None = None

            if prediction.reconstruction_method is not None:
                row_reasons.append("SNAPSHOT_RECONSTRUCTED")
            else:
                counts["native_snapshots"] += 1
                passed.append("native_snapshots")

            if not row_reasons:
                pit_reason = self._pit_reason(prediction)
                if pit_reason:
                    row_reasons.append(pit_reason)
                else:
                    counts["pit_valid"] += 1
                    passed.append("pit_valid")

            if not row_reasons:
                if prediction.eligibility_status != PredictionEligibility.ELIGIBLE:
                    row_reasons.append("PREDICTION_NOT_ELIGIBLE")
                else:
                    counts["prediction_eligible"] += 1
                    passed.append("prediction_eligible")

            if not row_reasons:
                lineage_reason = self._lineage_reason(prediction)
                if lineage_reason:
                    row_reasons.append(lineage_reason)
                else:
                    counts["lineage_sufficient"] += 1
                    passed.append("lineage_sufficient")

            if not row_reasons:
                incompatible = sorted(k for k, v in feature_map.items() if v == INCOMPATIBLE)
                if incompatible:
                    row_reasons.append("FEATURE_INCOMPATIBLE:" + ",".join(incompatible))
                else:
                    counts["feature_compatible"] += 1
                    passed.append("feature_compatible")

            if not row_reasons:
                if not config_map["compatible"]:
                    row_reasons.append("CONFIG_SEMANTICS_INCOMPATIBLE")
                else:
                    counts["config_semantics_compatible"] += 1
                    passed.append("config_semantics_compatible")

            if not row_reasons:
                replay = self.replay_resolver(db, prediction, outcome_definition, scope.cutoff_at)
                if replay is None:
                    row_reasons.append("ACTIVE_LABEL_REPLAY_NOT_POSSIBLE")
                else:
                    counts["outcome_replay_possible"] += 1
                    passed.append("outcome_replay_possible")

            if not row_reasons:
                quality_reason = self._quality_reason(prediction)
                if quality_reason:
                    row_reasons.append(quality_reason)
                else:
                    counts["quality_allowed"] += 1
                    passed.append("quality_allowed")

            if not row_reasons:
                if bool((prediction.lineage_json or {}).get("dependent_episode")):
                    row_reasons.append("DEPENDENT_EPISODE")
                elif prediction.episode_id is not None and prediction.episode_id in seen_episodes:
                    row_reasons.append("DUPLICATE_EPISODE_REPRESENTATIVE")
                else:
                    if prediction.episode_id is not None:
                        seen_episodes.add(prediction.episode_id)
                    counts["independent_episode_representatives"] += 1
                    passed.append("independent_episode_representatives")

            if not row_reasons:
                if prediction.prediction_as_of_date < rolling_start:
                    row_reasons.append("OUTSIDE_ROLLING_WINDOW")
                else:
                    counts["inside_rolling_window"] += 1
                    passed.append("inside_rolling_window")

            if not row_reasons:
                counts["final_training_eligible"] += 1
                passed.append("final_training_eligible")
            else:
                reasons.update(row_reasons)

            classifications.append(
                SnapshotCompatibility(
                    prediction_id=prediction.id,
                    passed_stages=tuple(passed),
                    reason_codes=tuple(row_reasons),
                    feature_compatibility=feature_map,
                    config_compatibility=config_map,
                    source_manifest_hash=self._source_manifest_hash(prediction),
                    replay=replay,
                )
            )

        manifest_hash = _hash(
            {
                "scope_request_key": scope.request_key,
                "policy_version": POLICY_VERSION,
                "eligible": [
                    {
                        "prediction_id": row.prediction_id,
                        "source_manifest_hash": row.source_manifest_hash,
                        "bar_lineage_hash": row.replay.bar_lineage_hash if row.replay else None,
                    }
                    for row in classifications
                    if row.training_allowed
                ],
            }
        )
        return Pre11DryRunResult(
            scope=scope,
            generated_at=datetime.now(UTC),
            funnel_counts=counts,
            reason_frequencies=dict(sorted(reasons.items())),
            classifications=tuple(classifications),
            manifest_hash=manifest_hash,
        )

    @staticmethod
    def _validate_target(
        scope: Pre11CompatibilityScope,
        outcome: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
    ) -> None:
        primary = config.primary_outcome_definition
        expected = (
            primary.id,
            primary.entry_model,
            primary.horizon_sessions,
            Decimal(str(primary.target_pct)),
            Decimal(str(primary.stop_pct)),
            primary.same_bar_conflict_policy,
        )
        actual = (
            outcome.definition_id,
            outcome.entry_model,
            outcome.horizon_sessions,
            Decimal(str(outcome.target_pct)),
            Decimal(str(outcome.stop_pct)),
            outcome.same_bar_conflict_policy,
        )
        if scope.outcome_definition_id != outcome.id or actual != expected or not outcome.is_active:
            raise ValueError("scope does not identify the active primary outcome definition")

    @staticmethod
    def _pit_reason(prediction: WinnerPredictionSnapshot) -> str | None:
        lineage = prediction.lineage_json or {}
        if lineage.get("point_in_time_validated") is not True:
            return "PIT_NOT_VALIDATED"
        if prediction.superseded_at is not None:
            return "SNAPSHOT_SUPERSEDED"
        completed = latest_completed_session(prediction.source_data_cutoff_at)
        if prediction.prediction_as_of_date != completed:
            return "PIT_SIGNAL_SESSION_MISMATCH"
        if prediction.planned_entry_session != next_regular_session(completed):
            return "PIT_ENTRY_SESSION_MISMATCH"
        return None

    @staticmethod
    def _lineage_reason(prediction: WinnerPredictionSnapshot) -> str | None:
        source_ids = prediction.source_ids_json or {}
        if any(not source_ids.get(name) for name in REQUIRED_LINEAGE_IDS):
            return "SOURCE_LINEAGE_INSUFFICIENT"
        audit = (prediction.lineage_json or {}).get("feature_cutoff_audit")
        if not isinstance(audit, dict) or not audit:
            return "FEATURE_CUTOFF_AUDIT_MISSING"
        cutoff = prediction.source_data_cutoff_at
        for value in audit.values():
            available = value.get("source_available_at") if isinstance(value, dict) else None
            if not available:
                continue
            parsed = datetime.fromisoformat(available)
            if parsed > cutoff:
                return "SOURCE_AVAILABLE_AFTER_CUTOFF"
        return None

    @staticmethod
    def _feature_compatibility(prediction: WinnerPredictionSnapshot) -> dict[str, str]:
        features = prediction.feature_json or {}
        result: dict[str, str] = {}
        for name in REQUIRED_FEATURES:
            result[name] = IDENTICAL if features.get(name) is not None else INCOMPATIBLE
        for name in OPTIONAL_FEATURES:
            result[name] = IDENTICAL if features.get(name) is not None else OPTIONAL_MISSING
        # These are deterministic snapshot-derived cohort dimensions, not reads
        # from today's mutable rows.
        for name in ("score_band", "dual_score_band", "market_regime_family"):
            result[name] = MAPPABLE if features.get(name) is not None else INCOMPATIBLE
        return result

    @staticmethod
    def _config_compatibility(
        prediction: WinnerPredictionSnapshot,
        outcome: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
    ) -> dict[str, Any]:
        source_ok = (
            prediction.config_hash == PRE11_SOURCE_CONFIG_HASH
            and prediction.calculation_version == PRE11_SOURCE_CALCULATION
            and prediction.feature_schema_version == PRE11_SOURCE_FEATURE_SCHEMA
        )
        target = config.primary_outcome_definition
        label_ok = (
            target.id == outcome.definition_id
            and target.entry_model == outcome.entry_model == "NEXT_OPEN"
            and target.horizon_sessions == outcome.horizon_sessions == 5
            and Decimal(str(target.target_pct))
            == Decimal(str(outcome.target_pct))
            == Decimal("2.5")
            and Decimal(str(target.stop_pct)) == Decimal(str(outcome.stop_pct)) == Decimal("2.0")
            and target.same_bar_conflict_policy
            == outcome.same_bar_conflict_policy
            == SAME_BAR_CONSERVATIVE_STOP_FIRST
        )
        return {
            "compatible": source_ok and label_ok,
            "bridge_version": BRIDGE_VERSION,
            "literal_hash_equal": prediction.config_hash == config.config_hash,
            "source_config_hash": prediction.config_hash,
            "target_config_hash": config.config_hash,
            "semantic_checks": {
                "target_stop_horizon_entry_same_bar": label_ok,
                "episode_policy": "TARGET_POLICY_APPLIED_AT_TRAINING",
                "rolling_window": "TARGET_POLICY_APPLIED_AT_TRAINING",
                "score_bands": "SNAPSHOT_DERIVED_VALUES_RETAINED",
                "cohort_dimensions": "MISSING_DIMENSIONS_RESTRICT_ONLY_REQUIRING_LEVELS",
                "quality_gates": "TARGET_POLICY_APPLIED_AT_TRAINING",
                "evidence_thresholds": "TARGET_SERVING_POLICY_NOT_SOURCE_LABEL_SEMANTICS",
            },
        }

    @staticmethod
    def _quality_reason(prediction: WinnerPredictionSnapshot) -> str | None:
        flags = {
            str(value)
            for value in (
                list((prediction.lineage_json or {}).get("source_quality_flags", []))
                + list(prediction.warning_flags_json or [])
            )
        }
        blocking = {"quality_blocking", "invalid_source", "exclude_from_production_training"}
        return "QUALITY_BLOCKING" if flags & blocking else None

    @staticmethod
    def _source_manifest_hash(prediction: WinnerPredictionSnapshot) -> str:
        return _hash(
            {
                "prediction_id": prediction.id,
                "revision": prediction.revision,
                "feature_vector_hash": prediction.feature_vector_hash,
                "source_ids": prediction.source_ids_json,
                "feature_cutoff_audit_hash": (prediction.lineage_json or {}).get(
                    "feature_cutoff_audit_hash"
                ),
                "source_data_cutoff_at": prediction.source_data_cutoff_at.isoformat(),
            }
        )

    def _prepare_replay_cache(
        self,
        db: Session,
        predictions: list[WinnerPredictionSnapshot],
        outcome: WinnerOutcomeDefinition,
    ) -> None:
        ids = [row.id for row in predictions]
        if not ids:
            self._forward_cache = {}
            self._bar_cache = {}
            self._bar_revision_cache = {}
            return
        forwards = list(
            db.scalars(
                select(WinnerForwardOutcome)
                .where(WinnerForwardOutcome.prediction_id.in_(ids))
                .where(WinnerForwardOutcome.entry_model == outcome.entry_model)
                .where(WinnerForwardOutcome.horizon_sessions == outcome.horizon_sessions)
                .where(WinnerForwardOutcome.is_current_revision.is_(True))
            )
        )
        self._forward_cache = {row.prediction_id: row for row in forwards}
        windows = [
            (
                next_regular_session(row.prediction_as_of_date),
                horizon_due_session(
                    next_regular_session(row.prediction_as_of_date), outcome.horizon_sessions
                ),
            )
            for row in predictions
        ]
        tickers = sorted({row.ticker.upper() for row in predictions})
        bars = list(
            db.scalars(
                select(PriceBar)
                .where(PriceBar.ticker.in_(tickers))
                .where(PriceBar.timeframe == "1 day")
                .where(PriceBar.what_to_show.in_(("ADJUSTED_LAST", "TRADES")))
                .where(PriceBar.bar_date >= min(window[0] for window in windows))
                .where(PriceBar.bar_date <= max(window[1] for window in windows))
                .order_by(PriceBar.ticker, PriceBar.what_to_show, PriceBar.bar_date)
            )
        )
        grouped: dict[tuple[str, str], list[PriceBar]] = {}
        for bar in bars:
            grouped.setdefault((bar.ticker.upper(), bar.what_to_show), []).append(bar)
        self._bar_cache = grouped
        revised_bar_ids = [bar.id for bar in bars if bar.revision_count]
        revisions = (
            list(
                db.scalars(
                    select(PriceBarRevision).where(
                        PriceBarRevision.price_bar_id.in_(revised_bar_ids)
                    )
                )
            )
            if revised_bar_ids
            else []
        )
        self._bar_revision_cache = {
            (row.price_bar_id, row.revision_number): row for row in revisions
        }

    def _replay_preview(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        outcome: WinnerOutcomeDefinition,
        cutoff_at: datetime,
    ) -> ReplayPreview | None:
        entry = next_regular_session(prediction.prediction_as_of_date)
        due = horizon_due_session(entry, outcome.horizon_sessions)
        if due > latest_completed_session(cutoff_at):
            return None
        source_forward = (
            self._forward_cache.get(prediction.id)
            if self._forward_cache is not None
            else db.scalar(
                select(WinnerForwardOutcome)
                .where(WinnerForwardOutcome.prediction_id == prediction.id)
                .where(WinnerForwardOutcome.entry_model == outcome.entry_model)
                .where(WinnerForwardOutcome.horizon_sessions == outcome.horizon_sessions)
                .where(WinnerForwardOutcome.is_current_revision.is_(True))
            )
        )
        if source_forward is None:
            return None
        if self._bar_cache is None:
            bars = load_preferred_price_bar_rows(
                db, prediction.ticker, start_date=entry, end_date=due
            )
        else:
            ticker = prediction.ticker.upper()
            adjusted = [
                bar
                for bar in self._bar_cache.get((ticker, "ADJUSTED_LAST"), [])
                if entry <= bar.bar_date <= due
            ]
            bars = adjusted or [
                bar
                for bar in self._bar_cache.get((ticker, "TRADES"), [])
                if entry <= bar.bar_date <= due
            ]
        expected: list[date] = []
        session = entry
        for _ in range(outcome.horizon_sessions):
            expected.append(session)
            if session != due:
                session = next_regular_session(session)
        by_date = {bar.bar_date: bar for bar in bars}
        if sorted(by_date) != expected:
            return None
        ordered = [by_date[session_date] for session_date in expected]
        if any(
            value is None or Decimal(str(value)) <= 0
            for bar in ordered
            for value in (bar.open, bar.high, bar.low, bar.close)
        ):
            return None
        what_to_show = {bar.what_to_show for bar in ordered}
        adjustments = {bar.adjustment_type for bar in ordered}
        if len(what_to_show) != 1 or len(adjustments) != 1:
            return None
        source_cutoff = max(
            (bar.revised_at or bar.first_seen_at or bar.created_at) for bar in ordered
        )
        if source_cutoff > cutoff_at:
            return None

        entry_price = Decimal(str(ordered[0].open))
        exit_price = Decimal(str(ordered[-1].close))
        close_return = (exit_price / entry_price - Decimal("1")) * Decimal("100")
        mfe = max(
            (Decimal(str(bar.high)) / entry_price - Decimal("1")) * Decimal("100")
            for bar in ordered
        )
        mae = min(
            (Decimal(str(bar.low)) / entry_price - Decimal("1")) * Decimal("100") for bar in ordered
        )
        evaluation = TargetStopService().evaluate(
            bars=ordered,
            entry_price=entry_price,
            target_pct=Decimal(str(outcome.target_pct)),
            stop_pct=Decimal(str(outcome.stop_pct)),
            same_bar_conflict_policy=outcome.same_bar_conflict_policy,
        )
        lineage_rows: list[dict[str, Any]] = []
        for bar in ordered:
            revision = (
                self._bar_revision_cache.get((bar.id, bar.revision_count))
                if self._bar_revision_cache is not None and bar.revision_count
                else None
            )
            lineage_rows.append(
                {
                    "price_bar_id": bar.id,
                    "price_bar_revision_id": revision.id if revision is not None else None,
                    "bar_date": bar.bar_date.isoformat(),
                    "what_to_show": bar.what_to_show,
                    "adjustment_type": bar.adjustment_type,
                    "revision_count": bar.revision_count,
                    "data_hash": bar.data_hash,
                    "observed_at": (
                        bar.revised_at or bar.first_seen_at or bar.created_at
                    ).isoformat(),
                }
            )
        lineage = tuple(lineage_rows)
        lineage_hash = _hash({"bars": lineage})
        return ReplayPreview(
            prediction_id=prediction.id,
            source_forward_outcome_id=source_forward.id,
            source_forward_outcome_revision=source_forward.revision,
            entry_session=entry,
            due_session=due,
            entry_price=entry_price,
            exit_price=exit_price,
            close_return_pct=close_return,
            mfe_pct=mfe,
            mae_pct=mae,
            target_hit=evaluation.target_hit,
            stop_hit=evaluation.stop_hit,
            first_event=evaluation.first_event,
            event_session=evaluation.event_session,
            same_bar_conflict=evaluation.same_bar_conflict,
            primary_winner=evaluation.primary_winner,
            optimistic_winner=evaluation.optimistic_winner,
            conservative_winner=evaluation.conservative_winner,
            bar_lineage=lineage,
            bar_lineage_hash=lineage_hash,
            source_revision_cutoff_at=source_cutoff,
        )


class Pre11CompatibilityWriteService:
    """Explicit second step; callers cannot turn a dry run into an implicit write."""

    def persist_decisions_and_replays(
        self,
        db: Session,
        *,
        dry_run: Pre11DryRunResult,
        reviewed_manifest_path: Path,
        request_key: str,
        approve_write: bool,
        actor: str,
        outcome_definition: WinnerOutcomeDefinition,
        config: WinnerProbabilityConfig,
    ) -> tuple[
        tuple[WinnerTrainingEligibilityDecision, ...],
        tuple[WinnerTrainingOutcomeReplay, ...],
    ]:
        if not approve_write:
            raise PermissionError("explicit approve_write=True is required")
        dry_run.scope.validate()
        if request_key != dry_run.scope.request_key:
            raise ValueError("request key does not match the reviewed dry-run scope")
        if not reviewed_manifest_path.is_file():
            raise ValueError("reviewed manifest path is required")
        reviewed = json.loads(reviewed_manifest_path.read_text(encoding="utf-8"))
        if reviewed.get("manifest_hash") != dry_run.manifest_hash:
            raise ValueError("reviewed manifest hash does not match the dry run")
        if not actor.strip():
            raise ValueError("actor is required")

        now = datetime.now(UTC)
        predictions = {
            row.id: row
            for row in db.scalars(
                select(WinnerPredictionSnapshot).where(
                    WinnerPredictionSnapshot.id.in_(
                        [row.prediction_id for row in dry_run.classifications]
                    )
                )
            )
        }
        decisions: list[WinnerTrainingEligibilityDecision] = []
        replays: list[WinnerTrainingOutcomeReplay] = []
        for classified in dry_run.classifications:
            prediction = predictions[classified.prediction_id]
            decision_payload = {
                "prediction_id": prediction.id,
                "policy_version": POLICY_VERSION,
                "training_family": dry_run.scope.training_family,
                "request_key": request_key,
                "source_manifest_hash": classified.source_manifest_hash,
                "training_allowed": classified.training_allowed,
                "reason_codes": classified.reason_codes,
            }
            decision_hash = _hash(decision_payload)
            existing = db.scalar(
                select(WinnerTrainingEligibilityDecision).where(
                    WinnerTrainingEligibilityDecision.decision_hash == decision_hash
                )
            )
            if existing is not None:
                decisions.append(existing)
                continue
            latest = db.scalar(
                select(WinnerTrainingEligibilityDecision)
                .where(WinnerTrainingEligibilityDecision.prediction_id == prediction.id)
                .where(
                    WinnerTrainingEligibilityDecision.training_family
                    == dry_run.scope.training_family
                )
                .where(WinnerTrainingEligibilityDecision.policy_version == POLICY_VERSION)
                .order_by(WinnerTrainingEligibilityDecision.revision.desc())
                .limit(1)
            )
            decision = WinnerTrainingEligibilityDecision(
                prediction_id=prediction.id,
                policy_version=POLICY_VERSION,
                training_family=dry_run.scope.training_family,
                compatibility_bridge_version=BRIDGE_VERSION,
                source_feature_schema_version=prediction.feature_schema_version,
                source_calculation_version=prediction.calculation_version,
                source_config_hash=prediction.config_hash,
                target_feature_schema_version=config.feature_schema.version,
                target_calculation_version=config.engine.calculation_version,
                target_config_hash=config.config_hash,
                target_outcome_definition_id=outcome_definition.id,
                classification_status="APPROVED" if classified.training_allowed else "REJECTED",
                training_allowed=classified.training_allowed,
                reason_codes_json=list(classified.reason_codes),
                feature_compatibility_json=classified.feature_compatibility,
                config_compatibility_json=classified.config_compatibility,
                outcome_compatibility_status=(
                    "COMPATIBLE" if classified.replay else "INCOMPATIBLE"
                ),
                pit_status=("VALID" if "pit_valid" in classified.passed_stages else "INVALID"),
                episode_status=(
                    "INDEPENDENT_REPRESENTATIVE"
                    if "independent_episode_representatives" in classified.passed_stages
                    else "INELIGIBLE"
                ),
                quality_status=(
                    "ALLOWED" if "quality_allowed" in classified.passed_stages else "BLOCKED"
                ),
                reconstruction_method=REPLAY_METHOD if classified.training_allowed else None,
                source_manifest_hash=classified.source_manifest_hash,
                evidence_manifest_hash=dry_run.manifest_hash,
                revision=(latest.revision + 1 if latest is not None else 1),
                supersedes_decision_id=latest.id if latest is not None else None,
                request_key=request_key,
                decision_hash=decision_hash,
                classified_at=now,
                classified_by=actor,
                metadata_json={"dry_run_manifest_path": str(reviewed_manifest_path)},
            )
            db.add(decision)
            decisions.append(decision)
        db.flush()
        by_prediction = {row.prediction_id: row for row in decisions}
        for classified in dry_run.classifications:
            preview = classified.replay
            if not classified.training_allowed or preview is None:
                continue
            decision = by_prediction[classified.prediction_id]
            replay_payload = {
                "eligibility_decision_id": decision.id,
                "prediction_id": classified.prediction_id,
                "target_outcome_definition_id": outcome_definition.id,
                "request_key": request_key,
                "bar_lineage_hash": preview.bar_lineage_hash,
                "primary_winner": preview.primary_winner,
            }
            replay_hash = _hash(replay_payload)
            existing_replay = db.scalar(
                select(WinnerTrainingOutcomeReplay).where(
                    WinnerTrainingOutcomeReplay.replay_hash == replay_hash
                )
            )
            if existing_replay is not None:
                replays.append(existing_replay)
                continue
            latest_replay = db.scalar(
                select(WinnerTrainingOutcomeReplay)
                .where(
                    WinnerTrainingOutcomeReplay.eligibility_decision_id == decision.id
                )
                .order_by(WinnerTrainingOutcomeReplay.revision.desc())
                .limit(1)
            )
            replay = WinnerTrainingOutcomeReplay(
                eligibility_decision_id=decision.id,
                prediction_id=classified.prediction_id,
                target_outcome_definition_id=outcome_definition.id,
                training_family=dry_run.scope.training_family,
                reconstruction_method=REPLAY_METHOD,
                replay_policy_version=REPLAY_POLICY_VERSION,
                compatibility_bridge_version=BRIDGE_VERSION,
                source_forward_outcome_id=preview.source_forward_outcome_id,
                entry_model=outcome_definition.entry_model,
                horizon_sessions=outcome_definition.horizon_sessions,
                entry_session=preview.entry_session,
                due_session=preview.due_session,
                entry_price=preview.entry_price,
                exit_price=preview.exit_price,
                close_return_pct=preview.close_return_pct,
                mfe_pct=preview.mfe_pct,
                mae_pct=preview.mae_pct,
                target_pct=outcome_definition.target_pct,
                stop_pct=outcome_definition.stop_pct,
                target_hit=preview.target_hit,
                stop_hit=preview.stop_hit,
                first_event=preview.first_event,
                event_session=preview.event_session,
                same_bar_conflict=preview.same_bar_conflict,
                primary_winner=preview.primary_winner,
                optimistic_winner=preview.optimistic_winner,
                conservative_winner=preview.conservative_winner,
                bar_lineage_json={
                    "bars": list(preview.bar_lineage),
                    "source_forward_outcome_revision": preview.source_forward_outcome_revision,
                },
                source_bar_lineage_hash=preview.bar_lineage_hash,
                source_revision_cutoff_at=preview.source_revision_cutoff_at,
                status="MATURED",
                revision=(latest_replay.revision + 1 if latest_replay is not None else 1),
                supersedes_replay_id=latest_replay.id if latest_replay is not None else None,
                request_key=request_key,
                replay_hash=replay_hash,
                replayed_at=now,
                replayed_by=actor,
                metadata_json={"never_replaces_decision_time_estimate": True},
            )
            db.add(replay)
            replays.append(replay)
        db.flush()
        return tuple(decisions), tuple(replays)


def _subtract_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
