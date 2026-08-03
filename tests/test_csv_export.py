from __future__ import annotations

import csv
from io import StringIO

from app.services.csv_export import sanitize_csv_cell, write_csv


def test_sanitize_csv_cell_prefixes_formula_values_after_whitespace() -> None:
    assert sanitize_csv_cell("=cmd") == "'=cmd"
    assert sanitize_csv_cell("  @SUM(A1:A2)") == "'  @SUM(A1:A2)"
    assert sanitize_csv_cell("+1") == "'+1"
    assert sanitize_csv_cell("-1") == "'-1"
    assert sanitize_csv_cell("plain") == "plain"


def test_write_csv_adds_schema_metadata_to_each_row() -> None:
    csv_text = write_csv(
        ["ticker", "notes"],
        [{"ticker": "MSFT", "notes": "=research hint"}],
        schema_id="swinglens.test.v1",
        metadata={"guidance_type": "research_hint", "execution_instruction": False},
    )

    row = next(csv.DictReader(StringIO(csv_text)))

    assert csv_text.startswith("ticker,notes,export_schema_id,export_schema_version")
    assert row["notes"] == "'=research hint"
    assert row["export_schema_id"] == "swinglens.test.v1"
    assert row["export_schema_version"] == "1"
    assert row["guidance_type"] == "research_hint"
    assert row["execution_instruction"] == "False"
