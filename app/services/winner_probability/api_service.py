from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    EstimateKind,
    WinnerCalibrationBin,
    WinnerDriftMetric,
    WinnerEstimateEvidenceMember,
    WinnerForwardOutcome,
    WinnerModelLifecycleEvent,
    WinnerModelVersion,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerSimilarityLink,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.dtos import (
    WinnerProbabilityApiQuery,
    WinnerProbabilityFilters,
)

ERROR_INVALID_OUTCOME_DEFINITION = "INVALID_OUTCOME_DEFINITION"
ERROR_PREDICTION_NOT_FOUND = "PREDICTION_NOT_FOUND"
ERROR_ESTIMATE_NOT_FOUND = "ESTIMATE_NOT_FOUND"
ERROR_INVALID_AS_OF_CUTOFF = "INVALID_AS_OF_CUTOFF"
ERROR_MODEL_RETIREMENT_BLOCKED = "MODEL_RETIREMENT_BLOCKED"

RUN_SORT_FIELDS = {
    "ticker",
    "probability",
    "lower_bound",
    "interval_width",
    "expected_return",
    "median_return",
    "median_mfe",
    "median_mae",
    "target_first_rate",
    "sample_n",
    "effective_n",
    "evidence_grade",
}


class WinnerProbabilityApiError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class PaginatedPayload:
    items: list[dict[str, Any]]
    next_cursor: str | None


