from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from inspect import Parameter, signature
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models.tables import (
    PredictionEligibility,
    WinnerPredictionSnapshot,
    WinnerTemporalValidityDecision,
)
from app.services.background_job_service import JobLeaseLost
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.contextual_calculation_identity import embed_identity
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.process_memory import WorkerMemoryCritical
from app.services.redaction import redact_sensitive
from app.services.us_market_calendar import us_market_session
from app.services.winner_probability.calculation_identity import (
    WINNER_HANDOFF_COMPATIBILITY,
    WinnerCalculationIdentityError,
    WinnerSourceAcquisition,
    acquire_winner_sources,
    build_winner_prediction_identity,
    validate_winner_handoff,
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
from app.services.winner_probability.market_data_obligation_service import (
    MarketDataObligationService,
)
from app.services.winner_probability.pending_outcome_service import PendingOutcomeService
from app.services.winner_probability.repository import (
    TickerCaptureContext,
    WinnerProbabilityRepository,
)
from app.services.winner_probability.temporal_eligibility import (
    prediction_temporally_eligible,
)
from app.services.winner_probability.temporal_integrity import validate_next_open_timing
from app.services.winner_probability.training_eligibility import TrainingEligibilityPolicy

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class WinnerPredictionCaptureConflict(ValueError):
    pass


class WinnerPredictionCaptureCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class WinnerPredictionCaptureResult:
    planned: int = 0
    attempted: int = 0
    inserted: int = 0
    duplicate: int = 0
    excluded: int = 0
    failed: int = 0
    warnings: int = 0
    pending_outcomes: int = 0
    target_stop_outcomes: int = 0
    decision_time_estimates: int = 0
    insufficient_estimates: int = 0
    failure_ratio: float = 0.0
    exclusion_reasons: dict[str, int] = field(default_factory=dict)
    failure_classifications: dict[str, int] = field(default_factory=dict)
    representative_failures: tuple[dict[str, str], ...] = ()
    performance: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
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
        training_eligibility_policy: TrainingEligibilityPolicy | None = None,
    ) -> None:
        self.repository = repository or WinnerProbabilityRepository()
        self.feature_extractor = feature_extractor or WinnerFeatureExtractor()
        self.episode_service = episode_service or WinnerEpisodeService(self.repository)
        self.pending_outcome_service = pending_outcome_service or PendingOutcomeService(
            self.repository,
            obligation_service=(MarketDataObligationService() if repository is None else None),
        )
        self.decision_time_estimate_service = (
            decision_time_estimate_service or DecisionTimeEstimateService(self.repository)
        )
        self.training_eligibility_policy = (
            training_eligibility_policy or TrainingEligibilityPolicy()
        )

    def capture_run(
        self,
        db: Session,
        *,
        run_id: int,
        config: WinnerProbabilityConfig | None = None,
        captured_at: datetime | None = None,
        decision_at: datetime | None = None,
        market_cutoff: MarketCalculationCutoff | None = None,
        decision_handoff_manifest_id: int | None = None,
        reconstruction_method: str | None = None,
        source_quality_flags: tuple[str, ...] = (),
        production_training_allowed: bool | None = None,
        should_cancel: Callable[[], bool] | None = None,
        lease_guard: Callable[[], None] | None = None,
        progress_callback: Callable[..., None] | None = None,
        memory_probe: Callable[[Session, int, int, str], None] | None = None,
    ) -> WinnerPredictionCaptureResult:
        config = config or load_winner_probability_config()
        identity_enforced = isinstance(db, Session)
        if identity_enforced and market_cutoff is None:
            raise WinnerCalculationIdentityError(
                "Winner capture requires an explicit frozen MarketCalculationContext"
            )
        if reconstruction_method is not None and market_cutoff is None:
            raise WinnerCalculationIdentityError(
                "historical Winner capture requires an explicit original decision identity"
            )
        if market_cutoff is not None:
            if decision_at is not None and _as_utc(decision_at) < market_cutoff.cutoff_at:
                raise WinnerCalculationIdentityError(
                    "Winner decision_at precedes the frozen market cutoff"
                )
        if identity_enforced:
            loader = self.repository.load_run_context
            parameters = signature(loader).parameters
            accepts_kwargs = any(item.kind is Parameter.VAR_KEYWORD for item in parameters.values())
            kwargs = {
                "market_cutoff": market_cutoff,
                "decision_handoff_manifest_id": decision_handoff_manifest_id,
            }
            run_context = loader(
                db,
                run_id,
                **{
                    name: value
                    for name, value in kwargs.items()
                    if accepts_kwargs or name in parameters
                },
            )
            validate_winner_handoff(
                run_context.decision_handoff_manifest,
                run_id=run_id,
                market_cutoff=market_cutoff,
            )
            if decision_at is None:
                decision_at = (
                    getattr(run_context.decision_handoff_manifest, "created_at", None)
                    or market_cutoff.cutoff_at
                )
        else:
            run_context = self.repository.load_run_context(db, run_id)
        ticker_contexts = list(run_context.tickers)
        total_tickers = len(ticker_contexts)
        totals = _MutableCaptureCounts(planned=total_tickers)
        primary_definition = self.repository.get_outcome_definition(
            db,
            definition_id=config.primary_outcome_definition.id,
            calculation_version=config.engine.calculation_version,
        )
        prepare_run = getattr(self.decision_time_estimate_service, "prepare_capture_run", None)
        metrics_reader = getattr(
            self.decision_time_estimate_service,
            "capture_run_evidence_metrics",
            None,
        )
        clear_run = getattr(self.decision_time_estimate_service, "clear_capture_run", None)
        item_session_factory = (
            sessionmaker(bind=db.get_bind(), expire_on_commit=False)
            if isinstance(db, Session)
            else None
        )
        capture_started = perf_counter()

        if progress_callback is not None:
            progress_callback(
                db,
                stage="CAPTURING_WINNER_PREDICTIONS",
                current_item=_ticker_name(ticker_contexts[0]) if ticker_contexts else None,
                last_completed_item=None,
                processed=0,
                total=total_tickers,
                checkpoint_version=f"winner-capture-v1:{run_id}:start",
            )
            _commit_if_supported(db)

        try:
            if primary_definition is not None and callable(prepare_run):
                prepare_run(
                    db,
                    outcome_definition=primary_definition,
                    config=config,
                    lease_guard=lease_guard,
                )

            for item_index, ticker_context in enumerate(ticker_contexts, start=1):
                ticker = _ticker_name(ticker_context)
                totals.attempted += 1
                _assert_capture_control(should_cancel=should_cancel, lease_guard=lease_guard)
                ticker_started = perf_counter()
                ticker_counts = _MutableCaptureCounts()
                try:
                    acquisition = (
                        acquire_winner_sources(
                            run_context,
                            ticker_context,
                            run_id=run_id,
                            market_cutoff=market_cutoff,
                            winner_config=config,
                        )
                        if identity_enforced
                        else None
                    )
                    if item_session_factory is None:
                        self._capture_ticker(
                            db,
                            run_id=run_id,
                            run_context=run_context,
                            ticker_context=ticker_context,
                            config=config,
                            primary_definition=primary_definition,
                            captured_at=captured_at,
                            decision_at=decision_at,
                            reconstruction_method=reconstruction_method,
                            source_quality_flags=source_quality_flags,
                            production_training_allowed=production_training_allowed,
                            acquisition=acquisition,
                            totals=ticker_counts,
                        )
                        if memory_probe is not None:
                            memory_probe(db, item_index, total_tickers, ticker)
                        _record_ticker_progress(
                            progress_callback,
                            db,
                            run_id=run_id,
                            ticker_contexts=ticker_contexts,
                            item_index=item_index,
                            ticker=ticker,
                        )
                    else:
                        with item_session_factory() as item_db:
                            self._capture_ticker(
                                item_db,
                                run_id=run_id,
                                run_context=run_context,
                                ticker_context=ticker_context,
                                config=config,
                                primary_definition=primary_definition,
                                captured_at=captured_at,
                                decision_at=decision_at,
                                reconstruction_method=reconstruction_method,
                                source_quality_flags=source_quality_flags,
                                production_training_allowed=production_training_allowed,
                                acquisition=acquisition,
                                totals=ticker_counts,
                            )
                            if memory_probe is not None:
                                memory_probe(item_db, item_index, total_tickers, ticker)
                            _record_ticker_progress(
                                progress_callback,
                                item_db,
                                run_id=run_id,
                                ticker_contexts=ticker_contexts,
                                item_index=item_index,
                                ticker=ticker,
                            )
                            item_db.commit()
                except (JobLeaseLost, WorkerMemoryCritical, WinnerPredictionCaptureCancelled):
                    raise
                except Exception as exc:
                    totals.record_failure(ticker, exc)
                    logger.exception(
                        "winner_prediction.capture_failed",
                        extra={"run_id": run_id, "ticker": ticker, "item_index": item_index},
                    )
                    if item_session_factory is not None:
                        with item_session_factory() as progress_db:
                            _record_ticker_progress(
                                progress_callback,
                                progress_db,
                                run_id=run_id,
                                ticker_contexts=ticker_contexts,
                                item_index=item_index,
                                ticker=ticker,
                            )
                            progress_db.commit()
                else:
                    totals.add(ticker_counts)
                    logger.info(
                        "winner_prediction.ticker_checkpoint",
                        extra={
                            "run_id": run_id,
                            "ticker": ticker,
                            "processed": item_index,
                            "total": total_tickers,
                            "elapsed_seconds": round(perf_counter() - ticker_started, 6),
                        },
                    )

            evidence_metrics = metrics_reader() if callable(metrics_reader) else {}
            return totals.to_result(
                performance={
                    "winner_capture_elapsed_seconds": round(
                        perf_counter() - capture_started,
                        6,
                    ),
                    "winner_capture_tickers_total": total_tickers,
                    **dict(evidence_metrics or {}),
                }
            )
        finally:
            if callable(clear_run):
                clear_run()

    def _capture_ticker(
        self,
        db: Session,
        *,
        run_id: int,
        run_context: Any,
        ticker_context: TickerCaptureContext,
        config: WinnerProbabilityConfig,
        primary_definition: Any,
        captured_at: datetime | None,
        decision_at: datetime | None,
        reconstruction_method: str | None,
        source_quality_flags: tuple[str, ...],
        production_training_allowed: bool | None,
        acquisition: WinnerSourceAcquisition | None,
        totals: _MutableCaptureCounts,
    ) -> None:
        if acquisition is not None:
            run_context = acquisition.run_context
            ticker_context = acquisition.ticker_context
        feature_as_of_at = decision_at or datetime.now(UTC)
        features = self.feature_extractor.extract(
            run_context,
            ticker_context,
            config,
            decision_at=feature_as_of_at,
        )
        ticker_decision_at = decision_at or datetime.now(UTC)
        features = self.feature_extractor.finalize_decision_timing(
            features,
            decision_at=ticker_decision_at,
        )
        winner_identity = (
            build_winner_prediction_identity(
                acquisition,
                config=config,
                feature_vector_hash=features.feature_vector_hash,
            )
            if acquisition is not None
            else None
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
            if winner_identity is not None:
                existing_identity = calculation_identity_from_debug(existing.lineage_json)
                if existing_identity is None:
                    raise WinnerPredictionCaptureConflict(
                        f"{features.ticker}: legacy prediction identity cannot be upgraded"
                    )
                if existing_identity.fingerprint() != winner_identity.fingerprint():
                    raise WinnerPredictionCaptureConflict(
                        f"{features.ticker}: active prediction identity conflict"
                    )
            if existing.feature_vector_hash != features.feature_vector_hash:
                raise WinnerPredictionCaptureConflict(
                    f"{features.ticker}: active prediction hash conflict"
                )
            totals.duplicate += 1
            temporal_decision = self.repository.get_current_temporal_decision(db, existing.id)
            if (
                existing.eligibility_status == PredictionEligibility.ELIGIBLE
                and prediction_temporally_eligible(existing, temporal_decision)
            ):
                self._ensure_eligible_children(
                    db,
                    existing,
                    config,
                    totals,
                    primary_definition=primary_definition,
                )
            return

        prediction = self._build_prediction_snapshot(
            run_id=run_id,
            ticker_context=ticker_context,
            features=features,
            config=config,
            reconstruction_method=reconstruction_method,
            source_quality_flags=source_quality_flags,
            production_training_allowed=production_training_allowed,
            decision_at=ticker_decision_at,
            captured_at=captured_at or datetime.now(UTC),
            winner_identity=winner_identity,
        )
        assignment = self.episode_service.assign_episode(db, features, config)
        prediction.episode_id = assignment.episode.id
        prediction.lineage_json = {
            **prediction.lineage_json,
            "dependent_episode": assignment.is_dependent,
        }
        self.training_eligibility_policy.persist_capture_decision(
            prediction,
            explicit_legacy_override=production_training_allowed,
        )
        temporal_decision = _initial_temporal_decision(
            prediction,
            semantic_input_time_valid=reconstruction_method is None,
        )
        self.repository.add(db, prediction)
        temporal_decision.prediction_id = prediction.id
        self.repository.add(db, temporal_decision)
        if prediction.eligibility_status == PredictionEligibility.ELIGIBLE:
            totals.inserted += 1
            self._ensure_eligible_children(
                db,
                prediction,
                config,
                totals,
                primary_definition=primary_definition,
            )
        else:
            totals.excluded += 1
            totals.record_exclusion(prediction.exclusion_reason)

    def _ensure_eligible_children(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        config: WinnerProbabilityConfig,
        totals: _MutableCaptureCounts,
        *,
        primary_definition: Any | None = None,
    ) -> None:
        pending_result = self.pending_outcome_service.materialize_pending_outcomes(
            db,
            prediction,
            config,
        )
        totals.pending_outcomes += pending_result.forward_outcome_count
        totals.target_stop_outcomes += pending_result.target_stop_outcome_count
        if primary_definition is None:
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
        reconstruction_method: str | None,
        source_quality_flags: tuple[str, ...],
        production_training_allowed: bool | None,
        decision_at: datetime,
        captured_at: datetime,
        winner_identity: Any | None,
    ) -> WinnerPredictionSnapshot:
        raw_row = ticker_context.raw_row
        technical = ticker_context.technical_score
        combined = ticker_context.combined_result
        fundamental = ticker_context.fundamental_score
        ranking = ticker_context.ranking_results[0] if ticker_context.ranking_results else None
        source_ids = features.source_ids_json
        prediction = WinnerPredictionSnapshot(
            run_id=run_id,
            raw_row_id=source_ids.get("raw_row_id"),
            combined_result_id=source_ids.get("combined_result_id"),
            ranking_result_id=source_ids.get("ranking_result_id"),
            market_regime_snapshot_id=source_ids.get("market_regime_snapshot_id"),
            sector_rotation_snapshot_id=source_ids.get("sector_rotation_snapshot_id"),
            ticker=features.ticker,
            prediction_as_of_date=features.prediction_as_of_date,
            source_data_cutoff_at=features.source_data_cutoff_at,
            decision_at=decision_at,
            captured_at=captured_at,
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
            lineage_json={
                **features.lineage_json,
                "source_quality_flags": list(source_quality_flags),
                "production_training_allowed_override": production_training_allowed,
            },
            reconstruction_method=reconstruction_method,
            retention_class="permanent",
        )
        if winner_identity is not None:
            prediction.lineage_json = embed_identity(
                prediction.lineage_json,
                winner_identity,
                policy=WINNER_HANDOFF_COMPATIBILITY.name,
            )
        return prediction


