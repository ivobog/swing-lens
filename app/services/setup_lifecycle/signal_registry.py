from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.setup_lifecycle.enums import SignalCategory, SignalValueType

SUPPORTED_SIGNAL_DIRECTIONS = frozenset(
    {
        "higher_is_better",
        "lower_is_better",
        "lower_is_better_until_trigger",
        "lower_rank_is_better",
        "true_is_better",
        "false_is_better",
        "classification_order",
        "quality_order",
        "risk_increase",
        "risk_decrease",
        "neutral",
    }
)
SUPPORTED_MISSING_VALUE_POLICIES = frozenset({"ignore", "warn", "block", "insufficient"})
SUPPORTED_TRIGGER_AUTHORITIES = frozenset({"close", "diagnostic_high", None})


class SignalRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class SignalDefinition:
    key: str
    source: str
    value_type: SignalValueType
    category: SignalCategory
    direction: str
    unit: str | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    percentile_change: float | None = None
    rank_change: float | None = None
    crossings: tuple[float, ...] = ()
    velocity_windows: tuple[int, ...] = ()
    material_on_change: bool = False
    missing_value_policy: str = "ignore"
    trigger_authority: str | None = None
    diagnostic_only: bool = False

    def normalized_delta(self, old_value: Any, new_value: Any) -> float | None:
        if self.value_type is SignalValueType.INTEGER_RANK:
            old_number = _number_or_none(old_value)
            new_number = _number_or_none(new_value)
            if old_number is None or new_number is None:
                return None
            return old_number - new_number
        if self.value_type in {SignalValueType.FLOAT, SignalValueType.PERCENTAGE}:
            old_number = _decimal_or_none(old_value)
            new_number = _decimal_or_none(new_value)
            if old_number is None or new_number is None:
                return None
            delta = float(new_number - old_number)
            if self.direction in {
                "lower_is_better",
                "lower_is_better_until_trigger",
                "risk_decrease",
            }:
                return -delta
            if self.direction == "risk_increase":
                return delta
            return delta
        if self.value_type is SignalValueType.BOOLEAN:
            return _boolean_delta(old_value, new_value, self.direction)
        if self.value_type is SignalValueType.DATE:
            if not isinstance(old_value, date) or not isinstance(new_value, date):
                return None
            return float((new_value - old_value).days)
        return None

    @property
    def is_close_authoritative_trigger(self) -> bool:
        return self.trigger_authority == "close" and not self.diagnostic_only

    @property
    def is_diagnostic_high_cross(self) -> bool:
        return self.trigger_authority == "diagnostic_high" and self.diagnostic_only


class SignalDefinitionRegistry:
    def __init__(self, definitions: dict[str, SignalDefinition]) -> None:
        if not definitions:
            raise SignalRegistryError("signal registry must not be empty")
        self._definitions = dict(definitions)

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> SignalDefinitionRegistry:
        if not isinstance(raw, dict) or not raw:
            raise SignalRegistryError("signals must be a non-empty mapping")
        definitions = {
            key: _parse_signal_definition(key, value)
            for key, value in sorted(raw.items(), key=lambda item: item[0])
        }
        _validate_trigger_separation(definitions)
        return cls(definitions)

    def require(self, key: str) -> SignalDefinition:
        try:
            return self._definitions[key]
        except KeyError as exc:
            raise SignalRegistryError(f"unknown signal definition: {key}") from exc

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def definitions(self) -> tuple[SignalDefinition, ...]:
        return tuple(self._definitions[key] for key in self.keys())

    def __contains__(self, key: str) -> bool:
        return key in self._definitions