class WinnerProbabilityApiService:
    def get_run_evidence(
        self,
        db: Session,
        *,
        run_id: int,
        query: WinnerProbabilityApiQuery,
    ) -> dict[str, Any]:
        query = _bounded_query(query)
        outcome_definition = self._resolve_outcome_definition(db, query)
        _validate_as_of(query)
        predictions = list(
            db.scalars(
                select(WinnerPredictionSnapshot)
                .where(WinnerPredictionSnapshot.run_id == run_id)
                .where(_prediction_visible_clause(query))
                .order_by(WinnerPredictionSnapshot.ticker.asc(), WinnerPredictionSnapshot.id.asc())
            )
        )
        rows = [
            self._run_row(db, prediction, outcome_definition=outcome_definition, query=query)
            for prediction in predictions
        ]
        rows = [
            row
            for row in rows
            if _matches_filters(row["prediction"], row["estimate"], query.filters)
        ]
        rows = _sort_rows(rows, query.sort, query.direction)
        page = _page_rows(rows, query.cursor, query.page_size)
        return {
            "run_id": run_id,
            "outcome_definition": _outcome_definition_payload(outcome_definition),
            "estimate_view": query.estimate_view,
            "outcome_revision_view": query.outcome_revision_view,
            "as_of_date": query.as_of_date.isoformat() if query.as_of_date else None,
            "training_cutoff": query.training_cutoff.isoformat() if query.training_cutoff else None,
            "horizon_convention": "ENTRY_SESSION_IS_SESSION_1",
            "schema_version": _first_non_null(rows, "schema_version"),
            "config_hash": _first_non_null(rows, "config_hash"),
            "items": page.items,
            "next_cursor": page.next_cursor,
        }

    def get_prediction_detail(
        self,
        db: Session,
        *,
        prediction_id: int,
        query: WinnerProbabilityApiQuery,
    ) -> dict[str, Any]:
        query = _bounded_query(query)
        prediction = db.get(WinnerPredictionSnapshot, prediction_id)
        if prediction is None or not _prediction_visible(prediction, query):
            raise WinnerProbabilityApiError(
                ERROR_PREDICTION_NOT_FOUND,
                f"Prediction {prediction_id} was not found.",
                status_code=404,
            )
        outcome_definition = self._resolve_outcome_definition(db, query, prediction=prediction)
        estimate = self._selected_estimate(
            db,
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            query=query,
        )
        return {
            "prediction": _prediction_payload(prediction),
            "outcome_definition": _outcome_definition_payload(outcome_definition),
            "decision_time_estimate": _estimate_payload(
                self._estimate_by_kind(
                    db,
                    prediction_id=prediction.id,
                    outcome_definition_id=outcome_definition.id,
                    estimate_kind=EstimateKind.DECISION_TIME,
                    query=query,
                )
            ),
            "latest_rescore": _estimate_payload(
                self._estimate_by_kind(
                    db,
                    prediction_id=prediction.id,
                    outcome_definition_id=outcome_definition.id,
                    estimate_kind=EstimateKind.LATEST_RESCORE,
                    query=query,
                )
            ),
            "selected_estimate": _estimate_payload(estimate),
            "forward_outcomes": [
                _forward_outcome_payload(row)
                for row in self._forward_outcomes(db, prediction.id, query=query)
            ],
            "target_stop_outcomes": [
                _target_stop_payload(row)
                for row in self._target_stop_outcomes(
                    db,
                    prediction.id,
                    outcome_definition_id=outcome_definition.id,
                    query=query,
                )
            ],
            "evidence_members": [
                _evidence_member_payload(row)
                for row in self._evidence_members(db, estimate.id if estimate else None)
            ],
            "warnings": prediction.warning_flags_json or [],
            "exclusions": [prediction.exclusion_reason] if prediction.exclusion_reason else [],
        }

    def get_neighbors(
        self,
        db: Session,
        *,
        prediction_id: int,
        query: WinnerProbabilityApiQuery,
        limit: int = 25,
    ) -> dict[str, Any]:
        query = _bounded_query(query)
        if limit <= 0 or limit > 100:
            raise WinnerProbabilityApiError("INVALID_NEIGHBOR_LIMIT", "limit must be 1..100")
        prediction = db.get(WinnerPredictionSnapshot, prediction_id)
        if prediction is None:
            raise WinnerProbabilityApiError(
                ERROR_PREDICTION_NOT_FOUND,
                f"Prediction {prediction_id} was not found.",
                status_code=404,
            )
        outcome_definition = self._resolve_outcome_definition(db, query, prediction=prediction)
        rows = list(
            db.scalars(
                select(WinnerSimilarityLink)
                .where(WinnerSimilarityLink.prediction_id == prediction_id)
                .where(WinnerSimilarityLink.outcome_definition_id == outcome_definition.id)
                .where(_similarity_visible_clause(query))
                .order_by(WinnerSimilarityLink.rank.asc())
                .limit(limit)
            )
        )
        return {
            "prediction_id": prediction_id,
            "outcome_definition": _outcome_definition_payload(outcome_definition),
            "neighbors": [_neighbor_payload(row) for row in rows],
        }

    def get_ticker_history(
        self,
        db: Session,
        *,
        ticker: str,
        query: WinnerProbabilityApiQuery,
    ) -> dict[str, Any]:
        query = _bounded_query(query)
        normalized = ticker.strip().upper()
        outcome_definition = self._resolve_outcome_definition(db, query)
        predictions = list(
            db.scalars(
                select(WinnerPredictionSnapshot)
                .where(WinnerPredictionSnapshot.ticker == normalized)
                .where(_prediction_visible_clause(query))
                .order_by(
                    WinnerPredictionSnapshot.prediction_as_of_date.desc(),
                    WinnerPredictionSnapshot.id.desc(),
                )
                .limit(query.page_size + 1)
            )
        )
        rows = [
            self._run_row(db, prediction, outcome_definition=outcome_definition, query=query)
            for prediction in predictions
        ]
        page = PaginatedPayload(
            items=rows[: query.page_size],
            next_cursor=str(rows[query.page_size]["prediction"]["id"])
            if len(rows) > query.page_size
            else None,
        )
        return {
            "ticker": normalized,
            "outcome_definition": _outcome_definition_payload(outcome_definition),
            "items": page.items,
            "next_cursor": page.next_cursor,
        }

    def list_models(self, db: Session) -> dict[str, Any]:
        models = list(
            db.scalars(select(WinnerModelVersion).order_by(WinnerModelVersion.created_at.desc()))
        )
        return {"models": [_model_payload(row) for row in models]}

    def get_model_calibration(self, db: Session, *, model_id: int) -> dict[str, Any]:
        model = _require_model(db, model_id)
        bins = list(
            db.scalars(
                select(WinnerCalibrationBin)
                .where(WinnerCalibrationBin.model_version_id == model_id)
                .order_by(WinnerCalibrationBin.bin_floor.asc())
            )
        )
        return {
            "model": _model_payload(model),
            "bins": [_calibration_bin_payload(row) for row in bins],
        }

    def get_model_drift(self, db: Session, *, model_id: int) -> dict[str, Any]:
        model = _require_model(db, model_id)
        rows = list(
            db.scalars(
                select(WinnerDriftMetric)
                .where(WinnerDriftMetric.model_version_id == model_id)
                .order_by(WinnerDriftMetric.as_of_date.desc(), WinnerDriftMetric.metric_name.asc())
            )
        )
        return {"model": _model_payload(model), "metrics": [_drift_payload(row) for row in rows]}

    def retire_model(
        self,
        db: Session,
        *,
        model_id: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        model = _require_model(db, model_id)
        old_status = model.status
        if model.status == "ACTIVE":
            active_count = db.scalar(
                select(func.count(WinnerModelVersion.id))
                .where(WinnerModelVersion.outcome_definition_id == model.outcome_definition_id)
                .where(WinnerModelVersion.status == "ACTIVE")
            )
            if int(active_count or 0) <= 1:
                raise WinnerProbabilityApiError(
                    ERROR_MODEL_RETIREMENT_BLOCKED,
                    "Cannot retire the only active model for this outcome definition.",
                    status_code=409,
                )
        model.status = "RETIRED"
        model.retired_at = _utcnow()
        event = WinnerModelLifecycleEvent(
            model_version_id=model.id,
            event_type="RETIRED",
            actor=actor,
            reason=reason.strip() or "Retired through local OWPE API.",
            old_status=old_status,
            new_status="RETIRED",
            metadata_json={"api": "winner_probability_phase_7"},
        )
        db.add(event)
        db.flush()
        return {"model": _model_payload(model), "lifecycle_event_id": event.id}

    def _resolve_outcome_definition(
        self,
        db: Session,
        query: WinnerProbabilityApiQuery,
        *,
        prediction: WinnerPredictionSnapshot | None = None,
    ) -> WinnerOutcomeDefinition:
        statement = select(WinnerOutcomeDefinition)
        if query.outcome_definition_id:
            statement = statement.where(
                WinnerOutcomeDefinition.definition_id == query.outcome_definition_id
            )
        elif prediction is not None:
            statement = statement.where(WinnerOutcomeDefinition.is_primary.is_(True))
        else:
            statement = statement.where(WinnerOutcomeDefinition.is_primary.is_(True))
        if query.entry_model:
            statement = statement.where(WinnerOutcomeDefinition.entry_model == query.entry_model)
        if query.horizon_sessions:
            statement = statement.where(
                WinnerOutcomeDefinition.horizon_sessions == query.horizon_sessions
            )
        outcome_definition = db.scalar(
            statement.order_by(WinnerOutcomeDefinition.id.asc()).limit(1)
        )
        if outcome_definition is None:
            raise WinnerProbabilityApiError(
                ERROR_INVALID_OUTCOME_DEFINITION,
                "Outcome definition could not be resolved.",
                status_code=404,
            )
        return outcome_definition

    def _run_row(
        self,
        db: Session,
        prediction: WinnerPredictionSnapshot,
        *,
        outcome_definition: WinnerOutcomeDefinition,
        query: WinnerProbabilityApiQuery,
    ) -> dict[str, Any]:
        estimate = self._selected_estimate(
            db,
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            query=query,
        )
        return {
            "prediction": _prediction_payload(prediction),
            "estimate": _estimate_payload(estimate),
            "outcome_definition": _outcome_definition_payload(outcome_definition),
            "schema_version": prediction.feature_schema_version,
            "config_hash": prediction.config_hash,
        }

    def _selected_estimate(
        self,
        db: Session,
        *,
        prediction_id: int,
        outcome_definition_id: int,
        query: WinnerProbabilityApiQuery,
    ) -> WinnerProbabilityEstimate | None:
        estimate_kind = (
            EstimateKind.LATEST_RESCORE
            if query.estimate_view in {"LATEST", EstimateKind.LATEST_RESCORE}
            else EstimateKind.DECISION_TIME
        )
        return self._estimate_by_kind(
            db,
            prediction_id=prediction_id,
            outcome_definition_id=outcome_definition_id,
            estimate_kind=estimate_kind,
            query=query,
        )

    def _estimate_by_kind(
        self,
        db: Session,
        *,
        prediction_id: int,
        outcome_definition_id: int,
        estimate_kind: str,
        query: WinnerProbabilityApiQuery,
    ) -> WinnerProbabilityEstimate | None:
        statement = (
            select(WinnerProbabilityEstimate)
            .where(WinnerProbabilityEstimate.prediction_id == prediction_id)
            .where(WinnerProbabilityEstimate.outcome_definition_id == outcome_definition_id)
            .where(WinnerProbabilityEstimate.estimate_kind == estimate_kind)
        )
        cutoff = _cutoff_from_query(query)
        if cutoff is not None:
            statement = statement.where(WinnerProbabilityEstimate.created_at <= cutoff)
            statement = statement.where(WinnerProbabilityEstimate.training_cutoff_at <= cutoff)
        if query.training_cutoff is not None:
            statement = statement.where(
                WinnerProbabilityEstimate.training_cutoff_at <= _end_of_day(query.training_cutoff)
            )
        return db.scalar(
            statement.order_by(
                WinnerProbabilityEstimate.training_cutoff_at.desc(),
                WinnerProbabilityEstimate.created_at.desc(),
                WinnerProbabilityEstimate.id.desc(),
            ).limit(1)
        )

    def _forward_outcomes(
        self,
        db: Session,
        prediction_id: int,
        *,
        query: WinnerProbabilityApiQuery,
    ) -> list[WinnerForwardOutcome]:
        statement = select(WinnerForwardOutcome).where(
            WinnerForwardOutcome.prediction_id == prediction_id
        )
        if query.entry_model:
            statement = statement.where(WinnerForwardOutcome.entry_model == query.entry_model)
        if query.horizon_sessions:
            statement = statement.where(
                WinnerForwardOutcome.horizon_sessions == query.horizon_sessions
            )
        statement = _apply_revision_view(statement, WinnerForwardOutcome, query)
        return list(db.scalars(statement.order_by(WinnerForwardOutcome.horizon_sessions.asc())))

    def _target_stop_outcomes(
        self,
        db: Session,
        prediction_id: int,
        *,
        outcome_definition_id: int,
        query: WinnerProbabilityApiQuery,
    ) -> list[WinnerTargetStopOutcome]:
        statement = (
            select(WinnerTargetStopOutcome)
            .where(WinnerTargetStopOutcome.prediction_id == prediction_id)
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition_id)
        )
        statement = _apply_revision_view(statement, WinnerTargetStopOutcome, query)
        return list(db.scalars(statement.order_by(WinnerTargetStopOutcome.revision.desc())))

    def _evidence_members(
        self,
        db: Session,
        estimate_id: int | None,
    ) -> list[WinnerEstimateEvidenceMember]:
        if estimate_id is None:
            return []
        return list(
            db.scalars(
                select(WinnerEstimateEvidenceMember)
                .where(WinnerEstimateEvidenceMember.estimate_id == estimate_id)
                .order_by(WinnerEstimateEvidenceMember.id.asc())
                .limit(500)
            )
        )


