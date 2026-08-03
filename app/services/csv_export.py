import csv
import json
from collections.abc import Iterable
from io import StringIO
from typing import Any

CSV_METADATA_HEADERS = ["export_schema_id", "export_schema_version"]
FORMULA_PREFIXES = ("=", "+", "-", "@")


def write_csv(
    headers: list[str] | tuple[str, ...],
    rows: Iterable[dict[str, Any]],
    *,
    schema_id: str | None = None,
    schema_version: str = "1",
    metadata: dict[str, Any] | None = None,
) -> str:
    fieldnames = list(headers)
    row_metadata = dict(metadata or {})
    if schema_id:
        row_metadata = {
            "export_schema_id": schema_id,
            "export_schema_version": schema_version,
            **row_metadata,
        }
    for key in row_metadata:
        if key not in fieldnames:
            fieldnames.append(key)

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: sanitize_csv_cell(row_metadata.get(key, row.get(key)))
                for key in fieldnames
            }
        )
    return buffer.getvalue()


def sanitize_csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, list | dict):
        value = json.dumps(value, sort_keys=True, default=str)
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value
