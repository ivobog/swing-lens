from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.models.tables import (
    EntryDataStatus,
    EntryScheduleStatus,
    PredictionEligibility,
)
from app.services.us_market_calendar import (
    latest_completed_us_trading_day,
    next_us_trading_day,
)
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.feature_schema import (
    FeatureSchemaError,
    FeatureSchemaRegistry,
)
from app.services.winner_probability.repository import RunCaptureContext, TickerCaptureContext


class WinnerFeatureExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedPredictionFeatures:
    ticker: str
    prediction_as_of_date: date
    source_data_cutoff_at: datetime
    planned_entry_session: date | None
    entry_schedule_status: str
    entry_data_status: str
    eligibility_status: str
    exclusion_reason: str | None
    warnings: tuple[str, ...]
    feature_json: dict[str, Any]
    feature_vector_hash: str
    source_ids_json: dict[str, Any]
    lineage_json: dict[str, Any]


class WinnerFeatureExtractor:
    def extract(
        self,
        run_context: RunCaptureContext,
        ticker_context: TickerCaptureContext,
        config: WinnerProbabilityConfig,
        *,
        captured_at: datetime | None = None,
    ) -> ExtractedPredictionFeatures:
        captured_at = captured_at or datetime.now(UTC)
        raw_row = ticker_context.raw_row
        ticker = _ticker(raw_row.ticker)
        technical = ticker_context.technical_score
        combined = ticker_context.combined_result
        fundamental = ticker_context.fundamental_score
        ranking = ticker_context.ranking_results[0] if ticker_context.ranking_results else None
        market = run_context.market_regime_snapshot
        sector_row = ticker_context.sector_row
        sector_snapshot = run_context.sector_rotation_snapshot
        warnings: list[str] = []

        if _source_after_cutoff(market, captured_at):
            warnings.append("future_market_regime_snapshot_omitted")
            market = None
        if _source_after_cutoff(sector_snapshot, captured_at):
            warnings.append("future_sector_rotation_context_omitted")
            sector_snapshot = None
            sector_row = None

        _validate_point_in_time_sources(
            captured_at,
            run_context.upload_run,
            raw_row,
            fundamental,
            technical,
            combined,
            ranking,
            market,
            sector_snapshot,
        )
        source_cutoff = _source_cutoff_at(
            captured_at,
            run_context.upload_run,
            raw_row,
            fundamental,
            technical,
            combined,
            ranking,
            market,
            sector_snapshot,
        )
        prediction_as_of = latest_completed_us_trading_day(captured_at)
        for context_name, context_date in (
            ("market_regime", getattr(market, "as_of_date", None)),
            ("sector_rotation", getattr(sector_snapshot, "as_of_date", None)),
        ):
            if context_date is not None and context_date > prediction_as_of:
                warnings.append(f"{context_name}_date_after_completed_signal_session")

        exclusion_reason = None
        eligibility = PredictionEligibility.ELIGIBLE
        if technical is None:
            eligibility = PredictionEligibility.EXCLUDED
            exclusion_reason = "missing_technical_score"
        elif getattr(technical, "insufficient_data", False):
            eligibility = PredictionEligibility.EXCLUDED
            exclusion_reason = "insufficient_completed_bars"
        elif combined is None:
            eligibility = PredictionEligibility.EXCLUDED
            exclusion_reason = "missing_combined_result"
        elif combined is not None and not bool(getattr(combined, "is_complete", True)):
            eligibility = PredictionEligibility.EXCLUDED
            exclusion_reason = "incomplete_combined_result"

        if market is None:
            warnings.append("missing_market_regime_snapshot")
        if sector_snapshot is None or sector_row is None:
            warnings.append("missing_sector_rotation_context")
        if ranking is None:
            warnings.append("missing_ranking_result")
        if fundamental is None:
            warnings.append("missing_fundamental_score")

        planned_entry = _planned_entry_session(prediction_as_of)
        entry_schedule_status = (
            EntryScheduleStatus.RESOLVED
            if planned_entry is not None
            else EntryScheduleStatus.UNRESOLVED
        )
        entry_data_status = (
            EntryDataStatus.NOT_DUE
            if planned_entry and planned_entry > latest_completed_us_trading_day(captured_at)
            else EntryDataStatus.PENDING
        )

        feature_json = _canonical_feature_json(
            config=config,
            ticker=ticker,
            raw_row=raw_row,
            fundamental=fundamental,
            technical=technical,
            combined=combined,
            ranking=ranking,
            market=market,
            sector_row=sector_row,
            run_context=run_context,
            prediction_as_of=prediction_as_of,
            planned_entry=planned_entry,
            setup_lifecycle_features=getattr(ticker_context, "setup_lifecycle_features", None),
        )
        feature_cutoff_audit = _feature_cutoff_audit(
            config=config,
            feature_json=feature_json,
            prediction_cutoff_at=captured_at,
            run_context=run_context,
            raw_row=raw_row,
            fundamental=fundamental,
            technical=technical,
            combined=combined,
            ranking=ranking,
            market=market,
            sector_snapshot=sector_snapshot,
        )
        feature_hash = _stable_hash(feature_json)
        return ExtractedPredictionFeatures(
            ticker=ticker,
            prediction_as_of_date=prediction_as_of,
            source_data_cutoff_at=source_cutoff,
            planned_entry_session=planned_entry,
            entry_schedule_status=entry_schedule_status,
            entry_data_status=entry_data_status,
            eligibility_status=eligibility,
            exclusion_reason=exclusion_reason,
            warnings=tuple(warnings),
            feature_json=feature_json,
            feature_vector_hash=feature_hash,
            source_ids_json={
                "upload_run_id": run_context.upload_run.id,
                "raw_row_id": raw_row.id,
                "fundamental_score_id": getattr(fundamental, "id", None),
                "technical_score_id": getattr(technical, "id", None),
                "combined_result_id": getattr(combined, "id", None),
                "ranking_result_id": getattr(ranking, "id", None),
                "market_regime_snapshot_id": getattr(market, "id", None),
                "sector_rotation_snapshot_id": getattr(sector_snapshot, "id", None),
                "sector_rotation_row_id": getattr(sector_row, "id", None),
            },
            lineage_json={
                "capture_phase": "phase_3",
                "point_in_time_validated": True,
                "feature_cutoff_audit": feature_cutoff_audit,
                "feature_cutoff_audit_hash": _stable_hash(feature_cutoff_audit),
                "entry_horizon_convention": config.horizon.counting_convention,
            },
        )