def _require_model(db: Session, model_id: int) -> WinnerModelVersion:
    model = db.get(WinnerModelVersion, model_id)
    if model is None:
        raise WinnerProbabilityApiError(
            "MODEL_NOT_FOUND",
            f"Model {model_id} was not found.",
            status_code=404,
        )
    return model


def _bounded_query(query: WinnerProbabilityApiQuery) -> WinnerProbabilityApiQuery:
    config = load_winner_probability_config()
    if query.page_size > config.api.max_page_size:
        raise WinnerProbabilityApiError(
            "PAGE_SIZE_TOO_LARGE",
            f"page_size must be <= {config.api.max_page_size}.",
        )
    if query.sort not in RUN_SORT_FIELDS:
        raise WinnerProbabilityApiError("INVALID_SORT", f"Unsupported sort field: {query.sort}.")
    return query


def _validate_as_of(query: WinnerProbabilityApiQuery) -> None:
    if query.as_of_date is not None and query.as_of_date > date.today():
        raise WinnerProbabilityApiError(
            ERROR_INVALID_AS_OF_CUTOFF,
            "as_of_date cannot be in the future.",
        )


def _prediction_visible_clause(query: WinnerProbabilityApiQuery):
    cutoff = _cutoff_from_query(query)
    if cutoff is None:
        return WinnerPredictionSnapshot.superseded_at.is_(None)
    return and_(
        WinnerPredictionSnapshot.source_data_cutoff_at <= cutoff,
        WinnerPredictionSnapshot.captured_at <= cutoff,
        (
            WinnerPredictionSnapshot.superseded_at.is_(None)
            | (WinnerPredictionSnapshot.superseded_at > cutoff)
        ),
    )


