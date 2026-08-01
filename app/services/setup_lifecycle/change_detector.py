from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.tables import SetupSignalSnapshot, SignalChangeEvent
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import EventSeverity, SignalCategory, SignalValueType
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.signal_registry import SignalDefinition

QUALITY_ORDER = {
    "INSUFFICIENT": 0,
    "LOW": 1,
    "NORMAL": 2,
    "HIGH": 3,
}


@dataclass(frozen=True)
class DetectedSignalChange:
    signal_key: str
    category: SignalCategory
    value_type: SignalValueType
    old_value: Any
    new_value: Any
    delta_numeric: Decimal | None
    percentage_delta: Decimal | None
    rank_delta: int | None
    normalized_delta: Decimal | None
    direction: str
    threshold_name: str | None
    threshold_direction: str | None
    severity: EventSeverity
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalChangeDetectionResult:
    created_events: int = 0
    suppressed: int = 0
    skipped: int = 0
    changes: tuple[DetectedSignalChange, ...] = ()
    event_ids: tuple[int | None, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "change_events": self.created_events,
            "changed": self.created_events,
            "suppressed": self.suppressed,
            "skipped": self.skipped,
        }


class SetupLifecycleChangeDetector:
    def __init__(
        self,
        *,
        repository: SetupLifecycleRepository | None = None,
        config: SetupLifecycleConfig | None = None,
    ) -> None:
        self.repository = repository or SetupLifecycleRepository()
        self.config = config or load_setup_lifecycle_config()

    def detect_changes(
        self,
        *,
        previous: SetupSignalSnapshot | None,
        current: SetupSignalSnapshot,
        history: tuple[SetupSignalSnapshot, ...] = (),
    ) -> tuple[DetectedSignalChange, ...]:
        if previous is None:
            return ()

        changes: list[DetectedSignalChange] = []
        for definition in self.config.signal_registry.definitions():
            old_value = _signal_value(previous, definition)
            new_value = _signal_value(current, definition)
            change = self._detect_one(definition, old_value, new_value, current, history)
            if change is not None:
                changes.append(change)
        return tuple(changes)

    def detect_and_persist(
        self,
        db,
        *,
        evaluation_run_id: int | None,
        snapshot_ids: tuple[int, ...],
    ) -> SignalChangeDetectionResult:
        current_snapshots = self.repository.get_snapshots_by_ids(db, snapshot_ids)
        event_ids: list[int | None] = []
        changes: list[DetectedSignalChange] = []
        skipped = 0

        for current in current_snapshots:
            previous = self.repository.previous_canonical_snapshot(
                db,
                ticker=current.ticker,
                timeframe=current.timeframe,
                before_date=current.data_as_of_date,
            )
            if previous is None:
                skipped += 1
                continue
            history = tuple(
                self.repository.canonical_snapshot_history(
                    db,
                    ticker=current.ticker,
                    timeframe=current.timeframe,
                    before_date=current.data_as_of_date,
                    limit=10,
                )
            )
            for change in self.detect_changes(
                previous=previous,
                current=current,
                history=history,
            ):
                event = self._to_event(
                    change,
                    previous=previous,
                    current=current,
                    evaluation_run_id=evaluation_run_id,
                )
                existing = self.repository.get_signal_change_event(db, event.source_event_key)
                if existing is not None:
                    continue
                persisted = self.repository.add_signal_change_event(db, event)
                event_ids.append(getattr(persisted, "id", None))
                changes.append(change)

        return SignalChangeDetectionResult(
            created_events=len(changes),
            skipped=skipped,
            changes=tuple(changes),
            event_ids=tuple(event_ids),
        )

    def _detect_one(
        self,
        definition: SignalDefinition,
        old_value: Any,
        new_value: Any,
        current: SetupSignalSnapshot,
        history: tuple[SetupSignalSnapshot, ...],
    ) -> DetectedSignalChange | None:
        if _same_value(old_value, new_value, definition.value_type):
            return None

        reason_codes: list[str] = []
        if old_value is None and new_value is not None:
            reason_codes.append("MISSING_TO_PRESENT")
        elif old_value is not None and new_value is None:
            reason_codes.append("PRESENT_TO_MISSING")
        elif definition.value_type is SignalValueType.NULLABILITY:
            reason_codes.append("NULLABILITY_CHANGED")

        delta_numeric = _numeric_delta(old_value, new_value, definition)
        percentage_delta = _percentage_delta(old_value, new_value)
        rank_delta = _rank_delta(old_value, new_value, definition)
        normalized_delta = _decimal_or_none(definition.normalized_delta(old_value, new_value))
        threshold_name, threshold_direction = _threshold_crossing(definition, old_value, new_value)
        if threshold_name is not None:
            reason_codes.append("THRESHOLD_CROSSED")

        if _data_quality_changed(definition, old_value, new_value):
            reason_codes.append(_data_quality_reason(old_value, new_value))

        if not self._is_material(
            definition,
            old_value,
            new_value,
            delta_numeric,
            percentage_delta,
            rank_delta,
            threshold_name,
            reason_codes,
        ):
            return None

        reason_codes.extend(_type_reason_codes(definition.value_type))
        velocity = velocity_by_window(definition, current, history)
        if velocity:
            reason_codes.append("VELOCITY_COMPUTED")

        return DetectedSignalChange(
            signal_key=definition.key,
            category=definition.category,
            value_type=definition.value_type,
            old_value=old_value,
            new_value=new_value,
            delta_numeric=delta_numeric,
            percentage_delta=percentage_delta,
            rank_delta=rank_delta,
            normalized_delta=normalized_delta,
            direction=definition.direction,
            threshold_name=threshold_name,
            threshold_direction=threshold_direction,
            severity=_severity(definition, old_value, new_value, normalized_delta, reason_codes),
            reason_codes=tuple(dict.fromkeys(reason_codes)),
            evidence={
                "velocity": velocity,
                "missing_policy": definition.missing_value_policy,
                "material_on_change": definition.material_on_change,
            },
        )

    def _is_material(
        self,
        definition: SignalDefinition,
        old_value: Any,
        new_value: Any,
        delta_numeric: Decimal | None,
        percentage_delta: Decimal | None,
        rank_delta: int | None,
        threshold_name: str | None,
        reason_codes: list[str],
    ) -> bool:
        if threshold_name is not None:
            return True
        if definition.value_type is SignalValueType.NULLABILITY:
            return old_value is None or new_value is None
        if {"MISSING_TO_PRESENT", "PRESENT_TO_MISSING"}.intersection(reason_codes):
            return definition.missing_value_policy != "ignore"
        if "STALE_TO_FRESH_DATA_QUALITY" in reason_codes:
            return True
        if definition.value_type in {
            SignalValueType.BOOLEAN,
            SignalValueType.ENUM,
            SignalValueType.SET,
        }:
            return definition.material_on_change
        if definition.value_type is SignalValueType.DATE:
            if definition.material_on_change:
                return True
            return delta_numeric is not None and abs(delta_numeric) >= Decimal(
                str(definition.absolute_change or 1)
            )
        if definition.value_type is SignalValueType.INTEGER_RANK:
            threshold = definition.rank_change or definition.absolute_change
            return threshold is not None and rank_delta is not None and abs(rank_delta) >= threshold
        if delta_numeric is not None and definition.absolute_change is not None:
            if abs(delta_numeric) >= Decimal(str(definition.absolute_change)):
                return True
        if percentage_delta is not None and definition.percentage_change is not None:
            if abs(percentage_delta) >= Decimal(str(definition.percentage_change)):
                return True
        return False

    def _to_event(
        self,
        change: DetectedSignalChange,
        *,
        previous: SetupSignalSnapshot,
        current: SetupSignalSnapshot,
        evaluation_run_id: int | None,
    ) -> SignalChangeEvent:
        source_event_key = self.repository.signal_change_key(
            ticker=current.ticker,
            timeframe=current.timeframe,
            signal_key=change.signal_key,
            effective_date=current.data_as_of_date,
            old_value=_json_value(change.old_value),
            new_value=_json_value(change.new_value),
            config_hash=current.config_hash,
        )
        return SignalChangeEvent(
            evaluation_run_id=evaluation_run_id,
            episode_id=None,
            previous_snapshot_id=previous.id,
            current_snapshot_id=current.id,
            ticker=current.ticker,
            timeframe=current.timeframe,
            effective_date=current.data_as_of_date,
            category=change.category.value,
            signal_key=change.signal_key,
            value_type=change.value_type.value,
            old_value_json={"value": _json_value(change.old_value)},
            new_value_json={"value": _json_value(change.new_value)},
            delta_numeric=change.delta_numeric,
            percentage_delta=change.percentage_delta,
            rank_delta=change.rank_delta,
            normalized_delta=change.normalized_delta,
            direction=change.direction,
            threshold_name=change.threshold_name,
            threshold_direction=change.threshold_direction,
            severity=change.severity.value,
            signal_definition_version=self.config.engine.config_version,
            source_event_key=source_event_key,
            config_hash=current.config_hash,
            reason_codes_json=list(change.reason_codes),
            evidence_json=change.evidence,
        )


