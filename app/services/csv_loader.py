import csv
from pathlib import Path
from typing import Any


class CsvLoadError(ValueError):
    pass


def load_csv_rows(file_path: Path) -> list[dict[str, Any]]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            with file_path.open("r", encoding=encoding, newline="") as handle:
                return _load_comma_csv(handle)
        except UnicodeDecodeError:
            continue
        except csv.Error as exc:
            raise CsvLoadError(f"CSV is malformed: {exc}") from exc

    raise CsvLoadError("CSV encoding is not supported.")


def _load_comma_csv(handle: Any) -> list[dict[str, Any]]:
    reader = csv.reader(handle, delimiter=",", strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise CsvLoadError("CSV file is empty or has no header row.") from exc

    _validate_header(header)
    rows: list[dict[str, Any]] = []
    expected_width = len(header)
    for row_number, values in enumerate(reader, start=2):
        if not values or all(value.strip() == "" for value in values):
            raise CsvLoadError(f"CSV row {row_number} is blank. Remove blank rows and try again.")
        if len(values) != expected_width:
            raise CsvLoadError(
                f"CSV row {row_number} has {len(values)} fields; expected {expected_width}."
            )
        rows.append(dict(zip(header, values, strict=True)))
    return rows


def _validate_header(header: list[str]) -> None:
    if not header or all(field.strip() == "" for field in header):
        raise CsvLoadError("CSV file is empty or has no header row.")
    if len(header) == 1 and any(delimiter in header[0] for delimiter in (";", "\t")):
        raise CsvLoadError("Only comma-delimited CSV files are supported.")

    seen: dict[str, str] = {}
    for field in header:
        normalized = field.strip().lower()
        if not normalized:
            raise CsvLoadError("CSV header contains an empty column name.")
        if normalized in seen:
            raise CsvLoadError(
                f"CSV header contains duplicate column '{field.strip()}'. "
                "Duplicate columns are not supported."
            )
        seen[normalized] = field