def _prediction_visible(
    prediction: WinnerPredictionSnapshot,
    query: WinnerProbabilityApiQuery,
) -> bool:
    cutoff = _cutoff_from_query(query)
    if cutoff is None:
        return prediction.superseded_at is None
    return (
        prediction.source_data_cutoff_at <= cutoff
        and prediction.captured_at <= cutoff
        and (prediction.superseded_at is None or prediction.superseded_at > cutoff)
    )


def _similarity_visible_clause(query: WinnerProbabilityApiQuery):
    cutoff = _cutoff_from_query(query)
    if cutoff is None:
        return True
    return WinnerSimilarityLink.source_cutoff_at <= cutoff


def _apply_revision_view(statement, model, query: WinnerProbabilityApiQuery):
    cutoff = _cutoff_from_query(query)
    if query.outcome_revision_view == "CURRENT" and cutoff is None:
        return statement.where(model.is_current_revision.is_(True))
    if cutoff is not None:
        timestamp_column = (
            model.matured_at if hasattr(model, "matured_at") else model.evaluated_at
        )
        statement = statement.where((timestamp_column <= cutoff) | (model.status == "PENDING"))
        statement = statement.where(
            model.superseded_at.is_(None) | (model.superseded_at > cutoff)
        )
    return statement


def _matches_filters(
    prediction: dict[str, Any],
    estimate: dict[str, Any] | None,
    filters: WinnerProbabilityFilters,
) -> bool:
    estimate = estimate or {}
    return all(
        (
            _min(estimate.get("point_probability"), filters.probability_min),
            _max(estimate.get("point_probability"), filters.probability_max),
            _min(estimate.get("lower_bound"), filters.lower_bound_min),
            _max(estimate.get("lower_bound"), filters.lower_bound_max),
            _min(estimate.get("interval_width"), filters.interval_width_min),
            _max(estimate.get("interval_width"), filters.interval_width_max),
            _min(estimate.get("expected_return_pct"), filters.expected_return_min),
            _max(estimate.get("expected_return_pct"), filters.expected_return_max),
            _min(estimate.get("median_return_pct"), filters.median_return_min),
            _max(estimate.get("median_return_pct"), filters.median_return_max),
            _min(estimate.get("median_mfe_pct"), filters.mfe_min),
            _max(estimate.get("median_mfe_pct"), filters.mfe_max),
            _min(estimate.get("median_mae_pct"), filters.mae_min),
            _max(estimate.get("median_mae_pct"), filters.mae_max),
            _min(estimate.get("target_first_rate"), filters.target_first_rate_min),
            _max(estimate.get("target_first_rate"), filters.target_first_rate_max),
            _min(estimate.get("effective_n"), filters.effective_sample_size_min),
            _min(estimate.get("sample_n"), filters.sample_size_min),
            _eq(estimate.get("evidence_grade"), filters.evidence_grade),
            _eq(prediction.get("earnings_risk_level"), filters.earnings_risk),
            _eq(prediction.get("technical_data_quality"), filters.data_quality),
            _eq(prediction.get("setup_classification"), filters.setup_classification),
            _eq(prediction.get("setup_family"), filters.setup_family),
            _eq(prediction.get("ranking_profile"), filters.ranking_profile),
            _eq(prediction.get("market_regime"), filters.market_regime),
            _eq(prediction.get("market_risk_state"), filters.market_risk_state),
            _eq(prediction.get("sector_state"), filters.sector_state),
            _eq(prediction.get("eligibility_status"), filters.eligibility_status),
            _min(prediction.get("sector_rank"), filters.sector_rank_min),
            _max(prediction.get("sector_rank"), filters.sector_rank_max),
        )
    )


