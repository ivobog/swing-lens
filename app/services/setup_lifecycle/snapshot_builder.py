from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.models.tables import SetupLifecycleEvaluationRun
from app.services.setup_lifecycle.config import (
    SetupLifecycleConfig,
    data_quality_label_for,
    load_setup_lifecycle_config,
)
from app.services.setup_lifecycle.enums import DataQualityLabel, EvaluationStatus, SnapshotOrigin
from app.services.setup_lifecycle.repository import (
    SetupLifecycleRepository,
    SetupSignalSnapshotWrite,
)
from app.services.setup_lifecycle.source_loader import (
    SetupLifecycleSourceLoader,
    TickerSourceContext,
)

REQUIRED_FEATURE_SOURCES = (
    "technical_score",
    "setup_score",
    "classification",
    "close_price",
)
OPTIONAL_CONTEXT_SOURCES = ("market_regime", "sector_rotation")
FRESH_BAR_GRACE_DAYS = 3


@dataclass(frozen=True)
class BuiltSnapshot:
    dto: SetupSignalSnapshotWrite
    warnings: tuple[str, ...]
    required_feature_coverage: float
    freshness_status: str
    source_data_hash: str


@dataclass(frozen=True)
class SnapshotCaptureResult:
    evaluation_run_id: int | None
    status: str
    read: int = 0
    captured: int = 0
    canonical: int = 0
    changed: int = 0
    transitioned: int = 0
    alerted: int = 0
    skipped: int = 0
    warning: int = 0
    failed: int = 0
    low_confidence: int = 0
    snapshot_ids: tuple[int, ...] = ()
    warnings_by_ticker: dict[str, tuple[str, ...]] = field(default_factory=dict)
    errors_by_ticker: dict[str, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "read": self.read,
            "captured": self.captured,
            "canonical": self.canonical,
            "changed": self.changed,
            "transitioned": self.transitioned,
            "alerted": self.alerted,
            "skipped": self.skipped,
            "warning": self.warning,
            "failed": self.failed,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluation_run_id": self.evaluation_run_id,
            "status": self.status,
            "snapshots_captured": self.captured,
            "canonical_snapshots": self.canonical,
            "change_events": self.changed,
            "lifecycle_transitions": self.transitioned,
            "alerts": self.alerted,
            "low_confidence": self.low_confidence,
            "failed": self.failed,
            **self.counts(),
        }


