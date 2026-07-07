import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.models.enums import RunStatus
from app.models.tables import FundamentalScore, RawCompanyRow, UploadRun
from app.services.column_mapper import MappedCsvRow, map_csv_rows
from app.services.csv_loader import CsvLoadError, load_csv_rows
from app.services.earnings_date_parser import parse_earnings_date
from app.services.fundamental_ranker_v2 import (
    FundamentalScoreV2Result,
    score_rows_v2,
    to_decimal,
)
from app.services.validation_service import CsvValidationError, validate_mapped_rows
from app.settings import get_settings


class UploadProcessingError(ValueError):
    pass


def create_upload_run(db: Session, upload_file: UploadFile) -> UploadRun:
    filename = upload_file.filename or "upload.csv"
    if not filename.lower().endswith(".csv"):
        raise UploadProcessingError("Please upload a .csv file.")

    settings = get_settings()
    _validate_upload_size(upload_file, settings.max_upload_size_mb)
    file_path = _save_upload(upload_file, settings.upload_dir, filename)

    run = UploadRun(
        filename=filename,
        file_path=str(file_path),
        status=RunStatus.VALIDATING.value,
    )
    db.add(run)
    db.flush()

    try:
        csv_rows = load_csv_rows(file_path)
        mapped_rows = map_csv_rows(csv_rows)
        validate_mapped_rows(mapped_rows)
    except (CsvLoadError, CsvValidationError) as exc:
        run.status = RunStatus.FAILED.value
        run.row_count = 0
        run.error_message = str(exc)
        db.commit()
        db.refresh(run)
        return run

    raw_rows = [
        _raw_company_row_from_mapped(run.id, row)
        for row in mapped_rows
        if row.ticker
    ]
    fundamental_scores = [
        _fundamental_score_from_v2(run.id, score) for score in score_rows_v2(mapped_rows)
    ]

    db.add_all(raw_rows)
    db.add_all(fundamental_scores)
    run.row_count = len(raw_rows)
    run.processed_at = datetime.now(UTC)
    run.status = RunStatus.COMPLETED.value
    run.notes = (
        "CSV uploaded, raw rows stored, and fundamental scores calculated "
        "with fundamentals_v2.0."
    )
    db.commit()
    db.refresh(run)
    return run


def _raw_company_row_from_mapped(run_id: int, row: MappedCsvRow) -> RawCompanyRow:
    raw_json = dict(row.raw)
    raw_earnings_value = row.canonical.get("upcoming_earnings_date")
    if raw_earnings_value is not None:
        raw_json["upcoming_earnings_date"] = raw_earnings_value

    return RawCompanyRow(
        run_id=run_id,
        row_number=row.row_number,
        ticker=row.ticker,
        company_name=row.company_name,
        sector=row.sector,
        upcoming_earnings_date=parse_earnings_date(raw_earnings_value),
        raw_json=raw_json,
    )


def _fundamental_score_from_v2(
    run_id: int,
    score: FundamentalScoreV2Result,
) -> FundamentalScore:
    return FundamentalScore(
        run_id=run_id,
        ticker=score.ticker,
        growth_score=to_decimal(score.growth_quality_score),
        profitability_score=to_decimal(score.profitability_quality_score),
        fcf_score=to_decimal(score.fcf_quality_score),
        balance_sheet_score=to_decimal(score.balance_sheet_quality_score),
        valuation_score=to_decimal(score.valuation_quality_score),
        momentum_score=None,
        dilution_score=to_decimal(score.shareholder_quality_score),
        risk_score=to_decimal(score.liquidity_risk_score),
        growth_quality_score=to_decimal(score.growth_quality_score),
        profitability_quality_score=to_decimal(score.profitability_quality_score),
        fcf_quality_score=to_decimal(score.fcf_quality_score),
        earnings_quality_score=to_decimal(score.earnings_quality_score),
        capital_efficiency_score=to_decimal(score.capital_efficiency_score),
        balance_sheet_quality_score=to_decimal(score.balance_sheet_quality_score),
        valuation_quality_score=to_decimal(score.valuation_quality_score),
        forward_quality_score=to_decimal(score.forward_quality_score),
        shareholder_quality_score=to_decimal(score.shareholder_quality_score),
        liquidity_risk_score=to_decimal(score.liquidity_risk_score),
        data_coverage_score=to_decimal(score.data_coverage_score),
        scoring_model_version=score.debug.get("model_version", "fundamentals_v2.0"),
        v2_warning_flags_json={"flags": score.warning_flags},
        missing_data_penalty=to_decimal(score.missing_data_penalty),
        fundamental_score=to_decimal(score.fundamental_score),
        fundamental_label=score.fundamental_label,
        trap_flags_json={"flags": score.warning_flags},
        explanation=score.explanation,
        debug_json=score.debug,
    )


def _validate_upload_size(upload_file: UploadFile, max_size_mb: int) -> None:
    max_bytes = max_size_mb * 1024 * 1024
    upload_file.file.seek(0, 2)
    size = upload_file.file.tell()
    upload_file.file.seek(0)
    if size > max_bytes:
        raise UploadProcessingError(
            f"{upload_file.filename or 'Upload'} is too large. "
            f"Maximum upload size is {max_size_mb} MB."
        )


def _save_upload(upload_file: UploadFile, upload_dir: Path, filename: str) -> Path:
    safe_name = _safe_filename(filename)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    destination = upload_dir / f"{timestamp}_{uuid4().hex[:8]}_{safe_name}"

    with destination.open("wb") as handle:
        shutil.copyfileobj(upload_file.file, handle)

    return destination


def _safe_filename(filename: str) -> str:
    keep = []
    for char in Path(filename).name:
        keep.append(char if char.isalnum() or char in (" ", ".", "-", "_") else "_")
    safe_name = "".join(keep).strip()
    return safe_name or "upload.csv"