def _sort_rows(rows: list[dict[str, Any]], sort: str, direction: str) -> list[dict[str, Any]]:
    reverse = direction == "desc"

    def key(row: dict[str, Any]) -> tuple[Any, str]:
        prediction = row["prediction"]
        estimate = row.get("estimate") or {}
        value = {
            "ticker": prediction.get("ticker"),
            "probability": estimate.get("point_probability"),
            "lower_bound": estimate.get("lower_bound"),
            "interval_width": estimate.get("interval_width"),
            "expected_return": estimate.get("expected_return_pct"),
            "median_return": estimate.get("median_return_pct"),
            "median_mfe": estimate.get("median_mfe_pct"),
            "median_mae": estimate.get("median_mae_pct"),
            "target_first_rate": estimate.get("target_first_rate"),
            "sample_n": estimate.get("sample_n"),
            "effective_n": estimate.get("effective_n"),
            "evidence_grade": estimate.get("evidence_grade"),
        }[sort]
        return (_sort_value(value, reverse), prediction.get("ticker") or "")

    return sorted(rows, key=key, reverse=False)


def _page_rows(
    rows: list[dict[str, Any]],
    cursor: str | None,
    page_size: int,
) -> PaginatedPayload:
    start = 0
    if cursor:
        for index, row in enumerate(rows):
            if str(row["prediction"]["id"]) == cursor:
                start = index + 1
                break
    page = rows[start : start + page_size]
    next_index = start + page_size
    next_cursor = str(page[-1]["prediction"]["id"]) if next_index < len(rows) and page else None
    return PaginatedPayload(items=page, next_cursor=next_cursor)


