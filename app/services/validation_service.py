from app.services.column_mapper import MappedCsvRow


class CsvValidationError(ValueError):
    pass


def validate_mapped_rows(rows: list[MappedCsvRow]) -> None:
    if not rows:
        raise CsvValidationError("CSV file has no data rows.")

    rows_with_tickers = [row for row in rows if row.ticker]
    if not rows_with_tickers:
        raise CsvValidationError("CSV must contain a ticker column with at least one ticker value.")

    seen: dict[str, int] = {}
    for row in rows_with_tickers:
        if row.ticker in seen:
            raise CsvValidationError(
                f"CSV contains duplicate ticker '{row.ticker}' in rows "
                f"{seen[row.ticker]} and {row.row_number}. Duplicate tickers are not supported."
            )
        seen[row.ticker] = row.row_number