def _parse_signal_definition(key: str, raw: Any) -> SignalDefinition:
    if not isinstance(raw, dict):
        raise SignalRegistryError(f"signals.{key} must be a mapping")
    source = _required_text(raw, f"signals.{key}.source")
    value_type = _enum_value(raw.get("type"), SignalValueType, f"signals.{key}.type")
    category = _enum_value(raw.get("category"), SignalCategory, f"signals.{key}.category")
    direction = _required_text(raw, f"signals.{key}.direction")
    if direction not in SUPPORTED_SIGNAL_DIRECTIONS:
        raise SignalRegistryError(f"signals.{key}.direction is not supported")
    missing_policy = str(raw.get("missing_value_policy", "ignore")).strip()
    if missing_policy not in SUPPORTED_MISSING_VALUE_POLICIES:
        raise SignalRegistryError(f"signals.{key}.missing_value_policy is not supported")
    trigger_authority = raw.get("trigger_authority")
    if trigger_authority not in SUPPORTED_TRIGGER_AUTHORITIES:
        raise SignalRegistryError(f"signals.{key}.trigger_authority is not supported")
    diagnostic_only = raw.get("diagnostic_only", False)
    if not isinstance(diagnostic_only, bool):
        raise SignalRegistryError(f"signals.{key}.diagnostic_only must be boolean")
    velocity_windows = tuple(
        int(_number(value, f"signals.{key}.velocity_windows"))
        for value in raw.get("velocity_windows", [])
    )
    if any(value <= 0 for value in velocity_windows):
        raise SignalRegistryError(f"signals.{key}.velocity_windows values must be positive")
    if tuple(sorted(set(velocity_windows))) != velocity_windows:
        raise SignalRegistryError(f"signals.{key}.velocity_windows must be unique and ascending")
    crossings = tuple(
        float(_number(value, f"signals.{key}.crossings"))
        for value in raw.get("crossings", [])
    )
    material_on_change = raw.get("material_on_change", False)
    if not isinstance(material_on_change, bool):
        raise SignalRegistryError(f"signals.{key}.material_on_change must be boolean")
    return SignalDefinition(
        key=key,
        source=source,
        value_type=value_type,
        category=category,
        direction=direction,
        unit=_optional_text(raw.get("unit")),
        absolute_change=_optional_number(
            raw.get("absolute_change"),
            f"signals.{key}.absolute_change",
        ),
        percentage_change=_optional_number(
            raw.get("percentage_change"),
            f"signals.{key}.percentage_change",
        ),
        percentile_change=_optional_number(
            raw.get("percentile_change"),
            f"signals.{key}.percentile_change",
        ),
        rank_change=_optional_number(raw.get("rank_change"), f"signals.{key}.rank_change"),
        crossings=crossings,
        velocity_windows=velocity_windows,
        material_on_change=material_on_change,
        missing_value_policy=missing_policy,
        trigger_authority=trigger_authority,
        diagnostic_only=diagnostic_only,
    )


def _validate_trigger_separation(definitions: dict[str, SignalDefinition]) -> None:
    close_triggers = [
        definition
        for definition in definitions.values()
        if definition.is_close_authoritative_trigger
    ]
    high_diagnostics = [
        definition for definition in definitions.values() if definition.is_diagnostic_high_cross
    ]
    if not close_triggers:
        raise SignalRegistryError("at least one close-authoritative trigger signal is required")
    if not high_diagnostics:
        raise SignalRegistryError("at least one diagnostic high-cross signal is required")
    for definition in high_diagnostics:
        if not definition.diagnostic_only:
            raise SignalRegistryError(
                f"{definition.key} must be diagnostic-only when trigger_authority is "
                "diagnostic_high"
            )


def _enum_value(value: Any, enum_type: Any, field_name: str) -> Any:
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise SignalRegistryError(f"{field_name} is not supported") from exc


def _required_text(raw: dict[str, Any], field_name: str) -> str:
    key = field_name.split(".")[-1]
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SignalRegistryError(f"{field_name} is required")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return _number(value, field_name)


def _number(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SignalRegistryError(f"{field_name} must be numeric") from exc


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _boolean_delta(old_value: Any, new_value: Any, direction: str) -> float | None:
    if not isinstance(old_value, bool) or not isinstance(new_value, bool):
        return None
    old_number = 1.0 if old_value else 0.0
    new_number = 1.0 if new_value else 0.0
    delta = new_number - old_number
    if direction == "false_is_better":
        return -delta
    return delta