def _prediction_payload(row: WinnerPredictionSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "ticker": row.ticker,
        "prediction_as_of_date": _iso(row.prediction_as_of_date),
        "source_data_cutoff_at": _iso(row.source_data_cutoff_at),
        "captured_at": _iso(row.captured_at),
        "planned_entry_session": _iso(row.planned_entry_session),
        "entry_schedule_status": row.entry_schedule_status,
        "entry_data_status": row.entry_data_status,
        "eligibility_status": row.eligibility_status,
        "exclusion_reason": row.exclusion_reason,
        "setup_family": row.setup_family,
        "setup_classification": row.setup_classification,
        "ranking_profile": row.ranking_profile,
        "fundamental_score": _number(row.fundamental_score),
        "technical_score": _number(row.technical_score),
        "combined_score": _number(row.combined_score),
        "market_regime": row.market_regime,
        "market_risk_state": row.market_risk_state,
        "sector_state": row.sector_state,
        "sector_rank": row.sector_rank,
        "earnings_risk_level": row.earnings_risk_level,
        "technical_data_quality": row.technical_data_quality,
        "fundamental_coverage": _number(row.fundamental_coverage),
        "feature_schema_version": row.feature_schema_version,
        "feature_vector_hash": row.feature_vector_hash,
        "config_hash": row.config_hash,
        "calculation_version": row.calculation_version,
        "feature_json": row.feature_json,
        "source_ids": row.source_ids_json or {},
        "lineage": row.lineage_json or {},
    }