def velocity_by_window(
    definition: SignalDefinition,
    current: SetupSignalSnapshot,
    history: tuple[SetupSignalSnapshot, ...],
) -> dict[str, dict[str, Any]]:
    if not definition.velocity_windows:
        return {}
    by_date = sorted(history, key=lambda snapshot: snapshot.data_as_of_date, reverse=True)
    current_value = _signal_value(current, definition)
    result: dict[str, dict[str, Any]] = {}
    for window in definition.velocity_windows:
        if len(by_date) < window:
            continue
        prior = by_date[window - 1]
        old_value = _signal_value(prior, definition)
        normalized = definition.normalized_delta(old_value, current_value)
        result[str(window)] = {
            "prior_snapshot_id": prior.id,
            "prior_date": prior.data_as_of_date.isoformat(),
            "old_value": _json_value(old_value),
            "new_value": _json_value(current_value),
            "normalized_delta": _json_value(_decimal_or_none(normalized)),
        }
    return result


def _signal_value(snapshot: SetupSignalSnapshot, definition: SignalDefinition) -> Any:
    raw_signal = (snapshot.signals_json or {}).get(definition.key)
    if isinstance(raw_signal, dict) and "value" in raw_signal:
        return _coerce_value(raw_signal.get("value"), definition.value_type)
    return _coerce_value(getattr(snapshot, definition.source, None), definition.value_type)


