from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MappedCsvRow:
    row_number: int
    ticker: str
    company_name: str | None
    sector: str | None
    raw: dict[str, Any]


def load_alias_map(path: Path = Path("config/column_aliases.yaml")) -> dict[str, list[str]]:
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    return {str(key): [str(alias) for alias in aliases] for key, aliases in data.items()}


def find_column(fieldnames: set[str], aliases: list[str]) -> str | None:
    normalized = {field.strip().lower(): field for field in fieldnames}
    for alias in aliases:
        found = normalized.get(alias.strip().lower())
        if found:
            return found
    return None


def map_csv_rows(
    rows: list[dict[str, Any]],
    aliases: dict[str, list[str]] | None = None,
) -> list[MappedCsvRow]:
    aliases = aliases or load_alias_map()
    fieldnames = {field for row in rows for field in row.keys()}

    ticker_column = find_column(fieldnames, aliases.get("ticker", ["Ticker", "Symbol"]))
    company_column = find_column(
        fieldnames,
        aliases.get("company_name", ["Company", "Description"]),
    )
    sector_column = find_column(fieldnames, aliases.get("sector", ["Sector"]))

    mapped_rows: list[MappedCsvRow] = []
    for index, row in enumerate(rows, start=1):
        ticker = _clean_value(row.get(ticker_column)) if ticker_column else ""
        company_name = _clean_value(row.get(company_column)) if company_column else None
        sector = _clean_value(row.get(sector_column)) if sector_column else None
        mapped_rows.append(
            MappedCsvRow(
                row_number=index,
                ticker=ticker.upper(),
                company_name=company_name,
                sector=sector,
                raw=row,
            )
        )

    return mapped_rows


def _clean_value(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