def _estimate_payload(row: WinnerProbabilityEstimate | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "prediction_id": row.prediction_id,
        "outcome_definition_id": row.outcome_definition_id,
        "estimate_kind": row.estimate_kind,
        "source": row.source,
        "source_version": row.source_version,
        "cohort_definition_id": row.cohort_definition_id,
        "model_version_id": row.model_version_id,
        "training_cutoff_at": _iso(row.training_cutoff_at),
        "created_at": _iso(row.created_at),
        "point_probability": _number(row.point_probability),
        "lower_bound": _number(row.lower_bound),
        "upper_bound": _number(row.upper_bound),
        "interval_width": _number(row.interval_width),
        "sample_n": row.sample_n,
        "effective_n": _number(row.effective_n),
        "evidence_grade": row.evidence_grade,
        "insufficient_reasons": row.insufficient_reasons_json or [],
        "expected_return_pct": _number(row.expected_return_pct),
        "median_return_pct": _number(row.median_return_pct),
        "median_mfe_pct": _number(row.median_mfe_pct),
        "median_mae_pct": _number(row.median_mae_pct),
        "target_first_rate": _number(row.target_first_rate),
        "config_hash": row.config_hash,
        "feature_schema_version": row.feature_schema_version,
        "evidence_manifest_hash": row.evidence_manifest_hash,
        "metadata": row.metadata_json or {},
    }


def _outcome_definition_payload(row: WinnerOutcomeDefinition) -> dict[str, Any]:
    return {
        "id": row.id,
        "definition_id": row.definition_id,
        "label": row.label,
        "entry_model": row.entry_model,
        "horizon_sessions": row.horizon_sessions,
        "target_pct": _number(row.target_pct),
        "stop_pct": _number(row.stop_pct),
        "same_bar_conflict_policy": row.same_bar_conflict_policy,
        "calculation_version": row.calculation_version,
        "config_hash": row.config_hash,
        "is_primary": row.is_primary,
    }


def _forward_outcome_payload(row: WinnerForwardOutcome) -> dict[str, Any]:
    return {
        "id": row.id,
        "entry_model": row.entry_model,
        "horizon_sessions": row.horizon_sessions,
        "entry_session": _iso(row.entry_session),
        "due_session": _iso(row.due_session),
        "status": row.status,
        "revision": row.revision,
        "is_current_revision": row.is_current_revision,
        "close_return_pct": _number(row.close_return_pct),
        "mfe_pct": _number(row.mfe_pct),
        "mae_pct": _number(row.mae_pct),
        "positive_return": row.positive_return,
        "matured_at": _iso(row.matured_at),
        "source_revision_cutoff_at": _iso(row.source_revision_cutoff_at),
    }


def _target_stop_payload(row: WinnerTargetStopOutcome) -> dict[str, Any]:
    return {
        "id": row.id,
        "outcome_definition_id": row.outcome_definition_id,
        "forward_outcome_id": row.forward_outcome_id,
        "entry_model": row.entry_model,
        "horizon_sessions": row.horizon_sessions,
        "status": row.status,
        "revision": row.revision,
        "is_current_revision": row.is_current_revision,
        "target_hit": row.target_hit,
        "stop_hit": row.stop_hit,
        "first_event": row.first_event,
        "same_bar_conflict": row.same_bar_conflict,
        "primary_winner": row.primary_winner,
        "evaluated_at": _iso(row.evaluated_at),
    }


