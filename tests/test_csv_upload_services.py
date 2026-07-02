from pathlib import Path

import pytest

from app.services.column_mapper import map_csv_rows
from app.services.csv_loader import load_csv_rows
from app.services.validation_service import CsvValidationError, validate_mapped_rows


def test_load_and_map_tradingview_style_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "Symbol,Description,Sector,Price,Market capitalization\n"
        "msft,Microsoft Corporation,Technology,410.50,3050000000000\n"
        "NVDA,NVIDIA Corporation,Electronic technology,150.25,3700000000000\n",
        encoding="utf-8",
    )

    rows = load_csv_rows(csv_path)
    mapped = map_csv_rows(rows)

    assert len(mapped) == 2
    assert mapped[0].ticker == "MSFT"
    assert mapped[0].company_name == "Microsoft Corporation"
    assert mapped[0].sector == "Technology"
    assert mapped[0].canonical["price"] == "410.50"
    assert mapped[0].canonical["market_cap"] == "3050000000000"
    assert mapped[0].raw["Symbol"] == "msft"


def test_validation_rejects_rows_without_tickers() -> None:
    mapped = map_csv_rows([{"Description": "No ticker"}])

    with pytest.raises(CsvValidationError, match="ticker column"):
        validate_mapped_rows(mapped)


def test_duplicate_like_columns_are_mapped_separately() -> None:
    mapped = map_csv_rows(
        [
            {
                "Symbol": "TEST",
                "Enterprise value to revenue ratio, Trailing 12 months": "3.2",
                "Enterprise value to revenue ratio, Trailing 12 months.1": "3.3",
                "Unmapped Column": "still preserved",
            }
        ]
    )

    assert mapped[0].canonical["ev_revenue"] == "3.2"
    assert mapped[0].canonical["ev_revenue_duplicate"] == "3.3"
    assert mapped[0].raw["Unmapped Column"] == "still preserved"