def _coerce_value(value: Any, value_type: SignalValueType) -> Any:
    if value is None:
        return None
    if value_type in {SignalValueType.FLOAT, SignalValueType.PERCENTAGE}:
        return _decimal_or_none(value)
    if value_type is SignalValueType.INTEGER_RANK:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if value_type is SignalValueType.BOOLEAN:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().casefold()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        return None
    if value_type is SignalValueType.SET:
        if isinstance(value, str):
            return frozenset(item.strip() for item in value.split(",") if item.strip())
        try:
            return frozenset(str(item) for item in value)
        except TypeError:
            return frozenset({str(value)})
    if value_type is SignalValueType.DATE:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
    if value_type is SignalValueType.NULLABILITY:
        return value is not None
    return str(value)


def _same_value(old_value: Any, new_value: Any, value_type: SignalValueType) -> bool:
    if value_type is SignalValueType.SET:
        return set(old_value or ()) == set(new_value or ())
    return old_value == new_value


def _numeric_delta(
    old_value: Any,
    new_value: Any,
    definition: SignalDefinition,
) -> Decimal | None:
    if definition.value_type is SignalValueType.DATE:
        if isinstance(old_value, date) and isinstance(new_value, date):
            return Decimal((new_value - old_value).days)
        return None
    old_number = _decimal_or_none(old_value)
    new_number = _decimal_or_none(new_value)
    if old_number is None or new_number is None:
        return None
    return new_number - old_number


def _percentage_delta(old_value: Any, new_value: Any) -> Decimal | None:
    old_number = _decimal_or_none(old_value)
    new_number = _decimal_or_none(new_value)
    if old_number in {None, Decimal("0")} or new_number is None:
        return None
    return (new_number - old_number) / abs(old_number)


def _rank_delta(
    old_value: Any,
    new_value: Any,
    definition: SignalDefinition,
) -> int | None:
    if definition.value_type is not SignalValueType.INTEGER_RANK:
        return None
    if old_value is None or new_value is None:
        return None
    return int(new_value) - int(old_value)


def _threshold_crossing(
    definition: SignalDefinition,
    old_value: Any,
    new_value: Any,
) -> tuple[str | None, str | None]:
    old_number = _decimal_or_none(old_value)
    new_number = _decimal_or_none(new_value)
    if old_number is None or new_number is None:
        return None, None
    for crossing in definition.crossings:
        threshold = Decimal(str(crossing))
        if old_number < threshold <= new_number:
            return f"crossing_{crossing:g}", "ENTER"
        if old_number >= threshold > new_number:
            return f"crossing_{crossing:g}", "EXIT"
    return None, None


def _data_quality_changed(
    definition: SignalDefinition,
    old_value: Any,
    new_value: Any,
) -> bool:
    return definition.key == "data_quality" and old_value != new_value


def _data_quality_reason(old_value: Any, new_value: Any) -> str:
    old_rank = QUALITY_ORDER.get(str(old_value).upper(), -1)
    new_rank = QUALITY_ORDER.get(str(new_value).upper(), -1)
    if new_rank > old_rank:
        return "STALE_TO_FRESH_DATA_QUALITY"
    if new_rank < old_rank:
        return "DATA_QUALITY_DEGRADED"
    return "DATA_QUALITY_CHANGED"


def _type_reason_codes(value_type: SignalValueType) -> tuple[str, ...]:
    return (f"{value_type.name}_CHANGE",)


def _severity(
    definition: SignalDefinition,
    old_value: Any,
    new_value: Any,
    normalized_delta: Decimal | None,
    reason_codes: list[str],
) -> EventSeverity:
    if "DATA_QUALITY_DEGRADED" in reason_codes:
        return EventSeverity.RISK
    if definition.category is SignalCategory.RISK and (
        normalized_delta is None or normalized_delta < 0
    ):
        return EventSeverity.RISK
    if definition.is_close_authoritative_trigger and old_value is False and new_value is True:
        return EventSeverity.ACTIONABLE
    if "THRESHOLD_CROSSED" in reason_codes:
        return EventSeverity.NOTABLE
    if {"MISSING_TO_PRESENT", "PRESENT_TO_MISSING"}.intersection(reason_codes):
        return EventSeverity.INFO
    return EventSeverity.NOTABLE


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, frozenset):
        return sorted(value)
    return value
