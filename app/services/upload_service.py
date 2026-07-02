import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.models.enums import RunStatus
from app.models.tables import RawCompanyRow, UploadRun
from app.services.column_mapper import map_csv_rows
from app.services.csv_loader import CsvLoadError, load_csv_rows
from app.services.validation_service import CsvValidationError, validate_mapped_rows
from app.settings import get_settings


class UploadProcessingError(ValueError):
    pass


def create_upload_run(db: Session, upload_file: UploadFile) -> UploadRun:
    filename = upload_file.filename or "upload.csv"
    if not filename.lower().endswith(".csv"):
        raise UploadProcessingError("Please upload a .csv file.")

    settings = get_settings()
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
        RawCompanyRow(
            run_id=run.id,
            row_number=row.row_number,
            ticker=row.ticker,
            company_name=row.company_name,
            sector=row.sector,
            raw_json=row.raw,
        )
        for row in mapped_rows
        if row.ticker
    ]

    db.add_all(raw_rows)
    run.row_count = len(raw_rows)
    run.processed_at = datetime.now(UTC)
    run.status = RunStatus.COMPLETED.value
    run.notes = "CSV uploaded and raw rows stored. Scoring has not run yet."
    db.commit()
    db.refresh(run)
    return run


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
