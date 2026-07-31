from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.tables import (
    PredictionEligibility,
    WinnerPredictionSnapshot,
)
from app.services.winner_probability.config import (
    WinnerProbabilityConfig,
    load_winner_probability_config,
)
from app.services.winner_probability.decision_time_estimate_service import (
    DecisionTimeEstimateService,
)
from app.services.winner_probability.episode_service import WinnerEpisodeService
from app.services.winner_probability.feature_extractor import (
    ExtractedPredictionFeatures,
    WinnerFeatureExtractor,
)
from app.services.winner_probability.pending_outcome_service import PendingOutcomeService
from app.services.winner_probability.repository import (
    TickerCaptureContext,
    WinnerProbabilityRepository,
)


class WinnerPredictionCaptureConflict(ValueError):
    pass


class WinnerPredictionCaptureCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class WinnerPredictionCaptureResult:
    inserted: int = 0
    duplicate: int = 0
    excluded: int = 0
    failed: int = 0
    warnings: int = 0
    pending_outcomes: int = 0
    target_stop_outcomes: int = 0
    decision_time_estimates: int = 0
    insufficient_estimates: int = 0

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


class WinnerPredictionCaptureService:
    def __init__(
        self,
        *,
        repository: WinnerProbabilityRepository | None = None,
        feature_extractor: WinnerFeatureExtractor | None = None,
        episode_service: WinnerEpisodeService | None = None,
        pending_outcome_service: PendingOutcomeService | None = None,
        decision_time_estimate_service: DecisionTimeEstimateService | None = None,
    ) -> None:
        self.repository = repository or WinnerProbabilityRepository()
        self.feature_extractor = feature_extractor or WinnerFeatureExtractor()
        self.episode_service = episode_service or WinnerEpisodeService(self.repository)
        self.pending_outcome_service = pending_outcome_service or PendingOutcomeService(
            self.repository
        )
        self.decision_time_estimate_service = (
            decision_time_estimate_service or DecisionTimeEstimateService(self.repository)
        )

    def capture_run(
        self,
        db: Session,
        *,
        run_id: int,
        config: WinnerProbabilityConfig | None = None,
        captured_at: datetime | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WinnerPredictionCaptureResult:
        config = config or load_winner_probability_config()
        captured_at = captured_at or datetime.now(UTC)
        run_context = self.repository.load_run_context(db, run_id)

        totals = _MutableCaptureCounts()
        for ticker_context in run_context.tickers:
            if should_cancel is not None and should_cancel():
                raise WinnerPredictionCaptureCancelled("winner prediction capture was cancelled")
            try:
                features = self.feature_extractor.extract(
                    run_context,
                    ticker_context,
                    config,
                    captured_at=captured_at,
                )
                totals.warnings += len(features.warnings)
                existing = self.repository.get_active_prediction(
                    db,
                    run_id=run_id,
                    ticker=features.ticker,
                    prediction_as_of_date=features.prediction_as_of_date,
                    feature_schema_version=config.feature_schema.version,
                )
                if existing is not None:
                    if existing.feature_vector_hash != features.feature_vector_hash:
                        raise WinnerPredictionCaptureConflict(
                            f"{features.ticker}: active prediction hash conflict"
                        )
                    totals.duplicate += 1
                    if existing.eligibility_status == PredictionEligibility.ELIGIBLE:
                        self._ensure_eligible_children(db, existing, config, totals)
                    continue

                prediction = self._build_prediction_snapshot(
                    run_id=run_id,
                    ticker_context=ticker_context,
                    features=features,
                    config=config,
                )
                assignment = self.episode_service.assign_episode(db, features, config)
                prediction.episode_id = assignment.episode.id
                prediction.lineage_json = {
                    **prediction.lineage_json,
                    "dependent_episode": assignment.is_dependent,
                }
                self.repository.add(db, prediction)
                if prediction.eligibility_status == PredictionEligibility.ELIGIBLE:
                    totals.inserted += 1
                    self._ensure_eligible_children(db, prediction, config, totals)
                else:
                    totals.excluded += 1
            except Exception:
                totals.failed += 1
        return totals.to_result()

    def _ensure_eligible_children(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        config: WinnerProbabilityConfig,
        totals: _MutableCaptureCounts,
    ) -> None:
        pending_result = self.pending_outcome_service.materialize_pending_outcomes(
            db,
            prediction,
            config,
        )
        totals.pending_outcomes += pending_result.forward_outcome_count
        totals.target_stop_outcomes += pending_result.target_stop_outcome_count
        primary_definition = self.repository.get_outcome_definition(
            db,
            definition_id=config.primary_outcome_definition.id,
            calculation_version=config.engine.calculation_version,
        )
        if primary_definition is None:
            return
        estimate_result = self.decision_time_estimate_service.create_decision_time_estimate(
            db,
            prediction=prediction,
            outcome_definition=primary_definition,
            config=config,
        )
        if estimate_result.status != "duplicate":
            totals.decision_time_estimates += 1
        if estimate_result.status == "insufficient":
            totals.insufficient_estimates += 1

    def _build_prediction_snapshot(
        self,
        *,
        run_id: int,
        ticker_context: TickerCaptureContext,
        features: ExtractedPredictionFeatures,
        config: WinnerProbabilityConfig,
    ) -> WinnerPredictionSnapshot:
        raw_row = ticker_context.raw_row
        technical = ticker_context.technical_score
        combined = ticker_context.combined_result
        fundamental = ticker_context.fundamental_score
        ranking = ticker_context.ranking_results[0] if ticker_context.ranking_results else None
        source_ids = features.source_ids_json
        return WinnerPredictionSnapshot(
            run_id=run_id,
            raw_row_id=source_ids.get("raw_row_id"),
            combined_result_id=source_ids.get("combined_result_id"),
            ranking_result_id=source_ids.get("ranking_result_id"),
            market_regime_snapshot_id=source_ids.get("market_regime_snapshot_id"),
            sector_rotation_snapshot_id=source_ids.get("sector_rotation_snapshot_id"),
            ticker=features.ticker,
            prediction_as_of_date=features.prediction_as_of_date,
            source_data_cutoff_at=features.source_data_cutoff_at,
            planned_entry_session=features.planned_entry_session,
            entry_schedule_status=features.entry_schedule_status,
            entry_data_status=features.entry_data_status,
            eligibility_status=features.eligibility_status,
            exclusion_reason=features.exclusion_reason,
            setup_family=features.feature_json.get("setup_family"),
            setup_classification=_first_present(
                getattr(combined, "technical_classification", None),
                getattr(technical, "classification", None),
            ),
            ranking_profile=getattr(ranking, "ranking_profile", None),
            fundamental_score=_decimal_or_none(
                _first_present(
                    getattr(fundamental, "fundamental_score", None),
                    getattr(combined, "fundamental_score", None),
                )
            ),
            technical_score=_decimal_or_none(getattr(technical, "dual_score", None)),
            combined_score=_decimal_or_none(getattr(combined, "final_score", None)),
            market_regime=features.feature_json.get("market_regime"),
            market_risk_state=features.feature_json.get("market_risk_state"),
            sector_state=features.feature_json.get("sector_state"),
            sector_rank=_int_or_none(features.feature_json.get("sector_rank")),
            suggested_target_pct=_decimal_or_none(getattr(technical, "suggested_target", None)),
            suggested_stop_pct=_decimal_or_none(getattr(technical, "suggested_stop", None)),
            reward_risk=_decimal_or_none(getattr(technical, "reward_risk", None)),
            upcoming_earnings_date=_first_present(
                getattr(combined, "upcoming_earnings_date", None),
                getattr(ranking, "upcoming_earnings_date", None),
                getattr(raw_row, "upcoming_earnings_date", None),
            ),
            days_until_earnings=_first_present(
                getattr(combined, "days_until_earnings", None),
                getattr(ranking, "days_until_earnings", None),
            ),
            earnings_risk_level=features.feature_json.get("earnings_risk"),
            technical_data_quality=features.feature_json.get("technical_data_quality"),
            fundamental_coverage=_decimal_or_none(
                getattr(fundamental, "data_coverage_score", None)
            ),
            universe_provenance=features.feature_json.get("universe_provenance"),
            screener_provenance=features.feature_json.get("screener_provenance"),
            feature_schema_version=config.feature_schema.version,
            feature_vector_hash=features.feature_vector_hash,
            config_hash=config.config_hash,
            calculation_version=config.engine.calculation_version,
            revision=1,
            feature_json=features.feature_json,
            source_ids_json=features.source_ids_json,
            warning_flags_json=list(features.warnings),
            lineage_json=features.lineage_json,
            retention_class="permanent",
        )


@dataclass
class _MutableCaptureCounts:
    inserted: int = 0
    duplicate: int = 0
    excluded: int = 0
    failed: int = 0
    warnings: int = 0
    pending_outcomes: int = 0
    target_stop_outcomes: int = 0
    decision_time_estimates: int = 0
    insufficient_estimates: int = 0

    def to_result(self) -> WinnerPredictionCaptureResult:
        return WinnerPredictionCaptureResult(**self.__dict__)


def _first_present(*values):
    return next((value for value in values if value is not None), None)


def _decimal_or_none(value) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _int_or_none(value) -> int | None:
    return int(value) if value is not None else None
