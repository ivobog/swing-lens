from dataclasses import dataclass
from typing import Any

from app.services.numeric_parser import parse_financial_number

TEXT_FIELDS = {"analyst_rating"}


@dataclass(frozen=True)
class FundamentalCoverageResult:
    available_scoring_fields: int
    total_scoring_fields: int
    coverage_ratio: float
    data_coverage_score: float
    component_coverage: dict[str, float]
    missing_fields_by_priority: dict[str, list[str]]
    missing_data_penalty: float
    parse_diagnostics: dict[str, Any]

    @property
    def missing_core_fields(self) -> list[str]:
        return self.missing_fields_by_priority.get("critical", [])

    @property
    def missing_high_fields(self) -> list[str]:
        return self.missing_fields_by_priority.get("high", [])


def calculate_coverage_v2(
    values: dict[str, Any],
    config: dict[str, Any],
) -> FundamentalCoverageResult:
    component_fields = {
        component: list(component_config["fields"])
        for component, component_config in config["components"].items()
    }
    scoring_fields = sorted({field for fields in component_fields.values() for field in fields})
    available_fields = {field for field in scoring_fields if _field_available(values, field)}
    component_coverage = {
        component: _coverage_ratio(fields, available_fields)
        for component, fields in component_fields.items()
    }
    missing_by_priority = _missing_by_priority(values, config["field_priorities"])
    missing_penalty = _missing_data_penalty(missing_by_priority, config["missing_data"])
    coverage_ratio = _coverage_ratio(scoring_fields, available_fields)

    return FundamentalCoverageResult(
        available_scoring_fields=len(available_fields),
        total_scoring_fields=len(scoring_fields),
        coverage_ratio=coverage_ratio,
        data_coverage_score=round(coverage_ratio * 10, 4),
        component_coverage=component_coverage,
        missing_fields_by_priority=missing_by_priority,
        missing_data_penalty=missing_penalty,
        parse_diagnostics=_parse_diagnostics(values, scoring_fields),
    )


def _coverage_ratio(fields: list[str], available_fields: set[str]) -> float:
    if not fields:
        return 1.0
    return round(len([field for field in fields if field in available_fields]) / len(fields), 4)


def _missing_by_priority(
    values: dict[str, Any],
    priorities: dict[str, list[str]],
) -> dict[str, list[str]]:
    return {
        priority: sorted(field for field in fields if not _field_available(values, field))
        for priority, fields in priorities.items()
    }


def _missing_data_penalty(
    missing_by_priority: dict[str, list[str]],
    config: dict[str, Any],
) -> float:
    penalty = (
        len(missing_by_priority.get("critical", [])) * float(config["critical_field_penalty"])
        + len(missing_by_priority.get("high", [])) * float(config["high_field_penalty"])
        + len(missing_by_priority.get("medium", [])) * float(config["medium_field_penalty"])
        + len(missing_by_priority.get("low", [])) * float(config["low_field_penalty"])
    )
    return round(min(float(config["max_penalty"]), penalty), 4)


def _field_available(values: dict[str, Any], field: str) -> bool:
    if field not in values or values[field] is None:
        return False
    if field in TEXT_FIELDS:
        return str(values[field]).strip() != ""
    return parse_financial_number(values[field]).value is not None


def _parse_diagnostics(values: dict[str, Any], scoring_fields: list[str]) -> dict[str, Any]:
    failed_fields = []
    for field in scoring_fields:
        if field in TEXT_FIELDS or field not in values:
            continue
        result = parse_financial_number(values.get(field))
        if not result.parsed and result.reason != "missing":
            failed_fields.append(
                {
                    "field": field,
                    "raw": values.get(field),
                    "normalized": result.normalized,
                    "reason": result.reason,
                }
            )
    return {
        "failed_fields": failed_fields,
        "failed_field_count": len(failed_fields),
    }