class SetupLifecycleSnapshotBuilder:
    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        self.config = config or load_setup_lifecycle_config()

    def build(self, context: TickerSourceContext) -> BuiltSnapshot:
        ticker = context.ticker
        if not ticker:
            raise ValueError("ticker is required")

        latest_bar = context.latest_completed_bar
        as_of_date = self._resolve_data_as_of_date(context)
        reference_date = self._reference_date(context)
        promoted = self._promoted_fields(context, latest_bar)
        source_values = self._source_values(context, promoted)
        warnings = list(self._warnings(context, as_of_date, reference_date, source_values))
        coverage = self._required_feature_coverage(source_values)
        freshness = self._freshness_status(as_of_date, reference_date, latest_bar is not None)
        context_complete = self._context_complete(context)
        data_quality = data_quality_label_for(
            self.config,
            required_feature_coverage=coverage,
            fresh_completed_bar=freshness == "FRESH",
            context_complete=context_complete,
            near_stale_data=freshness == "NEAR_STALE",
            hard_required_absent=coverage == 0.0,
            stale_beyond_hard_limit=freshness == "STALE",
        )
        source_values["data_quality_label"] = data_quality.value
        promoted["data_quality_label"] = data_quality.value
        promoted["required_feature_coverage"] = Decimal(str(round(coverage, 6)))
        promoted["freshness_status"] = freshness
        promoted["technical_confidence"] = self._technical_confidence(context, coverage, freshness)

        if data_quality in {DataQualityLabel.LOW, DataQualityLabel.INSUFFICIENT}:
            warnings.append(f"DATA_QUALITY_{data_quality.value}")
        if not context_complete:
            warnings.append("MISSING_OPTIONAL_CONTEXT")

        source_ids = self._source_ids(context)
        source_lineage = self._source_lineage(context, source_ids, latest_bar, as_of_date)
        source_data_hash = SetupLifecycleRepository.stable_hash(
            {
                "ticker": ticker,
                "as_of": as_of_date.isoformat(),
                "source_ids": source_ids,
                "signals": source_values,
                "lineage": source_lineage,
            }
        )

        dto = SetupSignalSnapshotWrite(
            ticker=ticker,
            timeframe=self.config.engine.timeframe,
            data_as_of_date=as_of_date,
            calculated_at=_utcnow(),
            origin_type=SnapshotOrigin.LIVE_RUN.value,
            engine_version=self.config.engine.version,
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            source_data_hash=source_data_hash,
            schema_version=self.config.engine.schema_version,
            data_quality_label=data_quality.value,
            run_id=context.raw_row.run_id,
            source_run_id_text=str(context.raw_row.run_id),
            source_ids=source_ids,
            promoted_fields=promoted,
            signals=self._signals_json(source_values),
            feature_flags=self._feature_flags(context),
            warning_flags=sorted(set(warnings)),
            missing_data=self._missing_data(source_values),
            source_lineage=source_lineage,
            diagnostic_high_cross=self._diagnostic_high_cross(context, promoted),
            canonical_decision={"canonical": False, "reason": "pending_phase_4_canonicalization"},
            debug={"builder": "phase_3_snapshot_builder"},
        )
        return BuiltSnapshot(
            dto=dto,
            warnings=tuple(sorted(set(warnings))),
            required_feature_coverage=coverage,
            freshness_status=freshness,
            source_data_hash=source_data_hash,
        )

    def _resolve_data_as_of_date(self, context: TickerSourceContext) -> date:
        latest_bar = context.latest_completed_bar
        if latest_bar is not None:
            return latest_bar.bar_date
        technical = context.technical_score
        if technical is not None and technical.created_at is not None:
            return technical.created_at.date()
        upload_run = context.raw_row.run
        if upload_run is not None:
            timestamp = upload_run.processed_at or upload_run.uploaded_at
            if timestamp is not None:
                return timestamp.date()
        return date.today()

    def _reference_date(self, context: TickerSourceContext) -> date:
        upload_run = context.raw_row.run
        if upload_run is not None:
            timestamp = upload_run.processed_at or upload_run.uploaded_at
            if timestamp is not None:
                return timestamp.date()
        return self._resolve_data_as_of_date(context)

    def _promoted_fields(self, context: TickerSourceContext, latest_bar) -> dict[str, Any]:
        raw = context.raw_row
        fundamental = context.fundamental_score
        technical = context.technical_score
        combined = context.combined_result
        ranking = context.ranking_results[0] if context.ranking_results else None
        market = context.market_regime_snapshot
        sector_row = context.sector_rotation_row

        close_price = _first_value(
            latest_bar.close if latest_bar is not None else None,
            _raw_value(raw, "close_price"),
            _raw_value(raw, "close"),
        )
        high_price = _first_value(
            latest_bar.high if latest_bar is not None else None,
            _raw_value(raw, "high_price"),
            _raw_value(raw, "high"),
        )
        pivot_price = _first_value(
            _raw_value(raw, "pivot_price"),
            _raw_value(raw, "pivot"),
            _raw_value(raw, "pivotPrice"),
        )
        trigger_price = _first_value(
            _raw_value(raw, "trigger_price"),
            _raw_value(raw, "trigger"),
            pivot_price,
        )

        return {
            "company_name": _first_value(raw.company_name, getattr(combined, "company_name", None)),
            "sector": _first_value(
                raw.sector_canonical,
                raw.sector,
                getattr(combined, "sector", None),
            ),
            "fundamental_score": _first_value(
                getattr(fundamental, "fundamental_score", None),
                getattr(combined, "fundamental_score", None),
            ),
            "dual_score": _first_value(
                getattr(technical, "dual_score", None),
                getattr(combined, "dual_score", None),
            ),
            "trend_score": getattr(technical, "trend_score", None),
            "momentum_score": _first_value(
                getattr(technical, "momentum_score", None),
                getattr(fundamental, "momentum_score", None),
            ),
            "setup_score": getattr(technical, "setup_score", None),
            "risk_score": _first_value(
                getattr(technical, "risk_score", None),
                getattr(fundamental, "risk_score", None),
            ),
            "final_score": getattr(combined, "final_score", None),
            "profile_score": getattr(ranking, "profile_score", None),
            "technical_classification": _first_value(
                getattr(technical, "classification", None),
                getattr(combined, "technical_classification", None),
            ),
            "stage": getattr(technical, "stage", None),
            "pullback_health": getattr(technical, "pullback_health", None),
            "action_bias": getattr(technical, "action_bias", None),
            "combined_decision": getattr(combined, "combined_decision", None),
            "ranking_profile": getattr(ranking, "ranking_profile", None),
            "close_price": close_price,
            "pivot_price": pivot_price,
            "trigger_price": trigger_price,
            "stop_price": _first_value(
                _raw_value(raw, "stop_price"),
                getattr(technical, "suggested_stop", None),
            ),
            "target_price": _first_value(
                _raw_value(raw, "target_price"),
                getattr(technical, "suggested_target", None),
            ),
            "distance_to_pivot_pct": _distance_to_pivot_pct(close_price, pivot_price),
            "entry_risk_pct": getattr(technical, "entry_risk_pct", None),
            "reward_risk": getattr(technical, "reward_risk", None),
            "close_above_trigger": _crossed(close_price, trigger_price),
            "high_above_trigger": _crossed(high_price, trigger_price),
            "high_price": high_price,
            "confidence_score": self._confidence_score(context),
            "confidence_label": self._confidence_label(self._confidence_score(context)),
            "primary_setup_family": _primary_setup_family(technical),
            "primary_phase": getattr(technical, "stage", None) or "CANDIDATE",
            "lifecycle_state_candidate": None,
            "actionability_candidate": None,
            "market_regime": getattr(market, "regime", None),
            "sector_rank": getattr(sector_row, "current_rank", None),
        }

    def _source_values(
        self,
        context: TickerSourceContext,
        promoted: dict[str, Any],
    ) -> dict[str, Any]:
        technical = context.technical_score
        fundamental = context.fundamental_score
        combined = context.combined_result
        sector_row = context.sector_rotation_row
        market = context.market_regime_snapshot
        values = {
            "technical_score": promoted.get("dual_score"),
            "setup_score": promoted.get("setup_score"),
            "classification": promoted.get("technical_classification"),
            "stage": promoted.get("stage"),
            "relative_strength": getattr(technical, "relative_strength_score", None),
            "sector_rank": getattr(sector_row, "current_rank", None),
            "market_regime": _first_value(
                getattr(market, "regime", None),
                getattr(technical, "market_regime", None),
            ),
            "earnings_risk": _first_value(
                getattr(combined, "earnings_risk_level", None),
                _raw_value(context.raw_row, "earnings_risk_level"),
            ),
            "liquidity": _liquidity_risk_flag(getattr(fundamental, "liquidity_risk_score", None)),
            "close_trigger_cross": promoted.get("close_above_trigger"),
            "intraday_high_trigger_cross_diagnostic": promoted.get("high_above_trigger"),
            "distance_to_pivot_pct": promoted.get("distance_to_pivot_pct"),
        }
        for definition in self.config.signal_registry.definitions():
            values.setdefault(definition.key, promoted.get(definition.source))
        return values

    def _warnings(
        self,
        context: TickerSourceContext,
        as_of_date: date,
        reference_date: date,
        source_values: dict[str, Any],
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if context.technical_score is None:
            warnings.append("MISSING_TECHNICAL_SCORE")
        if context.fundamental_score is None:
            warnings.append("MISSING_FUNDAMENTAL_SCORE")
        if context.combined_result is None:
            warnings.append("MISSING_COMBINED_RESULT")
        if context.latest_completed_bar is None:
            warnings.append("NO_COMPLETED_DAILY_BAR")
        if context.market_regime_snapshot is None:
            warnings.append("MISSING_MARKET_REGIME")
        if context.sector_rotation_snapshot is None or context.sector_rotation_row is None:
            warnings.append("MISSING_SECTOR_ROTATION")
        for key in REQUIRED_FEATURE_SOURCES:
            if source_values.get(key) is None:
                warnings.append(f"MISSING_REQUIRED_{key.upper()}")
        if as_of_date > reference_date:
            warnings.append("FUTURE_DATED_SNAPSHOT_CONTEXT")
        if _future_dated_context(context, as_of_date):
            warnings.append("FUTURE_DATED_SOURCE_CONTEXT")
        freshness = self._freshness_status(
            as_of_date,
            reference_date,
            context.latest_completed_bar is not None,
        )
        if freshness != "FRESH":
            warnings.append(f"{freshness}_PRICE_BAR")
        return tuple(warnings)

    def _required_feature_coverage(self, values: dict[str, Any]) -> float:
        present = sum(1 for key in REQUIRED_FEATURE_SOURCES if values.get(key) is not None)
        return present / len(REQUIRED_FEATURE_SOURCES)

    def _freshness_status(
        self,
        as_of_date: date,
        reference_date: date,
        has_completed_bar: bool,
    ) -> str:
        if not has_completed_bar:
            return "STALE"
        age_days = (reference_date - as_of_date).days
        if age_days <= FRESH_BAR_GRACE_DAYS:
            return "FRESH"
        if age_days <= FRESH_BAR_GRACE_DAYS * 2:
            return "NEAR_STALE"
        return "STALE"

    def _context_complete(self, context: TickerSourceContext) -> bool:
        return (
            context.market_regime_snapshot is not None
            and context.sector_rotation_row is not None
        )

    def _technical_confidence(
        self,
        context: TickerSourceContext,
        coverage: float,
        freshness: str,
    ) -> str:
        explicit = getattr(context.technical_score, "technical_confidence", None)
        if explicit:
            return str(explicit).upper()
        if coverage >= 1.0 and freshness == "FRESH":
            return "HIGH"
        if coverage >= 0.75:
            return "NORMAL"
        if coverage >= 0.5:
            return "LOW"
        return "INSUFFICIENT"

    def _confidence_score(self, context: TickerSourceContext) -> int:
        raw_score = getattr(context.technical_score, "data_quality_score", None)
        if raw_score is None:
            return 0
        score = float(raw_score)
        return max(0, min(100, int(score * 10 if score <= 10 else score)))

    def _confidence_label(self, score: int) -> str:
        if score >= self.config.confidence.high_min:
            return "HIGH"
        if score >= self.config.confidence.normal_min:
            return "NORMAL"
        if score >= self.config.confidence.low_min:
            return "LOW"
        return "INSUFFICIENT"

    def _source_ids(self, context: TickerSourceContext) -> dict[str, int | None]:
        return {
            "raw_row_id": context.raw_row.id,
            "fundamental_score_id": getattr(context.fundamental_score, "id", None),
            "technical_score_id": getattr(context.technical_score, "id", None),
            "combined_result_id": getattr(context.combined_result, "id", None),
            "ranking_result_id": getattr(context.ranking_results[0], "id", None)
            if context.ranking_results
            else None,
            "market_regime_snapshot_id": getattr(context.market_regime_snapshot, "id", None),
            "sector_rotation_snapshot_id": getattr(context.sector_rotation_snapshot, "id", None),
        }

    def _source_lineage(
        self,
        context: TickerSourceContext,
        source_ids: dict[str, int | None],
        latest_bar,
        as_of_date: date,
    ) -> dict[str, Any]:
        return {
            "source_ids": dict(source_ids),
            "run_id": context.raw_row.run_id,
            "ticker": context.ticker,
            "data_as_of_date": as_of_date.isoformat(),
            "latest_bar": _bar_lineage(latest_bar),
            "ranking_profiles": [row.ranking_profile for row in context.ranking_results],
            "market_regime_as_of": _date_or_none(
                getattr(context.market_regime_snapshot, "as_of_date", None)
            ),
            "sector_rotation_as_of": _date_or_none(
                getattr(context.sector_rotation_snapshot, "as_of_date", None)
            ),
        }

    def _signals_json(self, source_values: dict[str, Any]) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        for definition in self.config.signal_registry.definitions():
            value = source_values.get(definition.key)
            signals[definition.key] = {
                "value": _json_value(value),
                "source": definition.source,
                "type": definition.value_type.value,
                "category": definition.category.value,
                "unit": definition.unit,
                "missing": value is None,
                "diagnostic_only": definition.diagnostic_only,
                "trigger_authority": definition.trigger_authority,
            }
        return signals

    def _feature_flags(self, context: TickerSourceContext) -> dict[str, Any]:
        technical = context.technical_score
        return {
            "technical": list(getattr(technical, "feature_flags_json", None) or []),
            "technical_sub_tags": list(getattr(technical, "sub_tags_json", None) or []),
        }

    def _missing_data(self, source_values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: None
            for key, value in source_values.items()
            if value is None and key in self.config.signal_registry
        }

    def _diagnostic_high_cross(
        self,
        context: TickerSourceContext,
        promoted: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "diagnostic_only": True,
            "high_price": _json_value(promoted.get("high_price")),
            "trigger_price": _json_value(promoted.get("trigger_price")),
            "high_above_trigger": promoted.get("high_above_trigger"),
            "bar_id": getattr(context.latest_completed_bar, "id", None),
        }


class SetupLifecycleSnapshotCaptureService:
    def __init__(
        self,
        *,
        loader: SetupLifecycleSourceLoader | None = None,
        builder: SetupLifecycleSnapshotBuilder | None = None,
        repository: SetupLifecycleRepository | None = None,
        config: SetupLifecycleConfig | None = None,
    ) -> None:
        self.config = config or load_setup_lifecycle_config()
        self.loader = loader or SetupLifecycleSourceLoader()
        self.builder = builder or SetupLifecycleSnapshotBuilder(self.config)
        self.repository = repository or SetupLifecycleRepository()

    def capture_snapshots_for_run(
        self,
        db,
        run_id: int,
        *,
        evaluation_run: SetupLifecycleEvaluationRun | None = None,
        requester: str | None = None,
        finalize_evaluation_run: bool = True,
    ) -> SnapshotCaptureResult:
        try:
            run_context = self.loader.load_run_context(db, run_id)
        except Exception:
            if evaluation_run is not None:
                self.repository.complete_evaluation_run(
                    db,
                    evaluation_run,
                    status=EvaluationStatus.FAILED.value,
                    current_phase="snapshot_load_failed",
                    counts={"read": 0, "failed": 1},
                )
            raise

        if evaluation_run is None:
            evaluation_run = self.repository.create_evaluation_run(
                db,
                mode="LIVE",
                status=EvaluationStatus.RUNNING.value,
                engine_version=self.config.engine.version,
                config_version=self.config.engine.config_version,
                config_hash=self.config.config_hash,
                source_run_id=run_id,
                source_run_id_text=str(run_id),
                requester=requester,
            )

        snapshot_ids: list[int] = []
        warnings_by_ticker: dict[str, tuple[str, ...]] = {}
        errors_by_ticker: dict[str, str] = {}
        low_confidence = 0

        for ticker_context in run_context.tickers:
            try:
                built = self.builder.build(ticker_context)
                dto = replace(built.dto, evaluation_run_id=evaluation_run.id)
                snapshot = self.repository.upsert_snapshot(db, dto)
                snapshot_ids.append(snapshot.id)
                if built.warnings:
                    warnings_by_ticker[ticker_context.ticker] = built.warnings
                if dto.data_quality_label in {
                    DataQualityLabel.LOW.value,
                    DataQualityLabel.INSUFFICIENT.value,
                }:
                    low_confidence += 1
            except Exception as exc:
                errors_by_ticker[ticker_context.ticker] = str(exc)

        status = (
            EvaluationStatus.PARTIAL.value if errors_by_ticker else EvaluationStatus.COMPLETED.value
        )
        result = SnapshotCaptureResult(
            evaluation_run_id=evaluation_run.id,
            status=status,
            read=len(run_context.tickers),
            captured=len(snapshot_ids),
            failed=len(errors_by_ticker),
            warning=sum(len(warnings) for warnings in warnings_by_ticker.values()),
            low_confidence=low_confidence,
            snapshot_ids=tuple(snapshot_ids),
            warnings_by_ticker=warnings_by_ticker,
            errors_by_ticker=errors_by_ticker,
        )
        if finalize_evaluation_run:
            self.repository.complete_evaluation_run(
                db,
                evaluation_run,
                status=status,
                current_phase="snapshot_capture",
                counts=result.counts(),
                errors=dict(errors_by_ticker),
                source_snapshot_min_id=min(snapshot_ids) if snapshot_ids else None,
                source_snapshot_max_id=max(snapshot_ids) if snapshot_ids else None,
            )
        return result


def capture_snapshots_for_run(
    db,
    run_id: int,
    *,
    config: SetupLifecycleConfig | None = None,
    requester: str | None = None,
) -> SnapshotCaptureResult:
    return SetupLifecycleSnapshotCaptureService(config=config).capture_snapshots_for_run(
        db,
        run_id,
        requester=requester,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _first_value(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _raw_value(raw_row, key: str) -> Any:
    raw_json = raw_row.raw_json or {}
    if not isinstance(raw_json, dict):
        return None
    return raw_json.get(key)


def _distance_to_pivot_pct(close_price, pivot_price) -> Decimal | None:
    close_decimal = _decimal_or_none(close_price)
    pivot_decimal = _decimal_or_none(pivot_price)
    if close_decimal is None or pivot_decimal in {None, Decimal("0")}:
        return None
    return ((close_decimal - pivot_decimal) / pivot_decimal * Decimal("100")).quantize(
        Decimal("0.000001")
    )


def _crossed(price, trigger_price) -> bool | None:
    price_decimal = _decimal_or_none(price)
    trigger_decimal = _decimal_or_none(trigger_price)
    if price_decimal is None or trigger_decimal is None:
        return None
    return price_decimal >= trigger_decimal


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _liquidity_risk_flag(value) -> bool | None:
    score = _decimal_or_none(value)
    if score is None:
        return None
    return score < Decimal("5")


def _primary_setup_family(technical) -> str | None:
    classification = str(getattr(technical, "classification", "") or "").upper()
    if "VCP" in classification:
        return "VCP"
    if "PULLBACK" in classification:
        return "PULLBACK"
    if "CONTINUATION" in classification or "FLAG" in classification:
        return "CONTINUATION"
    if "BREAKOUT" in classification or "BASE" in classification:
        return "BREAKOUT"
    if classification:
        return "GENERIC"
    return None


def _future_dated_context(context: TickerSourceContext, as_of_date: date) -> bool:
    related_dates = [
        getattr(context.market_regime_snapshot, "as_of_date", None),
        getattr(context.sector_rotation_snapshot, "as_of_date", None),
    ]
    return any(item is not None and item > as_of_date for item in related_dates)


def _bar_lineage(bar) -> dict[str, Any] | None:
    if bar is None:
        return None
    return {
        "id": bar.id,
        "bar_date": bar.bar_date.isoformat(),
        "what_to_show": bar.what_to_show,
        "timeframe": bar.timeframe,
        "data_hash": bar.data_hash,
    }


def _date_or_none(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value