def _canonical_feature_json(
    *,
    config: WinnerProbabilityConfig,
    ticker: str,
    raw_row,
    fundamental,
    technical,
    combined,
    ranking,
    market,
    sector_row,
    run_context: RunCaptureContext,
    prediction_as_of: date,
    planned_entry: date | None,
    setup_lifecycle_features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    features = {
        "calculation_version": config.engine.calculation_version,
        "config_hash": config.config_hash,
        "feature_schema_version": config.feature_schema.version,
        "ticker": ticker,
        "prediction_as_of_date": _normalize(prediction_as_of),
        "planned_entry_session": _normalize(planned_entry),
        "setup_family": _normalize(_setup_family(combined, ranking, technical)),
        "trigger_state": _normalize(getattr(technical, "action_bias", None)),
        "ranking_profile": _normalize(getattr(ranking, "ranking_profile", None)),
        "fundamental_score": _normalize(
            _first_present(
                getattr(fundamental, "fundamental_score", None),
                getattr(combined, "fundamental_score", None),
            )
        ),
        "technical_score": _normalize(getattr(technical, "dual_score", None)),
        "combined_score": _normalize(getattr(combined, "final_score", None)),
        "dual_score_band": _score_band(getattr(technical, "dual_score", None)),
        "score_band": _score_band(getattr(combined, "final_score", None)),
        "market_regime": _normalize(getattr(market, "regime", None)),
        "market_regime_family": _normalize(getattr(market, "regime", None)),
        "market_risk_state": _normalize(getattr(market, "risk_state", None)),
        "sector_state": _normalize(getattr(sector_row, "rotation_state", None)),
        "sector_rank": _normalize(getattr(sector_row, "current_rank", None)),
        "sector_leadership_bucket": _sector_bucket(getattr(sector_row, "current_rank", None)),
        "reward_risk": _normalize(
            _first_present(
                getattr(technical, "reward_risk", None),
                getattr(combined, "reward_risk", None),
            )
        ),
        "earnings_risk": _normalize(
            _first_present(
                getattr(combined, "earnings_risk_level", None),
                getattr(ranking, "earnings_risk_level", None),
            )
        ),
        "technical_data_quality": _normalize(getattr(technical, "technical_confidence", None)),
        "fundamental_coverage": _normalize(getattr(fundamental, "data_coverage_score", None)),
        "universe_provenance": _normalize(getattr(run_context.upload_run, "filename", None)),
        "screener_provenance": _normalize(getattr(run_context.upload_run, "notes", None)),
        "raw_sector": _normalize(getattr(raw_row, "sector", None)),
        "canonical_sector": _normalize(getattr(raw_row, "sector_canonical", None)),
    }
    if setup_lifecycle_features:
        features.update(
            {
                key: _normalize(value)
                for key, value in setup_lifecycle_features.items()
                if key.startswith("setup_lifecycle_")
            }
        )
    return features


def _source_cutoff_at(captured_at: datetime, *rows) -> datetime:
    values = [
        value
        for row in rows
        for value in (
            getattr(row, "created_at", None),
            getattr(row, "updated_at", None),
            getattr(row, "uploaded_at", None),
            getattr(row, "processed_at", None),
        )
        if isinstance(value, datetime)
    ]
    return max(values) if values else captured_at


def _feature_cutoff_audit(
    *,
    config: WinnerProbabilityConfig,
    feature_json: dict[str, Any],
    prediction_cutoff_at: datetime,
    run_context: RunCaptureContext,
    raw_row,
    fundamental,
    technical,
    combined,
    ranking,
    market,
    sector_snapshot,
) -> dict[str, Any]:
    registry = FeatureSchemaRegistry(config.feature_schema.version)
    audit: dict[str, Any] = {}
    for feature_name in config.feature_schema.core_features:
        available_at = _feature_source_available_at(
            feature_name,
            prediction_cutoff_at,
            run_context=run_context,
            raw_row=raw_row,
            fundamental=fundamental,
            technical=technical,
            combined=combined,
            ranking=ranking,
            market=market,
            sector_snapshot=sector_snapshot,
        )
        value = feature_json.get(feature_name)
        if value is None:
            audit[feature_name] = {
                "status": "missing",
                "source_available_at": _normalize(available_at),
            }
            continue
        if available_at is None:
            raise WinnerFeatureExtractionError(
                f"{feature_name} source availability is unavailable"
            )
        try:
            registry.validate_source_available_at(
                feature_name,
                source_available_at=available_at,
                prediction_cutoff_at=prediction_cutoff_at,
            )
        except FeatureSchemaError as exc:
            raise WinnerFeatureExtractionError(str(exc)) from exc
        audit[feature_name] = {
            "status": "available",
            "source_available_at": _normalize(available_at),
        }
    return audit


def _feature_source_available_at(
    feature_name: str,
    fallback: datetime,
    *,
    run_context: RunCaptureContext,
    raw_row,
    fundamental,
    technical,
    combined,
    ranking,
    market,
    sector_snapshot,
) -> datetime | date | None:
    if feature_name in {"universe_provenance", "screener_provenance"}:
        return _row_available_at(run_context.upload_run) or fallback
    if feature_name == "ticker":
        return _row_available_at(raw_row) or _row_available_at(run_context.upload_run)
    if feature_name in {"setup_family", "combined_score", "reward_risk", "earnings_risk"}:
        return _row_available_at(combined)
    if feature_name == "ranking_profile":
        return _row_available_at(ranking)
    if feature_name in {"fundamental_score", "fundamental_coverage"}:
        return _row_available_at(fundamental)
    if feature_name in {"technical_score", "trigger_state", "technical_data_quality"}:
        return _row_available_at(technical)
    if feature_name in {"market_regime", "market_regime_family", "market_risk_state"}:
        return _row_available_at(market)
    if feature_name in {"sector_state", "sector_rank", "sector_leadership_bucket"}:
        return _row_available_at(sector_snapshot)
    if feature_name in {"dual_score_band", "score_band"}:
        return _latest_available_at(
            _row_available_at(fundamental),
            _row_available_at(technical),
            _row_available_at(combined),
        )
    return fallback


def _row_available_at(row) -> datetime | date | None:
    if row is None:
        return None
    datetimes = [
        value
        for value in (
            getattr(row, "created_at", None),
            getattr(row, "updated_at", None),
            getattr(row, "uploaded_at", None),
            getattr(row, "processed_at", None),
        )
        if isinstance(value, datetime)
    ]
    if datetimes:
        return max(datetimes)
    as_of = getattr(row, "as_of_date", None)
    if isinstance(as_of, date):
        return as_of
    return None


def _latest_available_at(*values: datetime | date | None) -> datetime | date | None:
    comparable = [value for value in values if value is not None]
    if not comparable:
        return None
    datetimes = [value for value in comparable if isinstance(value, datetime)]
    return max(datetimes) if datetimes else max(comparable)


def _validate_point_in_time_sources(captured_at: datetime, *rows) -> None:
    capture_day = captured_at.date()
    for row in rows:
        if row is None:
            continue
        row_name = row.__class__.__name__
        as_of = getattr(row, "as_of_date", None)
        if isinstance(as_of, date) and as_of > capture_day:
            raise WinnerFeatureExtractionError(f"{row_name}.as_of_date is after capture date")
        for field_name in ("created_at", "updated_at", "uploaded_at", "processed_at"):
            value = getattr(row, field_name, None)
            if isinstance(value, datetime) and _datetime_after(value, captured_at):
                raise WinnerFeatureExtractionError(f"{row_name}.{field_name} is after capture time")


def _source_after_cutoff(row, cutoff: datetime) -> bool:
    if row is None:
        return False
    as_of = getattr(row, "as_of_date", None)
    if isinstance(as_of, date) and as_of > cutoff.date():
        return True
    for field_name in ("created_at", "updated_at", "uploaded_at", "processed_at"):
        value = getattr(row, field_name, None)
        if isinstance(value, datetime) and _datetime_after(value, cutoff):
            return True
    return False


def _datetime_after(value: datetime, cutoff: datetime) -> bool:
    comparable_value = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    comparable_cutoff = cutoff if cutoff.tzinfo is not None else cutoff.replace(tzinfo=UTC)
    return comparable_value > comparable_cutoff


def _planned_entry_session(prediction_as_of: date) -> date | None:
    try:
        return next_us_trading_day(prediction_as_of)
    except Exception:
        return None


def _stable_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return None
    return value


def _first_present(*values):
    return next((value for value in values if value is not None), None)


def _setup_family(combined, ranking, technical) -> str | None:
    return _first_present(
        getattr(ranking, "decision_label", None),
        getattr(combined, "combined_decision", None),
        getattr(combined, "technical_classification", None),
        getattr(technical, "classification", None),
    )


def _score_band(value: Any) -> str | None:
    if value is None:
        return None
    number = float(value)
    if number >= 8:
        return "8_plus"
    if number >= 6.5:
        return "6_5_to_8"
    if number >= 5:
        return "5_to_6_5"
    return "below_5"


def _sector_bucket(rank: Any) -> str | None:
    if rank is None:
        return None
    value = int(rank)
    if value <= 3:
        return "leader"
    if value <= 7:
        return "middle"
    return "laggard"


def _ticker(value: str) -> str:
    return str(value).strip().upper()
