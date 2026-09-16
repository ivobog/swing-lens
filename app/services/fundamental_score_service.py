from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import FundamentalScore, RawCompanyRow
from app.services.calculation_identity import CalculationIdentity
from app.services.column_mapper import MappedCsvRow, map_csv_rows
from app.services.combined_ranking_identity import (
    build_fundamental_score_identity,
    embed_calculation_identity,
)
from app.services.core_calculation_evidence import CoreEvidenceKind, persist_core_evidence
from app.services.core_effective_configuration import (
    CoreEffectiveConfiguration,
    resolve_fundamental_configuration,
)
from app.services.fundamental_ranker_v2 import score_rows_v2
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.upload_service import _fundamental_score_from_v2


def recalculate_run_fundamentals(
    db: Session,
    run_id: int,
    *,
    market_cutoff: MarketCalculationCutoff | None = None,
    pipeline_run_id: int | None = None,
    effective_configuration: CoreEffectiveConfiguration | None = None,
    expected_calculation_identity: CalculationIdentity | None = None,
) -> list[FundamentalScore]:
    raw_rows = list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )
    mapped_rows = _mapped_rows_from_stored_raw(raw_rows)
    effective_configuration = effective_configuration or resolve_fundamental_configuration()
    effective_configuration.require_family("core.fundamental")
    if expected_calculation_identity is not None:
        effective_configuration.require_retry_identity(expected_calculation_identity)
    scores = [
        _fundamental_score_from_v2(run_id, score)
        for score in score_rows_v2(mapped_rows, config=effective_configuration.values)
    ]
    if market_cutoff is not None or pipeline_run_id is not None:
        if market_cutoff is None or pipeline_run_id is None:
            raise ValueError(
                "Fundamental identity production requires market_cutoff and "
                "pipeline_run_id together"
            )
        raw_by_ticker: dict[str, RawCompanyRow] = {}
        for row in raw_rows:
            raw_by_ticker.setdefault(row.ticker.upper(), row)
        for score in scores:
            raw_row = raw_by_ticker.get(score.ticker.upper())
            if raw_row is None:
                raise ValueError(
                    f"Fundamental identity source row is missing for {score.ticker.upper()}"
                )
            identity = build_fundamental_score_identity(
                score,
                raw_row=raw_row,
                market_cutoff=market_cutoff,
                pipeline_run_id=pipeline_run_id,
            )
            identity = effective_configuration.bind(identity)
            score.debug_json = embed_calculation_identity(
                score.debug_json,
                identity,
                policy="FUNDAMENTAL_SCORE_PRODUCER",
            )

    db.execute(delete(FundamentalScore).where(FundamentalScore.run_id == run_id))
    db.add_all(scores)
    db.flush()
    if isinstance(db, Session):
        for score in scores:
            persist_core_evidence(
                db,
                kind=CoreEvidenceKind.FUNDAMENTAL,
                current_row=score,
                effective_configuration=effective_configuration.snapshot
                if market_cutoff is not None
                else None,
            )
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
