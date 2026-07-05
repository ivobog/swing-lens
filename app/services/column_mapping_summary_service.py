from dataclasses import dataclass
from typing import Any

from app.models.tables import RawCompanyRow, UploadRun
from app.services.column_mapper import load_alias_map
from app.services.fundamental_ranker_v2 import load_fundamentals_v2_config


@dataclass(frozen=True)
class ColumnMappingItem:
    raw_header: str
    canonical_field: str | None
    priority: str | None
    component: str | None
    used_in_scoring: bool
    sample_value: str | None


@dataclass(frozen=True)
class ColumnMappingSummary:
    raw_column_count: int
    recognized_count: int
    unrecognized_count: int
    scoring_count: int
    stored_only_count: int
    missing_critical_fields: list[str]
    missing_high_fields: list[str]
    items: list[ColumnMappingItem]
    unrecognized_columns: list[str]


def summarize_run_column_mapping(run: UploadRun) -> ColumnMappingSummary:
    rows = sorted(run.raw_company_rows, key=lambda row: row.row_number)
    return summarize_column_mapping(rows)


def summarize_column_mapping(rows: list[RawCompanyRow]) -> ColumnMappingSummary:
    raw_headers = _raw_headers(rows)
    aliases = load_alias_map()
    config = load_fundamentals_v2_config()
    alias_lookup = _alias_lookup(aliases)
    scoring_fields = _scoring_fields(config)
    component_by_field = _component_by_field(config)
    priority_by_field = _priority_by_field(config)
    present_canonical_fields: set[str] = set()

    items: list[ColumnMappingItem] = []
    for header in raw_headers:
        canonical = alias_lookup.get(_normalize(header))
        if canonical:
            present_canonical_fields.add(canonical)
        used_in_scoring = canonical in scoring_fields if canonical else False
        items.append(
            ColumnMappingItem(
                raw_header=header,
                canonical_field=canonical,
                priority=priority_by_field.get(canonical) if canonical else None,
                component=component_by_field.get(canonical) if canonical else None,
                used_in_scoring=used_in_scoring,
                sample_value=_sample_value(rows, header),
            )
        )

    recognized_count = sum(item.canonical_field is not None for item in items)
    scoring_count = sum(item.used_in_scoring for item in items)
    missing_critical_fields = _missing_priority_fields(
        config,
        priority="critical",
        present=present_canonical_fields,
    )
    missing_high_fields = _missing_priority_fields(
        config,
        priority="high",
        present=present_canonical_fields,
    )

    return ColumnMappingSummary(
        raw_column_count=len(items),
        recognized_count=recognized_count,
        unrecognized_count=len(items) - recognized_count,
        scoring_count=scoring_count,
        stored_only_count=recognized_count - scoring_count,
        missing_critical_fields=missing_critical_fields,
        missing_high_fields=missing_high_fields,
        items=items,
        unrecognized_columns=[item.raw_header for item in items if not item.canonical_field],
    )


def _raw_headers(rows: list[RawCompanyRow]) -> list[str]:
    seen: set[str] = set()
    headers: list[str] = []
    for row in rows:
        for header in row.raw_json:
            if header not in seen:
                seen.add(header)
                headers.append(header)
    return headers


def _alias_lookup(aliases: dict[str, list[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, names in aliases.items():
        lookup[_normalize(canonical)] = canonical
        for name in names:
            lookup[_normalize(name)] = canonical
    return lookup


def _scoring_fields(config: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for component_config in config.get("components", {}).values():
        fields.update(str(field) for field in component_config.get("fields", []))
    return fields


def _component_by_field(config: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for component, component_config in config.get("components", {}).items():
        for field in component_config.get("fields", []):
            mapping.setdefault(str(field), str(component))
    return mapping


def _priority_by_field(config: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for priority, fields in config.get("field_priorities", {}).items():
        for field in fields:
            mapping[str(field)] = str(priority)
    return mapping


def _missing_priority_fields(
    config: dict[str, Any],
    priority: str,
    present: set[str],
) -> list[str]:
    return [
        str(field)
        for field in config.get("field_priorities", {}).get(priority, [])
        if str(field) not in present
    ]


def _sample_value(rows: list[RawCompanyRow], header: str) -> str | None:
    for row in rows:
        value = row.raw_json.get(header)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text[:120]
    return None


def _normalize(value: str) -> str:
    return value.strip().casefold()