def _evidence_member_payload(row: WinnerEstimateEvidenceMember) -> dict[str, Any]:
    return {
        "id": row.id,
        "estimate_id": row.estimate_id,
        "prediction_id": row.prediction_id,
        "outcome_id": row.outcome_id,
        "outcome_revision": row.outcome_revision,
        "episode_id": row.episode_id,
        "inclusion_weight": _number(row.inclusion_weight),
        "included_as_of": _iso(row.included_as_of),
        "inclusion_cutoff_at": _iso(row.inclusion_cutoff_at),
        "metadata": row.metadata_json or {},
    }


def _neighbor_payload(row: WinnerSimilarityLink) -> dict[str, Any]:
    return {
        "rank": row.rank,
        "neighbor_prediction_id": row.neighbor_prediction_id,
        "outcome_id": row.outcome_id,
        "outcome_revision": row.outcome_revision,
        "distance": _number(row.distance),
        "similarity_coverage": _number(row.similarity_coverage),
        "contributions": row.contribution_json or {},
        "cache_version": row.cache_version,
        "source_cutoff_at": _iso(row.source_cutoff_at),
    }


def _model_payload(row: WinnerModelVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "model_key": row.model_key,
        "algorithm": row.algorithm,
        "status": row.status,
        "outcome_definition_id": row.outcome_definition_id,
        "entry_model": row.entry_model,
        "feature_schema_version": row.feature_schema_version,
        "calculation_version": row.calculation_version,
        "config_hash": row.config_hash,
        "training_window_start": _iso(row.training_window_start),
        "training_cutoff_at": _iso(row.training_cutoff_at),
        "metrics": row.metrics_json or {},
        "artifact_format": row.artifact_format,
        "artifact_hash": row.artifact_hash,
        "artifact_size_bytes": row.artifact_size_bytes,
        "created_at": _iso(row.created_at),
        "activated_at": _iso(row.activated_at),
        "retired_at": _iso(row.retired_at),
    }


def _calibration_bin_payload(row: WinnerCalibrationBin) -> dict[str, Any]:
    return {
        "bin_floor": _number(row.bin_floor),
        "bin_ceiling": _number(row.bin_ceiling),
        "sample_n": row.sample_n,
        "effective_n": _number(row.effective_n),
        "mean_prediction": _number(row.mean_prediction),
        "observed_rate": _number(row.observed_rate),
        "lower_bound": _number(row.lower_bound),
        "upper_bound": _number(row.upper_bound),
        "error": _number(row.error),
        "segment": row.segment_json or {},
        "calculated_at": _iso(row.calculated_at),
    }


def _drift_payload(row: WinnerDriftMetric) -> dict[str, Any]:
    return {
        "as_of_date": _iso(row.as_of_date),
        "metric_name": row.metric_name,
        "metric_value": _number(row.metric_value),
        "threshold_value": _number(row.threshold_value),
        "breached": row.breached,
        "sample_n": row.sample_n,
        "comparison_window": row.comparison_window,
        "segment": row.segment_json or {},
        "sufficient_sample": row.sufficient_sample,
        "calculated_at": _iso(row.calculated_at),
    }


def _first_non_null(rows: list[dict[str, Any]], key: str) -> Any:
    return next((row.get(key) for row in rows if row.get(key) is not None), None)


def _cutoff_from_query(query: WinnerProbabilityApiQuery) -> datetime | None:
    if query.as_of_date is None:
        return None
    return _end_of_day(query.as_of_date)


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=UTC)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _min(value: Any, minimum: float | int | None) -> bool:
    return minimum is None or (value is not None and value >= minimum)


def _max(value: Any, maximum: float | int | None) -> bool:
    return maximum is None or (value is not None and value <= maximum)


def _eq(value: Any, expected: str | None) -> bool:
    return expected is None or value == expected


def _sort_value(value: Any, reverse: bool) -> Any:
    if value is None:
        return Decimal("-Infinity") if reverse else Decimal("Infinity")
    if isinstance(value, str):
        return value
    return -Decimal(str(value)) if reverse else Decimal(str(value))


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
