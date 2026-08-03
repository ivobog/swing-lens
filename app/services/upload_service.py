import shutil
from contextlib import suppress
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
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_taxonomy import normalize_sector_result
from app.services.validation_service import CsvValidationError, validate_mapped_rows
from app.settings import get_settings


class UploadProcessingError(ValueError):
    pass


_MAX_SAFE_FILENAME_LENGTH = 180
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def create_upload_run(db: Session, upload_file: UploadFile) -> UploadRun:
    filename = upload_file.filename or "upload.csv"
    if not filename.lower().endswith(".csv"):
        raise UploadProcessingError("Please upload a .csv file.")

    settings = get_settings()
    _validate_upload_size(upload_file, settings.max_upload_size_mb)
    file_path = _save_upload(upload_file, settings.upload_dir, filename)
    committed = False

    try:
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
            committed = True
            db.refresh(run)
            return run

        sector_config = load_sector_rotation_config()
        raw_rows = [
            _raw_company_row_from_mapped(run.id, row, sector_config)
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
        model_version = (
            fundamental_scores[0].scoring_model_version
            if fundamental_scores
            else "fundamentals_v2"
        )
        run.notes = (
            "CSV uploaded, raw rows stored, and fundamental scores calculated "
            f"with {model_version}."
        )
        db.commit()
        committed = True
        db.refresh(run)
        return run
    except Exception:
        if not committed:
            with suppress(Exception):
                db.rollback()
            _remove_upload_file(file_path)
        raise


def _raw_company_row_from_mapped(
    run_id: int,
    row: MappedCsvRow,
    sector_config: dict | None = None,
) -> RawCompanyRow:
    raw_json = dict(row.raw)
    raw_earnings_value = row.canonical.get("upcoming_earnings_date")
    if raw_earnings_value is not None:
        raw_json["upcoming_earnings_date"] = raw_earnings_value
    sector_result = normalize_sector_result(
        row.sector,
        sector_config or load_sector_rotation_config(),
    )

    return RawCompanyRow(
        run_id=run_id,
        row_number=row.row_number,
        ticker=row.ticker,
        company_name=row.company_name,
        sector=sector_result.raw_sector,
        sector_canonical=sector_result.canonical_sector,
        sector_taxonomy=sector_result.taxonomy,
        sector_mapping_status=sector_result.status,
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
        scoring_model_version=score.debug.get("model_version", "fundamentals_v2.1"),
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
    try:
        upload_file.file.seek(0, 2)
        size = upload_file.file.tell()
        upload_file.file.seek(0)
    except OSError as exc:
        raise UploadProcessingError(
            "Upload stream could not be inspected. Please retry with a standard CSV file upload."
        ) from exc
    if size > max_bytes:
        raise UploadProcessingError(
            f"{upload_file.filename or 'Upload'} is too large. "
            f"Maximum upload size is {max_size_mb} MB."
        )


def _save_upload(upload_file: UploadFile, upload_dir: Path, filename: str) -> Path:
    safe_name = _safe_filename(filename)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    destination = upload_dir / f"{timestamp}_{uuid4().hex[:8]}_{safe_name}"

    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            shutil.copyfileobj(upload_file.file, handle)
    except OSError as exc:
        _remove_upload_file(destination)
        raise UploadProcessingError("Upload file could not be saved.") from exc

    return destination


def _safe_filename(filename: str) -> str:
    keep = []
    for char in Path(filename).name:
        keep.append(char if char.isalnum() or char in (" ", ".", "-", "_") else "_")
    safe_name = "".join(keep).strip()
    safe_name = safe_name or "upload.csv"
    safe_name = _avoid_reserved_windows_name(safe_name)
    return _cap_filename_length(safe_name, _MAX_SAFE_FILENAME_LENGTH)


def _avoid_reserved_windows_name(filename: str) -> str:
    path = Path(filename)
    if path.stem.upper() in _WINDOWS_RESERVED_NAMES:
        return f"upload_{filename}"
    return filename


def _cap_filename_length(filename: str, max_length: int) -> str:
    if len(filename) <= max_length:
        return filename
    path = Path(filename)
    suffix = path.suffix if len(path.suffix) < max_length else ""
    stem_limit = max_length - len(suffix)
    return f"{path.stem[:stem_limit].rstrip()}{suffix}"


def _remove_upload_file(file_path: Path) -> None:
    with suppress(OSError):
        file_path.unlink(missing_ok=True)
