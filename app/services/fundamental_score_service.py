from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import FundamentalScore, RawCompanyRow
from app.services.column_mapper import MappedCsvRow, map_csv_rows
from app.services.fundamental_ranker_v2 import score_rows_v2
from app.services.upload_service import _fundamental_score_from_v2


def recalculate_run_fundamentals(db: Session, run_id: int) -> list[FundamentalScore]:
    raw_rows = list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )
    mapped_rows = _mapped_rows_from_stored_raw(raw_rows)
    scores = [
        _fundamental_score_from_v2(run_id, score)
        for score in score_rows_v2(mapped_rows)
    ]

    db.execute(delete(FundamentalScore).where(FundamentalScore.run_id == run_id))
    db.add_all(scores)
    db.flush()
    return scores


def _mapped_rows_from_stored_raw(raw_rows: list[RawCompanyRow]) -> list[MappedCsvRow]:
    remapped = map_csv_rows([row.raw_json for row in raw_rows])
    mapped_by_index = dict(enumerate(remapped))
    return [
        MappedCsvRow(
            row_number=row.row_number,
            ticker=row.ticker,
            company_name=row.company_name,
            sector=row.sector,
            canonical=mapped_by_index[index].canonical,
            raw=row.raw_json,
        )
        for index, row in enumerate(raw_rows)
        if row.ticker
    ]
