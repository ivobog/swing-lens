from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.processing_run_service import CeriProcessingRunService


@dataclass(frozen=True)
class CeriBackfillRequest:
    provider: str
    dataset: str
    ticker: str | None = None
    start: date | None = None
    end: date | None = None
    mode: str = "AS_KNOWN"
    actor: str | None = None


@dataclass(frozen=True)
class CeriBackfillResult:
    processing_run_id: int | None
    status: str
    checkpoints: dict[str, Any]
    skipped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "processing_run_id": self.processing_run_id,
            "status": self.status,
            "checkpoints": self.checkpoints,
            "skipped": self.skipped,
        }


class CeriBackfillService:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        processing_runs: CeriProcessingRunService | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self.processing_runs = processing_runs or CeriProcessingRunService()

    def request_key(self, request: CeriBackfillRequest) -> str:
        return ":".join(
            [
                "ceri",
                "backfill",
                request.provider,
                request.dataset,
                request.ticker or "*",
                request.start.isoformat() if request.start else "*",
                request.end.isoformat() if request.end else "*",
                request.mode,
                self.config.engine.config_version,
            ]
        )

    def run(self, db: Session, request: CeriBackfillRequest) -> CeriBackfillResult:
        run, created = self.processing_runs.create_or_get(
            db,
            job_type="CERI_BACKFILL",
            request_key=self.request_key(request),
            scope={
                "provider": request.provider,
                "dataset": request.dataset,
                "ticker": request.ticker,
                "start": request.start.isoformat() if request.start else None,
                "end": request.end.isoformat() if request.end else None,
                "mode": request.mode,
            },
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            actor=request.actor,
        )
        if not created and run.status in {"COMPLETED", "RUNNING"}:
            return CeriBackfillResult(
                processing_run_id=run.id,
                status=run.status,
                checkpoints=run.checkpoint_json or {},
                skipped=1,
            )
        checkpoint = {
            "provider_page": (run.checkpoint_json or {}).get("provider_page", 0),
            "ticker": request.ticker,
            "mode": request.mode,
        }
        self.processing_runs.finish(
            db,
            run,
            status="COMPLETED",
            counts={"read": 0, "features": 0, "warnings": 0, "failed": 0},
            checkpoint=checkpoint,
        )
        return CeriBackfillResult(
            processing_run_id=run.id,
            status=run.status,
            checkpoints=checkpoint,
        )
