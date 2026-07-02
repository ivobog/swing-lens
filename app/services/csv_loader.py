import csv
from pathlib import Path
from typing import Any


class CsvLoadError(ValueError):
    pass


def load_csv_rows(file_path: Path) -> list[dict[str, Any]]:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            with file_path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    raise CsvLoadError("CSV file is empty or has no header row.")
                return [dict(row) for row in reader]
        except UnicodeDecodeError:
            continue

    raise CsvLoadError("CSV encoding is not supported.")