@dataclass
class _MutableCaptureCounts:
    planned: int = 0
    attempted: int = 0
    inserted: int = 0
    duplicate: int = 0
    excluded: int = 0
    failed: int = 0
    warnings: int = 0
    pending_outcomes: int = 0
    target_stop_outcomes: int = 0
    decision_time_estimates: int = 0
    insufficient_estimates: int = 0
    exclusion_reasons: dict[str, int] = field(default_factory=dict)
    failure_classifications: dict[str, int] = field(default_factory=dict)
    representative_failures: list[dict[str, str]] = field(default_factory=list)

    def add(self, other: _MutableCaptureCounts) -> None:
        for name in (
            "inserted",
            "duplicate",
            "excluded",
            "failed",
            "warnings",
            "pending_outcomes",
            "target_stop_outcomes",
            "decision_time_estimates",
            "insufficient_estimates",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for reason, count in other.exclusion_reasons.items():
            self.exclusion_reasons[reason] = self.exclusion_reasons.get(reason, 0) + count
        for classification, count in other.failure_classifications.items():
            self.failure_classifications[classification] = (
                self.failure_classifications.get(classification, 0) + count
            )
        remaining = max(0, 5 - len(self.representative_failures))
        self.representative_failures.extend(other.representative_failures[:remaining])

    def record_exclusion(self, reason: str | None) -> None:
        normalized = str(reason or "unspecified").strip() or "unspecified"
        self.exclusion_reasons[normalized] = self.exclusion_reasons.get(normalized, 0) + 1

    def record_failure(self, ticker: str, exc: Exception) -> None:
        self.failed += 1
        classification = type(exc).__name__
        self.failure_classifications[classification] = (
            self.failure_classifications.get(classification, 0) + 1
        )
        if len(self.representative_failures) < 5:
            self.representative_failures.append(
                {
                    "ticker": ticker,
                    "classification": classification,
                    "message": str(redact_sensitive(str(exc))).replace("\n", " ").strip()[:300],
                }
            )

    def to_result(
        self,
        *,
        performance: dict[str, Any] | None = None,
    ) -> WinnerPredictionCaptureResult:
        return WinnerPredictionCaptureResult(
            planned=self.planned,
            attempted=self.attempted,
            inserted=self.inserted,
            duplicate=self.duplicate,
            excluded=self.excluded,
            failed=self.failed,
            warnings=self.warnings,
            pending_outcomes=self.pending_outcomes,
            target_stop_outcomes=self.target_stop_outcomes,
            decision_time_estimates=self.decision_time_estimates,
            insufficient_estimates=self.insufficient_estimates,
            failure_ratio=(self.failed / self.attempted if self.attempted else 0.0),
            exclusion_reasons=dict(sorted(self.exclusion_reasons.items())),
            failure_classifications=dict(sorted(self.failure_classifications.items())),
            representative_failures=tuple(self.representative_failures),
            performance=dict(performance or {}),
        )


def _ticker_name(ticker_context: TickerCaptureContext) -> str:
    return str(getattr(ticker_context.raw_row, "ticker", "") or "").strip().upper()


def _assert_capture_control(
    *,
    should_cancel: Callable[[], bool] | None,
    lease_guard: Callable[[], None] | None,
) -> None:
    if should_cancel is not None:
        if should_cancel():
            raise WinnerPredictionCaptureCancelled("winner prediction capture was cancelled")
        return
    if lease_guard is not None:
        lease_guard()


def _record_ticker_progress(
    progress_callback: Callable[..., None] | None,
    db: Session,
    *,
    run_id: int,
    ticker_contexts: list[TickerCaptureContext],
    item_index: int,
    ticker: str,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        db,
        stage="CAPTURING_WINNER_PREDICTIONS",
        current_item=(
            _ticker_name(ticker_contexts[item_index]) if item_index < len(ticker_contexts) else None
        ),
        last_completed_item=ticker,
        processed=item_index,
        total=len(ticker_contexts),
        checkpoint_version=f"winner-capture-v1:{run_id}:{ticker}",
    )


def _commit_if_supported(db: Any) -> None:
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()


def _first_present(*values):
    return next((value for value in values if value is not None), None)


def _decimal_or_none(value) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _int_or_none(value) -> int | None:
    return int(value) if value is not None else None


def _initial_temporal_decision(
    prediction: WinnerPredictionSnapshot,
    *,
    semantic_input_time_valid: bool,
) -> WinnerTemporalValidityDecision:
    session = us_market_session(prediction.planned_entry_session)
    if prediction.decision_at is None or session is None:
        raise WinnerPredictionCaptureConflict("NEXT_OPEN entry session could not be certified")
    result = validate_next_open_timing(
        prediction.decision_at,
        session.open_at,
        source_data_cutoff_at=prediction.source_data_cutoff_at,
        semantic_input_time_valid=semantic_input_time_valid,
    )
    if not result.entry_timing_valid:
        raise WinnerPredictionCaptureConflict("NEXT_OPEN entry is not strictly after decision")
    if not semantic_input_time_valid:
        prediction.lineage_json = {
            **(prediction.lineage_json or {}),
            "point_in_time_validation": {
                **(prediction.lineage_json or {}).get("point_in_time_validation", {}),
                "semantic_input_time": "UNRESOLVED",
            },
        }
    return WinnerTemporalValidityDecision(
        prediction_id=prediction.id,
        validation_sequence=1,
        status=result.status,
        entry_timing_valid=result.entry_timing_valid,
        source_cutoff_valid=result.source_cutoff_valid,
        semantic_input_time_valid=result.semantic_input_time_valid,
        evidence_eligible=result.evidence_eligible,
        reason_codes_json=list(result.reason_codes),
        validation_version=result.validation_version,
        decision_at=prediction.decision_at,
        entry_session=prediction.planned_entry_session,
        entry_open_at=session.open_at,
        evaluated_at=prediction.captured_at,
        evaluated_by="WINNER_CAPTURE",
        metadata_json={"capture_revision": prediction.revision},
    )
